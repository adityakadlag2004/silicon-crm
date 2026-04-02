"""Pre-deploy production readiness checks for security settings and migrations.

Usage:
    python manage.py prod_readiness_check
    python manage.py prod_readiness_check --fail-on-warning
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Validate production readiness settings and pending migrations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-warning",
            action="store_true",
            help="Exit with non-zero status if warnings are found.",
        )

    def handle(self, *args, **options):
        checks = self._run_settings_checks()
        migration_ok, migration_msg = self._check_migrations()

        failures = [c for c in checks if not c["ok"]]
        warnings = [c for c in checks if c["warning"]]

        self.stdout.write("Production Readiness Report")
        self.stdout.write("-" * 40)

        for check in checks:
            status = "PASS" if check["ok"] else ("WARN" if check["warning"] else "FAIL")
            line = f"[{status}] {check['name']}: {check['message']}"
            if status == "PASS":
                self.stdout.write(self.style.SUCCESS(line))
            elif status == "WARN":
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(self.style.ERROR(line))

        mig_status = "PASS" if migration_ok else "FAIL"
        mig_line = f"[{mig_status}] Migrations: {migration_msg}"
        if migration_ok:
            self.stdout.write(self.style.SUCCESS(mig_line))
        else:
            self.stdout.write(self.style.ERROR(mig_line))

        self.stdout.write("-" * 40)

        if not migration_ok or failures:
            raise CommandError("Production readiness check failed.")

        if options["fail_on_warning"] and warnings:
            raise CommandError("Production readiness check has warnings.")

        if warnings:
            self.stdout.write(self.style.WARNING("Readiness passed with warnings."))
        else:
            self.stdout.write(self.style.SUCCESS("Readiness passed with no warnings."))

    def _run_settings_checks(self):
        checks = []

        checks.append(self._check(
            "DEBUG",
            not bool(getattr(settings, "DEBUG", True)),
            f"DEBUG={getattr(settings, 'DEBUG', None)} (should be False)",
        ))

        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        allowed_ok = bool(allowed_hosts) and allowed_hosts != ["*"]
        checks.append(self._check(
            "ALLOWED_HOSTS",
            allowed_ok,
            f"ALLOWED_HOSTS={allowed_hosts}",
        ))

        checks.append(self._check(
            "SECURE_SSL_REDIRECT",
            bool(getattr(settings, "SECURE_SSL_REDIRECT", False)),
            f"SECURE_SSL_REDIRECT={getattr(settings, 'SECURE_SSL_REDIRECT', None)}",
        ))

        hsts = int(getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0)
        checks.append(self._check(
            "SECURE_HSTS_SECONDS",
            hsts > 0,
            f"SECURE_HSTS_SECONDS={hsts}",
        ))

        checks.append(self._check(
            "SESSION_COOKIE_SECURE",
            bool(getattr(settings, "SESSION_COOKIE_SECURE", False)),
            f"SESSION_COOKIE_SECURE={getattr(settings, 'SESSION_COOKIE_SECURE', None)}",
        ))

        checks.append(self._check(
            "CSRF_COOKIE_SECURE",
            bool(getattr(settings, "CSRF_COOKIE_SECURE", False)),
            f"CSRF_COOKIE_SECURE={getattr(settings, 'CSRF_COOKIE_SECURE', None)}",
        ))

        checks.append(self._check(
            "SECURE_BROWSER_XSS_FILTER",
            bool(getattr(settings, "SECURE_BROWSER_XSS_FILTER", False)),
            f"SECURE_BROWSER_XSS_FILTER={getattr(settings, 'SECURE_BROWSER_XSS_FILTER', None)}",
            warning=True,
        ))

        checks.append(self._check(
            "X_FRAME_OPTIONS",
            str(getattr(settings, "X_FRAME_OPTIONS", "")).upper() in {"DENY", "SAMEORIGIN"},
            f"X_FRAME_OPTIONS={getattr(settings, 'X_FRAME_OPTIONS', None)}",
            warning=True,
        ))

        return checks

    def _check_migrations(self):
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            return False, f"{len(plan)} unapplied migration step(s)."
        return True, "No pending migrations."

    @staticmethod
    def _check(name, ok, message, warning=False):
        return {
            "name": name,
            "ok": bool(ok),
            "message": message,
            "warning": bool(warning) and bool(not ok),
        }
