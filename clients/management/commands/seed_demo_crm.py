"""Populate Households, Insurance, Claims and Meetings with demo data.

Manual tool — never in CRONJOBS. Companion to seed_demo_tasks_links, which
covers the Tasks and Links modules.

Everything created here is tagged so it can be removed again exactly:
  * demo clients use ids from DEMO_CLIENT_ID_BASE upward
  * demo households use codes starting DEMO_PREFIX
  * demo policies use policy numbers starting DEMO_PREFIX
Nothing outside those ranges is ever touched, so running this against a
database that already holds real records cannot damage them.

    .venv/bin/python manage.py seed_demo_crm          # add demo data
    .venv/bin/python manage.py seed_demo_crm --undo   # remove it again
"""
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from clients.models import (
    Client,
    Employee,
    Family,
    InsuranceClaim,
    InsurancePolicy,
    Meeting,
)

DEMO_PREFIX = "DEMO-"
DEMO_CLIENT_ID_BASE = 990_000

FAMILIES = [
    # (household, members [(name, lumpsum, pms, sip_monthly)])
    ("Parekh Family", [
        ("Rajesh Parekh", 8_200_000, 3_000_000, 25_000),
        ("Sunita Parekh", 4_100_000, 0, 10_000),
        ("Aditi Parekh", 900_000, 0, 5_000),
    ]),
    ("Yadav Family", [
        ("Mahesh Yadav", 32_000_000, 12_000_000, 100_000),
        ("Kavita Yadav", 9_500_000, 4_000_000, 30_000),
    ]),
    ("Wagle Family", [
        ("Nitin Wagle", 6_400_000, 0, 15_000),
        ("Prerna Wagle", 2_100_000, 0, 8_000),
    ]),
    ("Bose Family", [
        ("Vishal Bose", 3_100_000, 0, 12_000),
        ("Diya Bose", 800_000, 0, 3_000),
    ]),
    ("Malhotra Family", [
        ("Aarav Malhotra", 1_900_000, 0, 6_000),
    ]),
]

INSURERS = ["ICICI Lombard", "HDFC Ergo", "Star Health", "LIC", "Bajaj Allianz"]
HEALTH_PLANS = ["Arogya Supreme", "Optima Restore", "Family Health Optima"]
LIFE_PLANS = ["Jeevan Anand", "Click 2 Protect", "Smart Term Plan"]


class Command(BaseCommand):
    help = "Seed demo households, insurance policies, claims and meetings."

    def add_arguments(self, parser):
        parser.add_argument("--undo", action="store_true",
                            help="Remove previously seeded demo data and exit.")
        parser.add_argument("--force", action="store_true",
                            help="Required when DEBUG is off (i.e. on a real server).")

    def handle(self, *args, **opts):
        # Guard: with DEBUG off this is probably a live server. Demo clients
        # sitting alongside real ones is a mess nobody wants to untangle.
        if not settings.DEBUG and not opts["force"]:
            self.stdout.write(self.style.ERROR(
                "DEBUG is off — this looks like a real server. Re-run with --force "
                "if you really want demo data here."))
            return

        if opts["undo"]:
            return self._undo()

        emps = list(Employee.objects.filter(active=True).select_related("user"))
        random.seed(20260720)   # stable output across runs

        with transaction.atomic():
            families = self._seed_families(emps)
            clients = [c for f in families for c in f.members.all()]
            policies = self._seed_policies(clients, emps)
            claims = self._seed_claims(policies, emps)
            meetings = self._seed_meetings(clients, emps)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(families)} households, {len(clients)} clients, "
            f"{len(policies)} policies, {len(claims)} claims, {len(meetings)} meetings."))
        self.stdout.write("Remove it again with:  manage.py seed_demo_crm --undo")

    # ── seeding ──────────────────────────────────────────────────────────

    def _seed_families(self, emps):
        out = []
        next_id = DEMO_CLIENT_ID_BASE
        for i, (name, members) in enumerate(FAMILIES, start=1):
            family, _ = Family.objects.get_or_create(
                code=f"{DEMO_PREFIX}A{i:03d}",
                defaults={
                    "name": name,
                    "relationship_manager": emps[i % len(emps)] if emps else None,
                },
            )
            for j, (member, lump, pms, sip) in enumerate(members):
                client, _ = Client.objects.get_or_create(
                    id=next_id,
                    defaults={
                        "name": member,
                        "email": f"{member.split()[0].lower()}@example.com",
                        "phone": f"9{random.randint(100000000, 999999999)}",
                        "pan": f"ABCDE{1000 + next_id % 9000}F",
                        "lumsum_investment": lump,
                        "pms_amount": pms,
                        "pms_status": pms > 0,
                        "sip_amount": sip,
                        "sip_status": sip > 0,
                        "mapped_to": emps[next_id % len(emps)] if emps else None,
                        "family": family,
                    },
                )
                # Re-attach on a repeat run in case the family was recreated.
                if client.family_id != family.id:
                    client.family = family
                    client.save(update_fields=["family"])
                if j == 0 and family.head_id != client.id:
                    family.head = client
                    family.save(update_fields=["head"])
                next_id += 1
            out.append(family)
        return out

    def _seed_policies(self, clients, emps):
        today = timezone.localdate()
        out = []
        for n, client in enumerate(clients):
            for kind in self._kinds_for(n):
                if kind == InsurancePolicy.TYPE_HEALTH:
                    plan, cover, premium = random.choice(HEALTH_PLANS), 700_000, 76_209
                elif kind == InsurancePolicy.TYPE_LIFE:
                    plan, cover, premium = random.choice(LIFE_PLANS), 5_000_000, 42_000
                else:
                    plan, cover, premium = "Motor Secure", 850_000, 18_400

                # Spread end dates so the "expiring ≤30 days" tile has content.
                offset = [12, 25, 120, 300, -40][n % 5]
                status = (InsurancePolicy.STATUS_LAPSED if offset < 0
                          else InsurancePolicy.STATUS_ACTIVE)
                policy, _ = InsurancePolicy.objects.get_or_create(
                    policy_number=f"{DEMO_PREFIX}{kind[:2].upper()}{70000 + n:05d}",
                    defaults={
                        "client": client,
                        "insurer": random.choice(INSURERS),
                        "plan_name": plan,
                        "insurance_type": kind,
                        "status": status,
                        "sum_insured": cover,
                        "premium_amount": premium,
                        "start_date": today - timedelta(days=365 * 2),
                        "end_date": today + timedelta(days=offset),
                        "term_months": 36,
                        "nominee_name": client.name.split()[0] + " (spouse)",
                        "nominee_relationship": "Spouse",
                        "relationship_manager": emps[n % len(emps)] if emps else None,
                    },
                )
                out.append(policy)
        return out

    @staticmethod
    def _kinds_for(n):
        """Give most clients health, some life, a few motor."""
        kinds = [InsurancePolicy.TYPE_HEALTH]
        if n % 2 == 0:
            kinds.append(InsurancePolicy.TYPE_LIFE)
        if n % 3 == 0:
            kinds.append(InsurancePolicy.TYPE_MOTOR)
        return kinds

    def _seed_claims(self, policies, emps):
        today = timezone.localdate()
        states = [
            (InsuranceClaim.STATUS_SETTLED, 245_500, 218_750),
            (InsuranceClaim.STATUS_INTIMATED, 120_000, 0),
            (InsuranceClaim.STATUS_FILE_RECEIVED, 88_000, 0),
            (InsuranceClaim.STATUS_SUBMITTED, 310_000, 0),
            (InsuranceClaim.STATUS_REJECTED, 65_000, 0),
        ]
        out = []
        health = [p for p in policies if p.insurance_type == InsurancePolicy.TYPE_HEALTH]
        for i, policy in enumerate(health[:6]):
            status, claimed, settled = states[i % len(states)]
            intimated = today - timedelta(days=60 - i * 7)
            claim, created = InsuranceClaim.objects.get_or_create(
                policy=policy,
                claim_type="Hospitalisation Claim",
                defaults={
                    "claim_mode": (InsuranceClaim.MODE_CASHLESS if i % 2 == 0
                                   else InsuranceClaim.MODE_REIMBURSEMENT),
                    "status": status,
                    "intimation_date": intimated,
                    "admission_date": intimated + timedelta(days=3),
                    "submission_date": intimated + timedelta(days=6),
                    "settlement_date": (intimated + timedelta(days=20)
                                        if status == InsuranceClaim.STATUS_SETTLED else None),
                    "claimed_amount": claimed,
                    "settled_amount": settled,
                    "settlement_details": ("Approved partially after deductions for "
                                           "non-medical items."
                                           if status == InsuranceClaim.STATUS_SETTLED else ""),
                    "handled_by": emps[i % len(emps)] if emps else None,
                },
            )
            if created:
                out.append(claim)
        return out

    def _seed_meetings(self, clients, emps):
        # Anchor to 10:00 today rather than "now", so scheduled_at is stable
        # across runs on the same day and get_or_create actually matches.
        from datetime import datetime, time as dtime
        base = timezone.make_aware(
            datetime.combine(timezone.localdate(), dtime(10, 0)),
            timezone.get_current_timezone())
        now = base
        out = []
        for i, client in enumerate(clients):
            # A past meeting that was held, plus a forward booking.
            held_at = now - timedelta(days=30 + i * 3)
            done, created = Meeting.objects.get_or_create(
                client=client, scheduled_at=held_at,
                defaults={
                    "employee": emps[i % len(emps)] if emps else None,
                    "kind": Meeting.KIND_REVIEW,
                    "status": Meeting.STATUS_COMPLETED,
                    "held_at": held_at,
                    "next_meeting_date": (now + timedelta(days=60 + i * 5)).date(),
                    "outcome": "Portfolio reviewed. Client comfortable with allocation.",
                },
            )
            if created:
                out.append(done)

            # Every third client has one that slipped — populates "Overdue".
            upcoming = now + (timedelta(days=7 + i) if i % 3 else -timedelta(days=4))
            nxt, created = Meeting.objects.get_or_create(
                client=client, scheduled_at=upcoming,
                defaults={
                    "employee": emps[i % len(emps)] if emps else None,
                    "kind": Meeting.KIND_SERVICE if i % 2 else Meeting.KIND_REVIEW,
                    "status": Meeting.STATUS_SCHEDULED,
                },
            )
            if created:
                out.append(nxt)
        return out

    # ── undo ─────────────────────────────────────────────────────────────

    def _undo(self):
        """Delete exactly what this command creates, and nothing else."""
        policies = InsurancePolicy.objects.filter(policy_number__startswith=DEMO_PREFIX)
        claims = InsuranceClaim.objects.filter(policy__in=policies)
        clients = Client.objects.filter(id__gte=DEMO_CLIENT_ID_BASE)
        meetings = Meeting.objects.filter(client__in=clients)
        families = Family.objects.filter(code__startswith=DEMO_PREFIX)

        counts = (claims.count(), policies.count(), meetings.count(),
                  clients.count(), families.count())
        claims.delete()
        policies.delete()
        meetings.delete()
        # Clear the head FK first so deleting members doesn't trip on it.
        families.update(head=None)
        clients.delete()
        families.delete()

        self.stdout.write(self.style.SUCCESS(
            "Removed demo data: %d claims, %d policies, %d meetings, "
            "%d clients, %d households." % counts))
