from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=(("admin", "Admin"), ("employee", "Employee")))

    def __str__(self):
        return self.user.username


class Client(models.Model):
    # Override default id with our own serial number
    id = models.IntegerField(primary_key=True, unique=True, editable=False)

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    pan = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    # Optional date of birth to support Birthday Calls in calendar
    date_of_birth = models.DateField(null=True, blank=True)

    mapped_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)

    # SIP details
    sip_status = models.BooleanField(default=False)
    sip_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    sip_topup = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Lumsum investment (separate from SIP)
    lumsum_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=0)

    # Health Insurance details
    health_status = models.BooleanField(default=False)
    health_cover = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    health_topup = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    health_product = models.CharField(max_length=200, blank=True, null=True)

    # Life Insurance details
    life_status = models.BooleanField(default=False)
    life_cover = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    life_product = models.CharField(max_length=200, blank=True, null=True)

    # Motor Insurance details
    motor_status = models.BooleanField(default=False)
    motor_insured_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    motor_product = models.CharField(max_length=200, blank=True, null=True)

    # PMS details
    pms_status = models.BooleanField(default=False)
    pms_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pms_start_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, default="Unmapped")
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    edited_by = models.ForeignKey('Employee', null=True, blank=True, on_delete=models.SET_NULL, related_name='edited_clients')

    def __str__(self):
        return f"{self.id} - {self.name}"

    def save(self, *args, **kwargs):
        # Auto-generate sequential id if not set
        is_new = self._state.adding
        if self.id is None:
            with transaction.atomic():
                max_id = Client.objects.aggregate(max_id=Max('id'))['max_id'] or 0
                self.id = max_id + 1
        else:
            # Ensure edited_at is set at least once after the first edit so
            # the "Show Edited" filter can surface historical edits.
            if self.edited_at is None and not is_new:
                self.edited_at = timezone.now()
        # Normalize lumsum investment to 0 if missing
        if self.lumsum_investment is None:
            self.lumsum_investment = Decimal("0.00")
        super().save(*args, **kwargs)
    
    def reassign_to(self, new_employee, changed_by=None, note=''):
        """
        Atomically reassign this client to `new_employee` and create an audit entry.

        Returns:
            (changed: bool, previous_employee, new_employee)
        """
        previous = self.mapped_to
        if previous == new_employee:
            return False, previous, new_employee

        with transaction.atomic():
            self.mapped_to = new_employee
            self.save(update_fields=['mapped_to'])

            # create audit entry
            ClientMappingAudit.objects.create(
                client=self,
                previous_employee=previous,
                new_employee=new_employee,
                changed_by=changed_by,
                changed_at=timezone.now(),
                note=note or ''
            )

        return True, previous, new_employee



class ClientMappingAudit(models.Model):
    client = models.ForeignKey('Client', on_delete=models.CASCADE, related_name='mapping_audits')
    previous_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    new_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"Client {self.client_id}: {self.previous_employee} → {self.new_employee} at {self.changed_at}"



# Add these imports at top if not already present
from decimal import Decimal
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

# ---------- IncentiveRule (configurable in admin) ----------
class IncentiveRule(models.Model):
    PRODUCT_CHOICES = [
        ("SIP", "SIP"),
        ("Lumsum", "Lumsum"),
        ("Life Insurance", "Life Insurance"),
        ("Health Insurance", "Health Insurance"),
        ("Motor Insurance", "Motor Insurance"),
        ("PMS", "PMS"),
    ]

    product = models.CharField(max_length=50, choices=PRODUCT_CHOICES, unique=True)
    unit_amount = models.DecimalField(max_digits=14, decimal_places=2,
                                      help_text="Base unit (e.g., 1000 or 100000)")
    points_per_unit = models.DecimalField(max_digits=12, decimal_places=3,
                                          help_text="Points awarded per unit_amount")
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Incentive Rule"
        verbose_name_plural = "Incentive Rules"

    def __str__(self):
        return f"{self.product}: {self.points_per_unit} pts per {self.unit_amount}"



from django.db import models
from decimal import Decimal

from django.db import models
from django.utils import timezone
from decimal import Decimal


class Sale(models.Model):
    PRODUCT_CHOICES = [
        ("SIP", "SIP"),
        ("Lumsum", "Lumsum"),
        ("Life Insurance", "Life Insurance"),
        ("Health Insurance", "Health Insurance"),
        ("Motor Insurance", "Motor Insurance"),
        ("PMS", "PMS"),
    ]

    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="sales")
    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="sales")
    product = models.CharField(max_length=50, choices=PRODUCT_CHOICES)

    # Business value (used for incentive calculation / points)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    # New: Cover amount (only relevant for Life & Health Insurance)
    cover_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    date = models.DateField(default=timezone.now)   # not auto_now_add
    points = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    incentive_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def compute_points(self):
        """Compute points based on IncentiveRule in DB"""
        from .models import IncentiveRule  # avoid circular import

        try:
            rule = IncentiveRule.objects.get(product=self.product, active=True)
            if rule.unit_amount > 0:
                self.points = (self.amount / rule.unit_amount) * rule.points_per_unit
                self.incentive_amount = self.points  # You can later define ₹ conversion
        except IncentiveRule.DoesNotExist:
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")

    def save(self, *args, **kwargs):
        self.compute_points()  # always compute before saving
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client} - {self.product} - ₹{self.amount}"



class MonthlyIncentive(models.Model):
    """
    Snapshot of total points and total sales amount for each employee for a given year+month.
    """
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='monthly_incentives')
    year = models.IntegerField()
    month = models.IntegerField()
    total_points = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal('0.000'))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"{self.employee} - {self.year}-{str(self.month).zfill(2)} : {self.total_points} pts"



class Target(models.Model):
    TARGET_TYPE_CHOICES = [
        ("daily", "Daily"),
        ("monthly", "Monthly"),
    ]

    product = models.CharField(max_length=50, choices=Sale.PRODUCT_CHOICES)
    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("product", "target_type")  # ✅ one daily + one monthly per product

    def __str__(self):
        return f"{self.product} ({self.target_type})"


class BusinessTarget(models.Model):
    metric = models.CharField(max_length=100)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unit = models.CharField(max_length=50, blank=True, default="")
    start_date = models.DateField()
    end_date = models.DateField()
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-end_date", "-created_at"]

    def __str__(self):
        return f"{self.metric} {self.start_date}–{self.end_date}"


class MonthlyTargetHistory(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    product = models.CharField(max_length=50)
    year = models.IntegerField()
    month = models.IntegerField()
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    achieved_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    points_value = models.IntegerField(default=0)  # ✅ new field

    class Meta:
        unique_together = ("employee", "product", "year", "month")

    def __str__(self):
        return f"{self.employee} - {self.product} ({self.month}/{self.year})"


class Redemption(models.Model):
    """Manual adjustment entries not linked to any customer.

    Used for lumsum redemptions and SIP stoppage records. Managers can add
    these to adjust net business calculations.
    """
    TYPE_CHOICES = [
        ("redemption", "Redemption (Lumsum)"),
        ("sip_stoppage", "SIP Stoppage"),
    ]

    product = models.CharField(max_length=50, choices=Sale.PRODUCT_CHOICES)
    entry_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="redemption")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.get_entry_type_display()} - {self.product} : ₹{self.amount} on {self.date}"


from django.conf import settings
# Calling and Calender
# Prospect status choices
STATUS_CHOICES = [
    ("new", "Not Called"),
    ("called", "Called"),
    ("no_answer", "No Answer"),
    ("interested", "Interested"),
    ("not_interested", "Not Interested"),
    ("busy", "Busy"),
    ("wrong_number", "Wrong Number"),
    ("follow_up", "Follow-up"),
]

class CallingList(models.Model):
    title = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
class Prospect(models.Model):
    calling_list = models.ForeignKey(CallingList, related_name="prospects", on_delete=models.CASCADE)
    assigned_to = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15)
    
    # ✅ Add these if you want to store them
    email = models.EmailField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    # Optional DOB for prospects (used to schedule birthday calls)
    date_of_birth = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    created_at = models.DateTimeField(auto_now_add=True)

class CallRecord(models.Model):
    prospect = models.ForeignKey(Prospect, on_delete=models.CASCADE, related_name="call_records")
    employee = models.ForeignKey("Employee", on_delete=models.CASCADE)
    call_time = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=50)  # e.g. "No Answer", "Interested"
    notes = models.TextField(blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)



class CalendarEvent(models.Model):
    EVENT_TYPES = [
        ("call_followup", "Call Follow-up"),
        ("meeting", "Meeting"),
        ("task", "Task"),
        ("reminder", "Reminder"),
    ]

    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="calendar_events")
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="calendar_events")  # ✅ NEW
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=EVENT_TYPES, default="task")
    related_prospect = models.ForeignKey("Prospect", on_delete=models.SET_NULL, null=True, blank=True)
    scheduled_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    reminder_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("completed", "Completed"),
            ("rescheduled", "Rescheduled"),
            ("skipped", "Skipped")
        ],
        default="pending"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.employee})"


from django.db import models
from django.conf import settings
from django.template import Template, Context
from django.utils import timezone

# adjust the Employee import/path if your project uses a different app/model
# from employees.models import Employee
from django.template import Template, Context
from django.utils.html import strip_tags

class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    content = models.TextField(help_text="Use any placeholders from the Client model like {{ name }}, {{ phone }}, {{ sip_amount }} etc.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def render(self, obj, extra_context=None):
        """
        Render the message with any placeholders present in 'content'.
        Automatically maps object attributes (e.g. from Client).
        """
        # Convert model instance to dict of all attributes
        context_data = {}

        # Add all field names + values from model (safe reflection)
        for field in obj._meta.get_fields():
            try:
                val = getattr(obj, field.name, "")
                # handle related fields (like mapped_to.user.username)
                if hasattr(val, "username"):
                    val = val.username
                context_data[field.name] = val
            except Exception:
                continue

        # Merge any extra values
        if extra_context:
            context_data.update(extra_context)

        # Render safely
        try:
            template = Template(self.content)
            context = Context(context_data)
            rendered = template.render(context)
            return strip_tags(rendered).strip()
        except Exception as e:
            print("Render error:", e)
            return self.content


class MessageLog(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    client = models.ForeignKey('Client', null=True, blank=True, on_delete=models.SET_NULL)
    recipient_phone = models.CharField(max_length=32)
    message_text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Message to {self.recipient_phone} [{self.status}]"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    related_sale = models.ForeignKey(
        "Sale", null=True, blank=True, on_delete=models.CASCADE
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} -> {self.recipient}"
