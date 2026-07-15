"""Pull CAMS/KFintech mailback emails and import them. Runs daily via cron;
silently no-ops when the RTA_FEED_IMAP_* env vars are not configured."""
from django.core.management.base import BaseCommand

from clients.services import rta_feed


class Command(BaseCommand):
    help = "Fetch RTA distributor mailback files from the configured mailbox and import them."

    def handle(self, *args, **options):
        imports = rta_feed.fetch_from_mailbox()
        if not imports:
            self.stdout.write("No RTA feed emails processed (mailbox unconfigured or empty).")
            return
        for feed_import in imports:
            self.stdout.write(
                f"{feed_import.file_name}: {feed_import.status} — "
                f"{feed_import.rows_imported} imported, {feed_import.rows_duplicate} duplicate, "
                f"{feed_import.folios_created} new folios, {feed_import.clients_linked} clients linked"
            )
