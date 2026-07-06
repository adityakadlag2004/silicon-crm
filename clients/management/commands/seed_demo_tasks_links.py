"""Populate the local DB with demo Tasks + Business Links for testing.

Idempotent-ish: pass --reset to wipe existing tasks/links/categories first.
Attaches data to whatever Employees already exist. Skips file attachments
(those need Google Drive); everything else — checklists, comments, subscribers,
activities, a recurring rule, favorites — is created so both modules look real.

    .venv/bin/python manage.py seed_demo_tasks_links --reset
"""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import (
    Employee,
    Link,
    LinkCategory,
    LinkFavorite,
    RecurringTaskRule,
    Task,
    TaskActivity,
    TaskCategory,
    TaskChecklistItem,
    TaskComment,
    TaskSubscriber,
)
from clients.services.tasks import generate_recurring, log_activity

TASK_CATEGORIES = [
    ("Sales", "#16a34a", "bi-graph-up-arrow"),
    ("Operations", "#2563eb", "bi-gear-fill"),
    ("Compliance", "#dc2626", "bi-shield-check"),
    ("Finance", "#7c3aed", "bi-cash-stack"),
    ("HR", "#db2777", "bi-people-fill"),
    ("Customer Support", "#d97706", "bi-headset"),
]

LINK_CATEGORIES = [
    ("Registrars & Platforms", "#2563eb", "bi-diagram-3-fill"),
    ("Insurance", "#16a34a", "bi-shield-plus"),
    ("Compliance", "#dc2626", "bi-file-earmark-check"),
    ("Internal Tools", "#7c3aed", "bi-tools"),
    ("Research", "#d97706", "bi-search"),
]

LINKS = {
    "Registrars & Platforms": [
        ("CAMS Online", "https://www.camsonline.com", "MF registrar — statements & transactions", "bi-bank"),
        ("KFintech", "https://www.kfintech.com", "Karvy/KFin MF registrar", "bi-bank2"),
        ("MF Utility", "https://www.mfuonline.com", "Consolidated MF transaction platform", "bi-layers"),
        ("BSE StAR MF", "https://www.bsestarmf.in", "BSE mutual-fund order platform", "bi-graph-up"),
        ("NSE NMF II", "https://www.nsenmf.com", "NSE mutual-fund platform", "bi-graph-up-arrow"),
    ],
    "Insurance": [
        ("Star Health Portal", "https://www.starhealth.in", "Star Health partner login", "bi-heart-pulse"),
        ("HDFC Life Partner", "https://www.hdfclife.com", "HDFC Life advisor portal", "bi-umbrella"),
        ("ICICI Pru Partner", "https://www.iciciprulife.com", "ICICI Prudential advisor login", "bi-shield"),
        ("Aditya Birla Health", "https://www.adityabirlacapital.com", "ABHI partner portal", "bi-heart"),
    ],
    "Compliance": [
        ("AMFI", "https://www.amfiindia.com", "ARN, NAV & industry data", "bi-file-earmark-text"),
        ("SEBI", "https://www.sebi.gov.in", "Regulator circulars & guidelines", "bi-bank"),
        ("CDSL", "https://www.cdslindia.com", "Depository — demat services", "bi-safe"),
        ("KRA / CVL KYC", "https://www.cvlkra.com", "KYC registration agency", "bi-person-vcard"),
    ],
    "Internal Tools": [
        ("Google Drive", "https://drive.google.com", "Client documents", "bi-folder"),
        ("Gmail", "https://mail.google.com", "Office email", "bi-envelope"),
        ("WhatsApp Web", "https://web.whatsapp.com", "Client messaging", "bi-whatsapp"),
        ("Calendly", "https://calendly.com", "Client meeting scheduling", "bi-calendar-check"),
    ],
    "Research": [
        ("Value Research", "https://www.valueresearchonline.com", "Fund research & ratings", "bi-star"),
        ("Morningstar India", "https://www.morningstar.in", "Fund analysis", "bi-bar-chart"),
        ("Moneycontrol", "https://www.moneycontrol.com", "Markets & news", "bi-newspaper"),
    ],
}

TASKS = [
    # (title, description, category, priority, status, due_offset_days)
    ("Collect KYC documents — Sharma family", "Aadhaar, PAN, cancelled cheque for 3 members.", "Compliance", "high", "in_progress", 1),
    ("Follow up SIP top-up — Patil", "Client wants to increase SIP from 10k to 15k.", "Sales", "medium", "pending", 2),
    ("Health policy renewal — Deshmukh", "Star Health family floater renewal due.", "Operations", "critical", "pending", 0),
    ("Prepare monthly business report", "Consolidate net business + SIP for June.", "Finance", "high", "in_progress", 3),
    ("Reconcile CAMS commission statement", "Match brokerage vs internal records.", "Finance", "medium", "pending", 5),
    ("Submit AMFI ARN renewal", "ARN expiring next month — start renewal.", "Compliance", "high", "pending", 7),
    ("Call back campaign leads", "20 leads from the Diwali SIP campaign.", "Sales", "medium", "overdue", -2),
    ("Update client risk profiles", "Annual risk-profiling refresh for top 30 clients.", "Operations", "low", "pending", 14),
    ("Motor insurance renewal — Joshi", "Car policy expires in 5 days.", "Operations", "high", "overdue", -1),
    ("Onboard new employee laptop", "Setup CRM access + email for new joiner.", "HR", "low", "completed", -3),
    ("Resolve client login complaint", "Client can't see portfolio on app.", "Customer Support", "medium", "completed", -1),
    ("Quarterly portfolio review — Kulkarni", "Prepare review deck for HNI client.", "Sales", "high", "pending", 4),
    ("File GST return", "Q1 GST filing for the firm.", "Finance", "critical", "pending", 6),
    ("Send birthday wishes batch", "Auto-list of clients with birthdays this week.", "Customer Support", "low", "pending", 1),
    ("Verify nominee details", "Cross-check nominee updates from last drive.", "Compliance", "medium", "in_progress", 2),
]

CHECKLISTS = {
    "Collect KYC documents — Sharma family": ["Aadhaar copies", "PAN copies", "Cancelled cheque", "Signed KYC form", "Upload to Drive"],
    "Prepare monthly business report": ["Pull SIP data", "Pull lumpsum data", "Compute net business", "Format deck", "Send to owner"],
    "Health policy renewal — Deshmukh": ["Confirm sum insured", "Collect premium", "Process renewal", "Share policy copy"],
}

COMMENTS = {
    "Follow up SIP top-up — Patil": ["Client asked to call after 6 PM.", "Left a voicemail, will retry tomorrow."],
    "Motor insurance renewal — Joshi": ["Quote shared on WhatsApp.", "Client confirmed, awaiting payment."],
    "Prepare monthly business report": ["Using last month's template."],
}


class Command(BaseCommand):
    help = "Seed demo Tasks + Business Links for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete existing tasks/links/categories first.")

    def handle(self, *args, **opts):
        emps = list(Employee.objects.filter(active=True).select_related("user"))
        if not emps:
            self.stdout.write(self.style.ERROR("No active employees found — create some first."))
            return
        admins = [e for e in emps if e.role == "admin"] or emps
        creator = admins[0].user
        all_users = [e.user for e in emps]

        if opts["reset"]:
            Task.objects.all().delete()
            RecurringTaskRule.objects.all().delete()
            TaskCategory.objects.all().delete()
            Link.objects.all().delete()
            LinkCategory.objects.all().delete()
            self.stdout.write("Cleared existing tasks & links.")

        # ── Task categories ──
        cat_map = {}
        for name, color, icon in TASK_CATEGORIES:
            cat_map[name], _ = TaskCategory.objects.get_or_create(
                name=name, defaults={"color": color, "icon": icon, "created_by": creator})

        today = timezone.localdate()
        now = timezone.now()
        made = 0
        for title, desc, cat, prio, status, off in TASKS:
            assignee = random.choice(emps)
            due = today + timedelta(days=off)
            task = Task.objects.create(
                title=title, description=desc, category=cat_map.get(cat),
                priority=prio, status=status, created_by=creator, assigned_to=assignee,
                due_date=due, due_time=None,
                completed_at=now if status == "completed" else None,
            )
            log_activity(task, creator, TaskActivity.CREATED, f"Created “{title}”.")
            log_activity(task, creator, TaskActivity.ASSIGNED, f"Assigned to {assignee.user.username}.")

            # subscribers: a couple of other users
            for u in random.sample(all_users, k=min(2, len(all_users))):
                if u != assignee.user:
                    TaskSubscriber.objects.get_or_create(task=task, user=u)

            for i, item in enumerate(CHECKLISTS.get(title, [])):
                done = status == "completed" or (status == "in_progress" and i < 2)
                TaskChecklistItem.objects.create(
                    task=task, title=item, order=i, is_done=done,
                    completed_by=assignee.user if done else None,
                    completed_at=now if done else None)
                if done:
                    log_activity(task, assignee.user, TaskActivity.CHECKLIST_UPDATED, f"✓ {item}")

            for body in COMMENTS.get(title, []):
                TaskComment.objects.create(task=task, author=assignee.user, body=body)
                log_activity(task, assignee.user, TaskActivity.COMMENT_ADDED, body[:120])

            if status == "completed":
                log_activity(task, assignee.user, TaskActivity.COMPLETED, "Marked completed.")
            made += 1

        # ── A recurring rule (weekly standup) + generate its due instances ──
        rule = RecurringTaskRule.objects.create(
            title="Weekly team review", description="Review pipeline + pending tasks.",
            category=cat_map.get("Operations"), priority="medium",
            created_by=creator, assigned_to=admins[0],
            frequency="weekly", interval=1, weekdays=str((today.weekday()) % 7),
            start_date=today - timedelta(days=21), occurrences_created=1,
            last_generated_date=today - timedelta(days=21),
            checklist_template="Review new leads\nReview overdue tasks\nPlan the week",
        )
        gen = generate_recurring()

        # ── Link categories + links ──
        lcat_map = {}
        for i, (name, color, icon) in enumerate(LINK_CATEGORIES):
            lcat_map[name], _ = LinkCategory.objects.get_or_create(
                name=name, defaults={"color": color, "icon": icon, "display_order": i, "created_by": creator})

        link_count = 0
        created_links = []
        for cat_name, items in LINKS.items():
            for order, (title, url, d, icon) in enumerate(items):
                link, _ = Link.objects.get_or_create(
                    title=title, defaults={
                        "url": url, "description": d, "icon": icon,
                        "category": lcat_map.get(cat_name), "created_by": random.choice(all_users),
                        "display_order": order})
                created_links.append(link)
                link_count += 1

        # A few favorites for every admin/manager, so the Favorites section is
        # populated whichever account you log in with.
        fav_users = [e.user for e in emps if e.role in ("admin", "manager")] or [creator]
        for u in fav_users:
            for link in random.sample(created_links, k=min(4, len(created_links))):
                LinkFavorite.objects.get_or_create(user=u, link=link)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {made} tasks (+{gen} recurring instances), "
            f"{len(cat_map)} task categories, {link_count} links in {len(lcat_map)} categories."))
