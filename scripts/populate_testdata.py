"""
Populate the Silicon CRM database with realistic Indian test data.

Run:  python manage.py shell < scripts/populate_testdata.py
  or: python manage.py shell -c "exec(open('scripts/populate_testdata.py').read())"
"""
import os, sys, random, datetime
from decimal import Decimal

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.utils import timezone
from django.contrib.auth.models import User
from clients.models import (
    Employee, Client, Sale, IncentiveRule, Lead, LeadFamilyMember,
    LeadProductProgress, LeadFollowUp, LeadRemark, CalendarEvent,
    CallingList, Prospect, CallRecord, Target, Notification,
    NetBusinessEntry, NetSipEntry,
)

random.seed(42)
now = timezone.now()
today = now.date()

# ─── Helper ──────────────────────────────────────────────────────────
def rand_phone():
    return f"9{random.randint(100000000, 999999999)}"

def rand_pan():
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(random.choices(letters, k=5)) + str(random.randint(1000, 9999)) + random.choice(letters)

def rand_date_in_range(start, end):
    delta = (end - start).days
    return start + datetime.timedelta(days=random.randint(0, max(delta, 0)))

def rand_dob():
    return rand_date_in_range(datetime.date(1970, 1, 1), datetime.date(2002, 12, 31))

# ─── Employees ───────────────────────────────────────────────────────
employees = list(Employee.objects.filter(active=True))
admin_user = User.objects.filter(employee__role="admin").first()
print(f"Found {len(employees)} active employees")

# ─── 1. Incentive Rules ─────────────────────────────────────────────
rules_data = [
    ("SIP",              1000,   1),
    ("Lumsum",           100000, 1),
    ("Life Insurance",   100000, 1),
    ("Health Insurance", 10000,  1),
    ("Motor Insurance",  10000,  1),
    ("PMS",              100000, 1),
    ("COB",              100000, 1),
]
for product, unit, pts in rules_data:
    IncentiveRule.objects.update_or_create(
        product=product,
        defaults={"unit_amount": Decimal(str(unit)), "points_per_unit": Decimal(str(pts)), "active": True},
    )
print(f"✓ {IncentiveRule.objects.count()} incentive rules")

# ─── 2. Targets ──────────────────────────────────────────────────────
products = ["SIP", "Lumsum", "Life Insurance", "Health Insurance", "Motor Insurance", "PMS", "COB"]
target_vals = {
    "SIP": (50000, 1500000), "Lumsum": (200000, 5000000),
    "Life Insurance": (100000, 3000000), "Health Insurance": (50000, 1000000),
    "Motor Insurance": (30000, 500000), "PMS": (200000, 5000000), "COB": (100000, 3000000),
}
for prod in products:
    daily, monthly = target_vals[prod]
    Target.objects.update_or_create(product=prod, target_type="daily", defaults={"target_value": daily})
    Target.objects.update_or_create(product=prod, target_type="monthly", defaults={"target_value": monthly})
print(f"✓ {Target.objects.count()} targets")

# ─── 3. Clients (60) ────────────────────────────────────────────────
FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan",
    "Krishna", "Ishaan", "Shaurya", "Atharv", "Advik", "Pranav", "Advaith", "Aryan",
    "Dhruv", "Kabir", "Ritvik", "Aarush", "Karthik", "Sahil", "Rohan", "Ankit",
    "Ananya", "Saanvi", "Aanya", "Aadhya", "Aarohi", "Anvi", "Prisha", "Myra",
    "Sara", "Ira", "Anika", "Navya", "Diya", "Riya", "Kiara", "Tara",
    "Sneha", "Pooja", "Meera", "Shalini", "Rashmi", "Kavita", "Sunita", "Neha",
    "Priya", "Deepa", "Rekha", "Anjali", "Swati", "Nandini", "Pallavi", "Shruti",
    "Rahul", "Amit", "Suresh", "Rajesh",
]
LAST_NAMES = [
    "Sharma", "Verma", "Patel", "Deshmukh", "Patil", "Kulkarni", "Joshi",
    "Mehta", "Shah", "Reddy", "Iyer", "Nair", "Singh", "Gupta", "Kumar",
    "Agarwal", "Mishra", "Pandey", "Chauhan", "Yadav",
]
CITIES = [
    "Pune", "Mumbai", "Nagpur", "Nashik", "Aurangabad", "Kolhapur",
    "Solapur", "Thane", "Satara", "Sangli",
]
HEALTH_PRODUCTS = ["Star Health", "HDFC Ergo", "ICICI Lombard", "Niva Bupa", "Care Health"]
LIFE_PRODUCTS = ["LIC Jeevan Anand", "HDFC Life Click 2 Protect", "Max Life Smart Secure", "ICICI Pru iProtect", "SBI Life eShield"]
MOTOR_PRODUCTS = ["Bajaj Allianz Motor", "ICICI Lombard Motor", "HDFC Ergo Motor", "New India Motor"]

clients = []
for i in range(60):
    fname = random.choice(FIRST_NAMES)
    lname = random.choice(LAST_NAMES)
    emp = random.choice(employees)
    has_sip = random.random() < 0.5
    has_health = random.random() < 0.35
    has_life = random.random() < 0.25
    has_motor = random.random() < 0.2
    has_pms = random.random() < 0.15

    c = Client(
        name=f"{fname} {lname}",
        email=f"{fname.lower()}.{lname.lower()}{random.randint(1,99)}@gmail.com",
        phone=rand_phone(),
        pan=rand_pan(),
        address=f"{random.randint(1,500)}, {random.choice(['MG Road','FC Road','JM Road','Station Road','Market Yard','Camp Area','Civil Lines'])}, {random.choice(CITIES)}",
        date_of_birth=rand_dob() if random.random() < 0.7 else None,
        mapped_to=emp,
        status="Mapped",
        sip_status=has_sip,
        sip_amount=Decimal(str(random.choice([1000,2000,3000,5000,10000,15000,20000,25000]))) if has_sip else None,
        sip_topup=Decimal(str(random.choice([0,500,1000,2000]))) if has_sip and random.random() < 0.3 else None,
        lumsum_investment=Decimal(str(random.choice([0, 50000, 100000, 200000, 500000, 1000000]))) if random.random() < 0.4 else Decimal("0"),
        health_status=has_health,
        health_cover=Decimal(str(random.choice([300000, 500000, 1000000, 1500000]))) if has_health else None,
        health_product=random.choice(HEALTH_PRODUCTS) if has_health else None,
        life_status=has_life,
        life_cover=Decimal(str(random.choice([2500000, 5000000, 10000000, 15000000]))) if has_life else None,
        life_product=random.choice(LIFE_PRODUCTS) if has_life else None,
        motor_status=has_motor,
        motor_insured_value=Decimal(str(random.choice([300000, 500000, 800000, 1200000]))) if has_motor else None,
        motor_product=random.choice(MOTOR_PRODUCTS) if has_motor else None,
        pms_status=has_pms,
        pms_amount=Decimal(str(random.choice([500000, 1000000, 2500000, 5000000]))) if has_pms else None,
        pms_start_date=rand_date_in_range(datetime.date(2024, 1, 1), today) if has_pms else None,
    )
    c.save()
    clients.append(c)

print(f"✓ {len(clients)} clients created (IDs {clients[0].id}–{clients[-1].id})")

# ─── 4. Sales (120+) across last 3 months ───────────────────────────
sale_products = ["SIP", "Lumsum", "Life Insurance", "Health Insurance", "Motor Insurance", "PMS", "COB"]
amount_ranges = {
    "SIP":              (1000, 25000),
    "Lumsum":           (50000, 2000000),
    "Life Insurance":   (15000, 500000),
    "Health Insurance":  (8000, 80000),
    "Motor Insurance":   (5000, 50000),
    "PMS":              (200000, 5000000),
    "COB":              (100000, 1500000),
}

sales_created = 0
# Generate sales for past 3 months + current month
for month_offset in range(4):
    ref = today.replace(day=1) - datetime.timedelta(days=30 * month_offset)
    month_start = ref.replace(day=1)
    if month_offset == 0:
        month_end = today
    else:
        next_month = month_start.replace(day=28) + datetime.timedelta(days=4)
        month_end = next_month.replace(day=1) - datetime.timedelta(days=1)

    # 25-40 sales per month
    num_sales = random.randint(25, 40)
    for _ in range(num_sales):
        emp = random.choice(employees)
        client = random.choice(clients)
        prod = random.choice(sale_products)
        lo, hi = amount_ranges[prod]
        amt = Decimal(str(random.randint(lo, hi)))
        # Round to nearest 1000
        amt = (amt // 1000) * 1000
        if amt < lo:
            amt = Decimal(str(lo))

        sale_date = rand_date_in_range(month_start, month_end)
        status = random.choices(["approved", "pending", "rejected"], weights=[70, 25, 5])[0]

        s = Sale(
            client=client,
            employee=emp,
            product=prod,
            amount=amt,
            cover_amount=Decimal(str(random.choice([500000, 1000000, 1500000]))) if prod in ("Life Insurance", "Health Insurance") else None,
            status=status,
            date=sale_date,
            approved_by=admin_user if status == "approved" else None,
            approved_at=timezone.make_aware(datetime.datetime.combine(sale_date, datetime.time(10, 0))) if status == "approved" else None,
        )
        s.save()
        sales_created += 1

print(f"✓ {sales_created} sales created")

# ─── 5. Leads (20) ──────────────────────────────────────────────────
leads_created = []
for i in range(20):
    fname = random.choice(FIRST_NAMES)
    lname = random.choice(LAST_NAMES)
    emp = random.choice(employees)
    stage = random.choice(["pending", "half_sold", "processed"])
    lead = Lead.objects.create(
        customer_name=f"{fname} {lname}",
        phone=rand_phone(),
        email=f"{fname.lower()}{random.randint(1,99)}@gmail.com",
        assigned_to=emp,
        created_by=admin_user,
        stage=stage,
        data_received=random.random() < 0.6,
        data_received_on=rand_date_in_range(today - datetime.timedelta(days=60), today) if random.random() < 0.5 else None,
        income=Decimal(str(random.randint(30000, 200000))) if random.random() < 0.6 else None,
        expenses=Decimal(str(random.randint(15000, 100000))) if random.random() < 0.5 else None,
        notes=random.choice([
            "Interested in tax-saving investment",
            "Looking for health cover for family",
            "Wants to start SIP for children's education",
            "Referred by existing client",
            "Walk-in enquiry about PMS",
            "Called regarding life insurance",
            "",
        ]),
    )
    leads_created.append(lead)

    # Add family members (0-3)
    for _ in range(random.randint(0, 3)):
        LeadFamilyMember.objects.create(
            lead=lead,
            name=f"{random.choice(FIRST_NAMES)} {lname}",
            relation=random.choice(["Spouse", "Son", "Daughter", "Father", "Mother"]),
            date_of_birth=rand_dob() if random.random() < 0.5 else None,
        )

    # Add product progress entries
    for prod in random.sample(["health", "life", "wealth"], k=random.randint(1, 3)):
        LeadProductProgress.objects.create(
            lead=lead,
            product=prod,
            target_amount=Decimal(str(random.choice([100000, 250000, 500000, 1000000]))),
            achieved_amount=Decimal(str(random.choice([0, 50000, 100000, 250000]))),
            status=random.choice(["pending", "half_sold", "processed"]),
            remark=random.choice(["Follow up next week", "Document pending", "Premium paid", "Proposal sent", ""]),
        )

    # Add remarks (1-3)
    for _ in range(random.randint(1, 3)):
        LeadRemark.objects.create(
            lead=lead,
            text=random.choice([
                "Spoke with client, interested in SIP",
                "Documents received, processing",
                "Needs follow-up for health policy",
                "Client wants to compare plans",
                "Meeting scheduled for next week",
                "Quotation shared via WhatsApp",
                "Client confirmed, preparing proposal",
            ]),
            created_by=admin_user,
        )

    # Add follow-ups (0-2)
    for _ in range(random.randint(0, 2)):
        sched = now + datetime.timedelta(days=random.randint(-5, 14), hours=random.randint(9, 17))
        LeadFollowUp.objects.create(
            lead=lead,
            assigned_to=emp,
            scheduled_time=sched,
            note=random.choice(["Call to discuss proposal", "Reminder for documents", "Follow-up on premium payment", ""]),
            status="done" if sched < now else "pending",
            created_by=admin_user,
        )

# Recompute stages
for lead in leads_created:
    lead.recompute_stage(save=True)
print(f"✓ {len(leads_created)} leads created with family, progress, remarks, follow-ups")

# ─── 6. Calling Lists & Prospects ───────────────────────────────────
list_titles = [
    "Pune MF Prospects - Feb 2026",
    "Health Insurance Leads - Q1",
    "SIP Campaign - January",
    "Corporate Referrals Batch 3",
]
for title in list_titles:
    cl = CallingList.objects.create(title=title, uploaded_by=admin_user)
    # 8-15 prospects per list
    for _ in range(random.randint(8, 15)):
        fname = random.choice(FIRST_NAMES)
        lname = random.choice(LAST_NAMES)
        emp = random.choice(employees)
        status = random.choice(["new", "called", "no_answer", "interested", "not_interested", "follow_up"])
        p = Prospect.objects.create(
            calling_list=cl,
            assigned_to=emp,
            name=f"{fname} {lname}",
            phone=rand_phone(),
            email=f"{fname.lower()}.{lname.lower()}@gmail.com" if random.random() < 0.5 else None,
            status=status,
            date_of_birth=rand_dob() if random.random() < 0.3 else None,
            notes=random.choice(["", "Very interested", "Call back after 5pm", "Wants brochure", "Already has policy"]),
        )
        # Add call records for non-new prospects
        if status != "new":
            for _ in range(random.randint(1, 3)):
                CallRecord.objects.create(
                    prospect=p,
                    employee=emp,
                    outcome=random.choice(["No Answer", "Called Back", "Interested", "Not Interested", "Busy"]),
                    notes=random.choice(["", "Will call back", "Sent details on WhatsApp", "Not interested currently"]),
                    duration_seconds=random.randint(15, 300),
                )

print(f"✓ {CallingList.objects.count()} calling lists, {Prospect.objects.count()} prospects, {CallRecord.objects.count()} call records")

# ─── 7. Calendar Events ─────────────────────────────────────────────
event_titles = [
    "Follow-up call with {name}",
    "Meeting with {name} re: policy review",
    "SIP discussion with {name}",
    "Document collection from {name}",
    "Health insurance proposal for {name}",
    "Portfolio review - {name}",
    "Birthday call - {name}",
    "Premium payment reminder - {name}",
]
events_created = 0
for emp in employees:
    for _ in range(random.randint(5, 12)):
        client = random.choice(clients)
        evt_type = random.choice(["call_followup", "meeting", "task", "reminder"])
        sched = now + datetime.timedelta(days=random.randint(-10, 21), hours=random.randint(9, 17))
        title = random.choice(event_titles).format(name=client.name.split()[0])
        CalendarEvent.objects.create(
            employee=emp,
            client=client,
            title=title,
            type=evt_type,
            scheduled_time=sched,
            end_time=sched + datetime.timedelta(minutes=random.choice([15, 30, 45, 60])),
            status="completed" if sched < now else "pending",
            notes=random.choice(["", "High priority", "Client requested morning slot", "Rescheduled once"]),
        )
        events_created += 1
print(f"✓ {events_created} calendar events")

# ─── 8. Net Business & SIP Entries ──────────────────────────────────
for _ in range(15):
    d = rand_date_in_range(today - datetime.timedelta(days=90), today)
    NetBusinessEntry.objects.create(
        entry_type=random.choice(["sale", "redemption"]),
        amount=Decimal(str(random.randint(50000, 2000000))),
        date=d,
        note=random.choice(["Fresh MF investment", "Lumsum redemption", "SIP conversion", "Partial withdrawal", ""]),
        created_by=admin_user,
    )
for _ in range(12):
    d = rand_date_in_range(today - datetime.timedelta(days=90), today)
    NetSipEntry.objects.create(
        entry_type=random.choice(["fresh", "stopped"]),
        amount=Decimal(str(random.choice([2000, 3000, 5000, 10000, 15000, 20000, 25000]))),
        date=d,
        note=random.choice(["New SIP started", "Client stopped SIP", "SIP increase", "Systematic transfer", ""]),
        created_by=admin_user,
    )
print(f"✓ {NetBusinessEntry.objects.count()} net business, {NetSipEntry.objects.count()} net SIP entries")

# ─── 9. Notifications ───────────────────────────────────────────────
notif_titles = [
    "New sale submitted by {emp}",
    "Sale approved for {client}",
    "Follow-up due tomorrow",
    "New lead assigned to you",
    "Target achievement: 80% of SIP monthly target",
    "Client {client} birthday today",
]
for emp in employees:
    user = emp.user
    for _ in range(random.randint(3, 8)):
        client = random.choice(clients)
        Notification.objects.create(
            recipient=user,
            title=random.choice(notif_titles).format(emp=emp.user.username, client=client.name.split()[0]),
            body=random.choice([
                f"A new SIP sale worth ₹{random.randint(5,50)*1000} has been submitted.",
                f"Sale for {client.name} has been approved by admin.",
                "You have a follow-up scheduled for tomorrow morning.",
                "A new lead has been assigned. Please check your lead dashboard.",
                "Great work! You've hit 80% of your monthly SIP target.",
                f"Today is {client.name}'s birthday. Don't forget to wish!",
            ]),
            link=random.choice(["/clients/dashboard/", "/clients/sales/all/", "/clients/leads/", ""]),
            is_read=random.random() < 0.4,
        )
print(f"✓ {Notification.objects.count()} notifications")

# ─── Summary ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("DATABASE POPULATED SUCCESSFULLY!")
print("=" * 60)
print(f"  Clients:          {Client.objects.count()}")
print(f"  Sales:            {Sale.objects.count()}")
print(f"  Leads:            {Lead.objects.count()}")
print(f"  Calendar Events:  {CalendarEvent.objects.count()}")
print(f"  Calling Lists:    {CallingList.objects.count()}")
print(f"  Prospects:        {Prospect.objects.count()}")
print(f"  Call Records:     {CallRecord.objects.count()}")
print(f"  Net Business:     {NetBusinessEntry.objects.count()}")
print(f"  Net SIP:          {NetSipEntry.objects.count()}")
print(f"  Notifications:    {Notification.objects.count()}")
print(f"  Incentive Rules:  {IncentiveRule.objects.count()}")
print(f"  Targets:          {Target.objects.count()}")
print("=" * 60)
