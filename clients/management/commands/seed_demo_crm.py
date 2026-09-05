"""Populate Households, Insurance, Claims and the SPANCO lead
pipeline with demo data.

Manual tool — never in CRONJOBS. Companion to seed_demo_tasks_links, which
covers the Tasks and Links modules.

Everything created here is tagged so it can be removed again exactly:
  * demo clients use ids from DEMO_CLIENT_ID_BASE upward
  * demo households use codes starting DEMO_PREFIX
  * demo policies (and the multiyear sales behind them) use policy numbers
    starting DEMO_PREFIX
  * demo leads use ids from DEMO_LEAD_ID_BASE upward (deleting one takes its
    requirements, stage history, follow-ups and remarks with it)
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
    Lead,
    LeadInterest,
    LeadRemark,
    LeadStageEvent,
    Product,
    Sale,
    Task,
)

DEMO_PREFIX = "DEMO-"
DEMO_CLIENT_ID_BASE = 990_000
DEMO_LEAD_ID_BASE = 990_000

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

# (name, stage, days sitting there, [product codes wanted], lost reason or "")
# Deliberately uneven: a fat top of funnel, a few deals near the line, two
# dead ones, and several parked long enough to show up as stalled.
DEMO_LEADS = [
    ("Rohit Deshmukh",  Lead.STAGE_SUSPECT,      2,  [], ""),
    ("Sneha Kulkarni",  Lead.STAGE_SUSPECT,      5,  ["HEALTH_INS"], ""),
    ("Imran Shaikh",    Lead.STAGE_SUSPECT,     21,  [], ""),
    ("Farah Qureshi",   Lead.STAGE_SUSPECT,     34,  ["SIP"], ""),
    ("Anand Jain",      Lead.STAGE_SUSPECT,      1,  [], ""),
    ("Tejas Bhosale",   Lead.STAGE_PROSPECT,     4,  ["LIFE_INS"], ""),
    ("Meera Iyer",      Lead.STAGE_PROSPECT,    17,  ["HEALTH_INS", "SIP"], ""),
    ("Kabir Sethi",     Lead.STAGE_PROSPECT,     9,  ["SIP"], ""),
    ("Nandini Rao",     Lead.STAGE_APPROACH,     6,  ["HEALTH_INS"], ""),
    ("Vikram Chandra",  Lead.STAGE_APPROACH,    28,  ["LIFE_INS", "SIP"], ""),
    ("Pooja Nair",      Lead.STAGE_NEGOTIATION,  3,  ["HEALTH_INS"], ""),
    ("Sanjay Gupta",    Lead.STAGE_NEGOTIATION, 19,  ["LIFE_INS"], ""),
    ("Ritu Malhotra",   Lead.STAGE_CONCLUSION,   2,  ["HEALTH_INS", "LIFE_INS"], ""),
    ("Devendra Patil",  Lead.STAGE_ORDER,        1,  ["SIP"], ""),
    ("Aisha Khan",      Lead.STAGE_ORDER,        8,  ["HEALTH_INS"], ""),
    ("Gaurav Menon",    Lead.STAGE_NEGOTIATION, 40,  ["LIFE_INS"], "Premium too high, went with a bank plan"),
    ("Leela Prasad",    Lead.STAGE_PROSPECT,    52,  [], "Not reachable after four attempts"),
]

# Amounts asked for, per product, so the pipeline report has a value column.
DEMO_INTEREST_AMOUNTS = {
    "HEALTH_INS": 25_000,
    "LIFE_INS": 100_000,
    "SIP": 15_000,
}

DEMO_REMARKS = [
    "Spoke on call, wants a comparison of two insurers.",
    "Asked us to reach out after the 10th — salary credit.",
    "Referred by an existing client, warm.",
    "Wants the spouse covered on the same policy.",
]


class Command(BaseCommand):
    help = "Seed demo households, insurance policies and claims."

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
            leads = self._seed_leads(emps)
            sales = self._seed_multiyear_sales(clients, emps)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(families)} households, {len(clients)} clients, "
            f"{len(policies)} policies, {len(claims)} claims, "
            f"{len(leads)} leads, {len(sales)} multiyear health sales."))
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

    def _seed_leads(self, emps):
        """Leads spread across all six SPANCO stages, plus two lost ones.

        Each lead is walked through the stages it has passed so the stage
        history, the funnel's step-by-step conversion and the "stalled 14+
        days" panel all have something real to show. Requirements are picked
        per lead from the MAIN product catalog — never a fixed trio, and
        never a sub-product.
        """
        if not emps:
            self.stdout.write(self.style.WARNING(
                "No active employees — skipping demo leads (a lead needs an owner)."))
            return []

        from clients.services import followups
        from clients.services import leads as lead_service

        catalog = {
            p.code: p for p in Product.objects.selectable().main()
            .filter(code__in=DEMO_INTEREST_AMOUNTS)
        }
        now = timezone.now()
        out = []

        for n, (name, stage, days, product_codes, lost_reason) in enumerate(DEMO_LEADS):
            lead, created = Lead.objects.get_or_create(
                id=DEMO_LEAD_ID_BASE + n,
                defaults={
                    "customer_name": name,
                    "phone": f"9{random.randint(100000000, 999999999)}",
                    "email": f"{name.split()[0].lower()}@example.com",
                    "income": random.choice([600_000, 900_000, 1_500_000, 2_400_000]),
                    "assigned_to": emps[n % len(emps)],
                    "notes": "Demo lead — safe to delete.",
                },
            )
            if not created:
                continue

            # Walk the roadmap: a lead at Negotiation really did pass through
            # Suspect, Prospect and Approach, and the history says so.
            path = Lead.STAGE_SEQUENCE[:Lead.STAGE_SEQUENCE.index(stage) + 1]
            for step, to_stage in enumerate(path):
                if step == 0:
                    # Entering the pipeline, written exactly as the real
                    # create form writes it: no "from", so the timeline reads
                    # "— → Suspect" and not "Suspect → Suspect".
                    event = lead.stage_events.create(to_stage=to_stage, note="Lead created")
                else:
                    event = lead_service.set_stage(lead, to_stage, note=f"Moved to {to_stage}.")
                # auto_now_add ignores assignment, so backdate after the fact —
                # a timeline stamped all-today reads as fake. The last move
                # lands on `days` ago, matching the stage clock set below.
                if event:
                    LeadStageEvent.objects.filter(pk=event.pk).update(
                        created_at=now - timedelta(days=days + (len(path) - 1 - step) * 4),
                    )

            for code in product_codes:
                product = catalog.get(code)
                if product:
                    LeadInterest.objects.get_or_create(
                        lead=lead, product=product,
                        defaults={"amount": DEMO_INTEREST_AMOUNTS[code]},
                    )

            LeadRemark.objects.create(lead=lead, text=DEMO_REMARKS[n % len(DEMO_REMARKS)])

            # Every third live lead carries a follow-up; one in six is overdue.
            # A follow-up is a Task — services/followups.py owns that.
            if not lost_reason and n % 3 == 0:
                overdue = n % 6 == 0
                task = followups.schedule(
                    followups.LEAD, lead,
                    now + timedelta(days=-2 if overdue else 3, hours=n % 7),
                    note="Call back with the quote.",
                )
                # Past-dated demo rows must not set the ring cron going.
                if overdue:
                    Task.objects.filter(pk=task.pk).update(due_alarm_sent_at=now)

            if lost_reason:
                lead_service.mark_lost(lead, reason=lost_reason)

            Lead.objects.filter(pk=lead.pk).update(stage_changed_at=now - timedelta(days=days))
            out.append(lead)

        return out

    def _seed_multiyear_sales(self, clients, emps):
        """Multiyear health policies — the book behind the Future Points page.

        A 2- or 3-year premium is paid up front and credited one year at a
        time, so each of these sales owes its seller points on anniversaries
        still to come. Started inside the last year so every later year is in
        the future, and spread across employees and months so the FY → month →
        policy drill-down has something in each level.
        """
        product = Product.objects.filter(code="HEALTH_INS").first()
        if not (product and emps):
            self.stdout.write(self.style.WARNING(
                "No HEALTH_INS product or no active employees — skipped multiyear sales."))
            return []

        today = timezone.localdate()
        out = []
        for n, client in enumerate(clients[:12]):
            years = 3 if n % 2 else 2
            annual = [76_209, 41_500, 128_400, 63_750][n % 4]
            start = today - timedelta(days=[20, 75, 140, 210, 300][n % 5])
            number = f"{DEMO_PREFIX}MY{70000 + n:05d}"
            employee = emps[n % len(emps)]

            sale, created = Sale.objects.get_or_create(
                client=client, policy_number=number,
                defaults={
                    "employee": employee,
                    "product": product.name,
                    "product_ref": product,
                    "amount": annual * years,
                    "cover_amount": 700_000,
                    "date": start,
                    "policy_date": start,
                    "policy_type": "fresh",
                    "policy_years": years,
                    "emi_months": [0, 5, 8, 11][n % 4],
                    "status": Sale.STATUS_APPROVED,
                    "policy_doc_submitted": bool(n % 3),
                },
            )
            InsurancePolicy.objects.get_or_create(
                policy_number=number,
                defaults={
                    "client": client,
                    "insurer": random.choice(INSURERS),
                    "plan_name": random.choice(HEALTH_PLANS),
                    "insurance_type": InsurancePolicy.TYPE_HEALTH,
                    "status": InsurancePolicy.STATUS_ACTIVE,
                    "sum_insured": 700_000,
                    "premium_amount": annual,
                    "start_date": start,
                    "end_date": start + timedelta(days=365 * years),
                    "term_months": 12 * years,
                    "nominee_name": client.name.split()[0] + " (spouse)",
                    "nominee_relationship": "Spouse",
                    "relationship_manager": employee,
                    "source_sale": sale,
                },
            )
            out.append(sale)
        return out

    # ── undo ─────────────────────────────────────────────────────────────

    def _undo(self):
        """Delete exactly what this command creates, and nothing else."""
        policies = InsurancePolicy.objects.filter(policy_number__startswith=DEMO_PREFIX)
        claims = InsuranceClaim.objects.filter(policy__in=policies)
        clients = Client.objects.filter(id__gte=DEMO_CLIENT_ID_BASE)
        # Sales cascade off the client anyway; deleted first so the count is
        # honest and so their accruals go with them.
        sales = Sale.objects.filter(client_id__gte=DEMO_CLIENT_ID_BASE)
        families = Family.objects.filter(code__startswith=DEMO_PREFIX)
        leads = Lead.objects.filter(id__gte=DEMO_LEAD_ID_BASE)

        counts = (claims.count(), policies.count(), sales.count(),
                  clients.count(), families.count(), leads.count())
        claims.delete()
        policies.delete()
        sales.delete()
        # Requirements, stage history, follow-ups and remarks cascade off these.
        leads.delete()
        # Clear the head FK first so deleting members doesn't trip on it.
        families.update(head=None)
        clients.delete()
        families.delete()

        self.stdout.write(self.style.SUCCESS(
            "Removed demo data: %d claims, %d policies, %d sales, "
            "%d clients, %d households, %d leads." % counts))
