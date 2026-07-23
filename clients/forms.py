import re

from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django_select2.forms import ModelSelect2Widget
from django.forms import inlineformset_factory
from .models import Sale, Client, Employee, Lead, LeadFamilyMember, LeadProductProgress, FirmSettings, Renewal, Product, PlanPptRate

# Indian PAN: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
PAN_RE = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")


def validate_pan(raw, required=False):
    """Normalize + validate a PAN. Returns the cleaned value (or None when
    blank and not required); raises forms.ValidationError otherwise."""
    value = re.sub(r"\s+", "", (raw or "")).upper()
    if not value:
        if required:
            raise forms.ValidationError("PAN is required — it links the client to their mutual fund folios.")
        return None
    if not PAN_RE.fullmatch(value):
        raise forms.ValidationError("Enter a valid PAN (format: ABCDE1234F).")
    return value


def _sale_products():
    return (
        Product.objects.filter(is_active=True, archived_at__isnull=True)
        .filter(domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH])
        .select_related("parent")
        .order_by("display_order", "name")
    )


def _main_product_choices(instance=None):
    """First dropdown: top-level products (categories + standalone)."""
    products = list(_sale_products())
    top = [p for p in products if not p.parent_id]
    choices = [(p.name, p.name) for p in top]
    listed = {p.name for p in top}
    # On edit the saved product may be a sub-product — show its category as the
    # selected main; also keep a since-removed value selectable.
    existing = getattr(instance, "product", "") if instance and instance.pk else ""
    if existing:
        saved = next((p for p in products if p.name == existing), None)
        main = saved.parent.name if (saved and saved.parent_id) else existing
        if main not in listed:
            choices.append((main, main))
            listed.add(main)
    return choices


def _subproduct_choices():
    """Second dropdown: every sub-product. The JS filters to the chosen main
    and clean() enforces the link, so all children are valid choices here."""
    return [(p.name, p.name) for p in _sale_products() if p.parent_id]


def _product_children_map():
    """{main product name: [sub-product name, ...]} — drives the cascade JS."""
    m = {}
    for p in _sale_products():
        if p.parent_id:
            m.setdefault(p.parent.name, []).append(p.name)
    return m


def _ppt_choices():
    """All PPT values that appear on any plan, in chart order. The JS narrows
    this to the chosen plan's own PPTs; clean() validates the final pick."""
    from .models.catalog import _ppt_sort_key
    vals = sorted(
        set(PlanPptRate.objects.values_list("ppt", flat=True)),
        key=lambda p: _ppt_sort_key(type("R", (), {"ppt": p})()),
    )
    return [("", "— Select PPT —")] + [(v, f"PPT {v}") for v in vals]


def _ppt_options_map():
    """{sub-product name: [ppt, ...]} for the PPT dropdown cascade. Advisor and
    MDRT share the same PPT set, so one list per plan suffices."""
    m = {}
    for r in PlanPptRate.objects.filter(designation=PlanPptRate.DESIG_ADVISOR).select_related("product"):
        m.setdefault(r.product.name, []).append(r.ppt)
    from .models.catalog import _ppt_sort_key
    return {name: sorted(ppts, key=lambda p: _ppt_sort_key(type("R", (), {"ppt": p})()))
            for name, ppts in m.items()}


def _init_product_fields(form):
    """Wire the two-step product picker on a sale form and, on edit, split an
    already-saved sub-product back into (main = category, subproduct = child)."""
    form.fields["product"].choices = _main_product_choices(form.instance)
    form.fields["subproduct"].choices = _subproduct_choices()
    if "ppt" in form.fields:
        form.fields["ppt"].choices = _ppt_choices()

    inst = form.instance
    saved = (
        Product.objects.filter(name=inst.product).select_related("parent").first()
        if inst and inst.pk and inst.product else None
    )
    if saved and saved.parent_id:
        form.initial["product"] = saved.parent.name
        form.initial["subproduct"] = saved.name


def _is_health_product_name(product_name):
    product = Product.objects.filter(name=(product_name or "").strip()).select_related("parent").first()
    if product:
        return product.is_health
    return (product_name or "").strip().lower() == "health insurance"


def _renewal_type_from_product(product_ref):
    if not product_ref:
        return Renewal.PRODUCT_TYPE_OTHER
    if product_ref.code == "LIFE_INS" or (product_ref.name or "").strip().lower() == "life insurance":
        return Renewal.PRODUCT_TYPE_LIFE
    if product_ref.code == "HEALTH_INS" or (product_ref.name or "").strip().lower() == "health insurance":
        return Renewal.PRODUCT_TYPE_HEALTH
    return Renewal.PRODUCT_TYPE_OTHER


def _is_insurance_product_name(product_name):
    """Health or Life insurance — the products that need a policy date."""
    product = Product.objects.filter(name=(product_name or "").strip()).select_related("parent").first()
    if product:
        return product.is_insurance
    return (product_name or "").strip().lower() in {"health insurance", "life insurance"}


class SalePolicyTypeMixin:
    def _configure_policy_field(self):
        if "policy_type" in self.fields:
            self.fields["policy_type"].required = False
            self.fields["policy_type"].widget = forms.RadioSelect(
                choices=Sale.POLICY_TYPE_CHOICES,
            )
        # policy_date / policy_number are optional at the field level
        # (non-insurance sales have neither); clean() makes them mandatory for
        # Health/Life insurance.
        if "policy_date" in self.fields:
            self.fields["policy_date"].required = False
            self.fields["policy_date"].help_text = (
                "Read the policy commencement date from the policy document — "
                "not the sale date. This drives the annual renewal reminder."
            )
        if "policy_number" in self.fields:
            self.fields["policy_number"].required = False
            self.fields["policy_number"].widget.attrs.update(
                {"class": "form-control", "placeholder": "e.g. INS76123499"})
            self.fields["policy_number"].help_text = (
                "The insurer's policy number from the document. Links this sale "
                "to its policy on the Insurance Tracker."
            )

    def clean(self):
        cleaned_data = super().clean()

        # Two-step product picker: when the chosen main product has sub-products,
        # a sub-product is mandatory and becomes the effective product sold (so
        # margin/analytics read at the sub-product level). Mains with no
        # sub-products behave as before.
        if "subproduct" in self.fields:
            main = (cleaned_data.get("product") or "").strip()
            sub = (cleaned_data.get("subproduct") or "").strip()
            main_prod = Product.objects.filter(name=main).first() if main else None
            child_names = (
                set(main_prod.children.values_list("name", flat=True)) if main_prod else set()
            )
            if child_names:
                if not sub:
                    self.add_error("subproduct", "Select a sub-product.")
                elif sub not in child_names:
                    self.add_error("subproduct", "That sub-product doesn't belong to the selected product.")
                else:
                    cleaned_data["product"] = sub  # effective product sold
            else:
                cleaned_data["subproduct"] = ""

        # PPT is mandatory for PPT-priced plans (the FYC margin is read off it)
        # and meaningless for everything else.
        if "ppt" in self.fields:
            eff = Product.objects.filter(name=cleaned_data.get("product")).first()
            ppt = (cleaned_data.get("ppt") or "").strip()
            if eff and eff.has_ppt_rates:
                valid = set(eff.ppt_rates.values_list("ppt", flat=True))
                if not ppt:
                    self.add_error("ppt", "Select the Premium Paying Term for this plan.")
                elif ppt not in valid:
                    self.add_error("ppt", "That PPT isn't available for the selected plan.")
            else:
                cleaned_data["ppt"] = ""

        product = cleaned_data.get("product")
        policy_type = (cleaned_data.get("policy_type") or "").strip()

        if _is_health_product_name(product) and not policy_type:
            self.add_error("policy_type", "Select Port or Fresh for Health Insurance.")
        elif not _is_health_product_name(product):
            cleaned_data["policy_type"] = ""

        # Policy date is mandatory for insurance (the sale date is the approval
        # day, not when the policy actually starts), and meaningless otherwise.
        if "policy_date" in self.fields:
            if _is_insurance_product_name(product):
                if not cleaned_data.get("policy_date"):
                    self.add_error("policy_date",
                                   "Enter the policy date from the policy document.")
            else:
                cleaned_data["policy_date"] = None
        if "policy_number" in self.fields:
            if _is_insurance_product_name(product):
                if not (cleaned_data.get("policy_number") or "").strip():
                    self.add_error("policy_number",
                                   "Enter the policy number from the policy document.")
            else:
                cleaned_data["policy_number"] = ""

        return cleaned_data

class SaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select(), label="Product")
    subproduct = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="Sub-product")
    ppt = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="PPT")

    class Meta:
        model = Sale
        fields = ["client", "product", "ppt", "amount", "cover_amount", "policy_type", "date", "policy_date", "policy_number"]
        widgets = {
            "client": ModelSelect2Widget(
                model=Client,
                search_fields=["name__icontains", "phone__icontains", "email__icontains"],
                attrs={"data-placeholder": "Search Client"}
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
            "policy_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        employee = kwargs.pop("employee", None)
        super().__init__(*args, **kwargs)
        _init_product_fields(self)
        self.fields["cover_amount"].required = False
        self._configure_policy_field()


class AdminSaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select(), label="Product")
    subproduct = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="Sub-product")
    ppt = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="PPT")

    class Meta:
        model = Sale
        fields = ["client", "employee", "product", "ppt", "amount", "cover_amount", "policy_type", "date", "policy_date", "policy_number"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "policy_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _init_product_fields(self)
        if "employee" in self.fields:
            self.fields["employee"].queryset = Employee.objects.filter(active=True)
            # Non-admins don't submit an employee (the field is hidden for them);
            # the view assigns their own employee record. Keep it optional so the
            # form validates for employees/managers, not just admins.
            self.fields["employee"].required = False
        self.fields["cover_amount"].required = False
        self._configure_policy_field()

class EditSaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select(), label="Product")
    subproduct = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="Sub-product")
    ppt = forms.ChoiceField(choices=(), required=False, widget=forms.Select(), label="PPT")

    class Meta:
        model = Sale
        fields = ["product", "ppt", "amount", "policy_type", "date", "policy_date", "policy_number"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "policy_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _init_product_fields(self)
        self._configure_policy_field()


class RenewalForm(forms.ModelForm):
    product_ref = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=True,
        label="Product",
    )

    class Meta:
        model = Renewal
        fields = [
            "client",
            "employee",
            "product_ref",
            "product_name",
            "renewal_date",
            "renewal_end_date",
            "frequency",
            "premium_amount",
            "premium_collected_on",
            "notes",
        ]
        widgets = {
            "renewal_date": forms.DateInput(attrs={"type": "date"}),
            "renewal_end_date": forms.DateInput(attrs={"type": "date"}),
            "premium_collected_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].required = False
        self.fields["employee"].required = False
        self.fields["employee"].queryset = Employee.objects.filter(active=True)
        self.fields["product_ref"].queryset = Product.objects.filter(is_active=True, archived_at__isnull=True).filter(
            domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH]
        )

    def clean(self):
        cleaned_data = super().clean()
        product_ref = cleaned_data.get("product_ref")
        product_name = (cleaned_data.get("product_name") or "").strip()
        product_type = _renewal_type_from_product(product_ref)
        cleaned_data["product_type"] = product_type
        if product_type in (Renewal.PRODUCT_TYPE_LIFE, Renewal.PRODUCT_TYPE_HEALTH):
            cleaned_data["product_name"] = None
        else:
            if not product_name:
                cleaned_data["product_name"] = product_ref.name if product_ref else None
        return cleaned_data


class EditRenewalForm(forms.ModelForm):
    product_ref = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=True,
        label="Product",
    )

    class Meta:
        model = Renewal
        fields = [
            "employee",
            "product_ref",
            "product_name",
            "renewal_date",
            "renewal_end_date",
            "frequency",
            "premium_amount",
            "premium_collected_on",
            "notes",
        ]
        widgets = {
            "renewal_date": forms.DateInput(attrs={"type": "date"}),
            "renewal_end_date": forms.DateInput(attrs={"type": "date"}),
            "premium_collected_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].required = False
        self.fields["employee"].queryset = Employee.objects.filter(active=True)
        product_qs = Product.objects.filter(is_active=True, archived_at__isnull=True).filter(
            domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH]
        )
        if self.instance and self.instance.pk and self.instance.product_ref_id:
            product_qs = Product.objects.filter(pk=self.instance.product_ref_id) | product_qs
        self.fields["product_ref"].queryset = product_qs.distinct()
        if self.instance and self.instance.pk and self.instance.product_ref_id:
            self.fields["product_ref"].initial = self.instance.product_ref

    def clean(self):
        cleaned_data = super().clean()
        product_ref = cleaned_data.get("product_ref")
        product_name = (cleaned_data.get("product_name") or "").strip()
        product_type = _renewal_type_from_product(product_ref)
        cleaned_data["product_type"] = product_type
        if product_type in (Renewal.PRODUCT_TYPE_LIFE, Renewal.PRODUCT_TYPE_HEALTH):
            cleaned_data["product_name"] = None
        else:
            if not product_name:
                cleaned_data["product_name"] = product_ref.name if product_ref else None
        return cleaned_data

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "name", "email", "phone", "pan", "address", "mapped_to",
            "sip_status", "sip_amount", "sip_topup",
            "lumsum_investment",
            "life_status", "life_cover", "life_product",
            "health_status", "health_cover", "health_topup", "health_product",
            "motor_status", "motor_insured_value", "motor_product",
            "pms_status", "pms_amount", "pms_start_date",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "pms_start_date": forms.DateInput(attrs={"type": "date"}),
            "lumsum_investment": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_add_flow = not getattr(self.instance, "pk", None)
        if is_add_flow:
            self.fields["name"].required = True
            self.fields["phone"].required = True
            self.fields["email"].required = True
            self.fields["pan"].required = True
        if "mapped_to" in self.fields:
            self.fields["mapped_to"].queryset = Employee.objects.filter(active=True)
        for name, field in self.fields.items():
            widget = field.widget
            # Style checkboxes distinctly so they stay visible
            if getattr(widget, "input_type", "") == "checkbox":
                widget.attrs["class"] = "form-check-input"
                widget.attrs.pop("style", None)
            else:
                widget.attrs["class"] = "form-control"
                if name == "lumsum_investment":
                    widget.attrs.setdefault("step", "0.01")
                    widget.attrs.setdefault("placeholder", "0")
        # optional: uppercase PAN
        if "pan" in self.fields:
            self.fields["pan"].widget.attrs["style"] = "text-transform: uppercase;"

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip().upper()
        if not value:
            raise forms.ValidationError("Client name is required.")
        return value

    def clean_phone(self):
        value = (self.cleaned_data.get("phone") or "").strip().upper()
        if not getattr(self.instance, "pk", None) and not value:
            raise forms.ValidationError("Phone is required.")
        return value

    def clean_pan(self):
        # Required for NEW clients (RTA folio matching runs on PAN); existing
        # clients without one are surfaced on the KYC Issues screen instead of
        # blocking every edit.
        return validate_pan(self.cleaned_data.get("pan"),
                            required=not getattr(self.instance, "pk", None))

    def clean_email(self):
        value = (self.cleaned_data.get("email") or "").strip().upper()
        if not getattr(self.instance, "pk", None) and not value:
            raise forms.ValidationError("Email is required.")
        return value

    def clean_lumsum_investment(self):
        val = self.cleaned_data.get("lumsum_investment")
        # Default to 0.00 if left blank
        if val is None:
            return 0
        return val

class ClientReassignForm(forms.Form):
    new_employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(active=True),
        required=False,
        empty_label="-- Unassign --",
        label="Assign to"
    )
    note = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)


class EmployeeCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=(("admin", "Admin"), ("manager", "Manager"), ("employee", "Employee")))
    salary = forms.DecimalField(max_digits=12, decimal_places=2, initial=0)

    def clean_username(self):
        from django.contrib.auth.models import User

        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists")
        return username


class EmployeeDeactivateForm(forms.Form):
    employee_id = forms.IntegerField()


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = [
            "customer_name",
            "phone",
            "email",
            "data_received",
            "data_received_on",
            "income",
            "expenses",
            "notes",
            "assigned_to",
            "stage",
        ]
        widgets = {
            "data_received_on": forms.DateInput(attrs={"type": "date"}),
            "income": forms.NumberInput(attrs={"step": "0.01"}),
            "expenses": forms.NumberInput(attrs={"step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = Employee.objects.filter(active=True)
        self.fields["stage"].widget = forms.HiddenInput()

        if user and hasattr(user, "employee") and getattr(user.employee, "role", "") == "employee":
            self.fields["assigned_to"].initial = user.employee
            self.fields["assigned_to"].disabled = True

        for name, field in self.fields.items():
            widget = field.widget
            if getattr(widget, "input_type", "") == "checkbox":
                widget.attrs.setdefault("class", "form-check-input")
            else:
                widget.attrs.setdefault("class", "form-control")


class LeadFamilyMemberForm(forms.ModelForm):
    class Meta:
        model = LeadFamilyMember
        fields = ["name", "relation", "date_of_birth", "notes"]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class LeadProductProgressForm(forms.ModelForm):
    class Meta:
        model = LeadProductProgress
        fields = ["product", "target_amount", "achieved_amount", "status", "remark"]
        widgets = {
            "remark": forms.Textarea(attrs={"rows": 2}),
        }


LeadFamilyMemberFormSet = inlineformset_factory(
    Lead,
    LeadFamilyMember,
    form=LeadFamilyMemberForm,
    extra=1,
    can_delete=True,
)

LeadProductProgressFormSet = inlineformset_factory(
    Lead,
    LeadProductProgress,
    form=LeadProductProgressForm,
    extra=3,
    can_delete=True,
)


class FirmSettingsForm(forms.ModelForm):
    class Meta:
        model = FirmSettings
        fields = [
            "firm_name",
            "address",
            "email",
            "phone",
            "website",
            "logo",
            "primary_color",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "website": forms.URLInput(attrs={"placeholder": "https://example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if getattr(field.widget, "input_type", "") == "checkbox":
                field.widget.attrs.setdefault("class", "form-check-input")
            elif name == "primary_color":
                field.widget.attrs.setdefault("class", "form-control form-control-color")
            elif name == "website":
                field.widget.attrs.setdefault("class", "form-control")
                # Update help text to clarify URL format requirement
                field.help_text = "Include https:// or http:// (e.g., https://example.com)"
            else:
                field.widget.attrs.setdefault("class", "form-control")


class MyProfileForm(forms.ModelForm):
    """What an employee may edit about themselves.

    Deliberately excludes role, salary, employee_number, joining_date and
    position — those are the admin's to set, and letting people edit their own
    would make the record untrustworthy.
    """

    class Meta:
        model = Employee
        fields = [
            "first_name", "middle_name", "last_name", "date_of_birth",
            "personal_email", "phone", "address", "marital_status",
            "qualification", "skills",
            "emergency_contact_name", "emergency_contact_phone",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "skills": forms.TextInput(attrs={"placeholder": "MFD, NISM-VA, Excel"}),
        }
        labels = {
            "personal_email": "Personal email",
            "marital_status": "Marital status",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css)
        # Nudge the fields that drive the completeness prompt.
        for name in ("first_name", "last_name", "date_of_birth", "phone"):
            self.fields[name].required = True


class MyPasswordForm(PasswordChangeForm):
    """Django's own change-password form, Bootstrap-dressed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class MyLoginIdForm(forms.ModelForm):
    """Employees fix their own login id — some were typed wrong at setup.
    ModelForm brings User's uniqueness check and username validator along."""

    class Meta:
        model = User
        fields = ["username"]
        labels = {"username": "Login ID"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.setdefault("class", "form-control")


class EmployeeAdminForm(forms.ModelForm):
    """The admin's view of a team member — the employment facts."""

    class Meta:
        model = Employee
        fields = [
            "first_name", "middle_name", "last_name", "date_of_birth",
            "personal_email", "phone", "address", "marital_status",
            "role", "position", "domain", "joining_date", "reports_to",
            "salary", "employee_number",
            "qualification", "skills",
            "emergency_contact_name", "emergency_contact_phone", "notes",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "joining_date": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css)
        # Nobody reports to themselves, and inactive staff aren't managers.
        qs = Employee.objects.filter(active=True)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["reports_to"].queryset = qs.select_related("user")
        self.fields["reports_to"].required = False


class ClaimForm(forms.ModelForm):
    """Raise or edit a claim. The policy is set by the view (from the policy
    page or a picker), so it isn't a form field here."""

    class Meta:
        from .models import InsuranceClaim
        model = InsuranceClaim
        fields = [
            "claim_type", "claim_mode", "status",
            "intimation_date", "admission_date", "submission_date", "settlement_date",
            "claimed_amount", "settled_amount", "settlement_details", "handled_by",
        ]
        widgets = {
            "intimation_date": forms.DateInput(attrs={"type": "date"}),
            "admission_date": forms.DateInput(attrs={"type": "date"}),
            "submission_date": forms.DateInput(attrs={"type": "date"}),
            "settlement_date": forms.DateInput(attrs={"type": "date"}),
            "settlement_details": forms.Textarea(attrs={"rows": 2}),
            "claim_type": forms.TextInput(attrs={"placeholder": "e.g. Hospitalisation Claim"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["handled_by"].queryset = Employee.objects.filter(active=True).select_related("user")
        self.fields["handled_by"].required = False
        self.fields["claim_type"].required = True
        for name, field in self.fields.items():
            css = "form-select" if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) else "form-control"
            field.widget.attrs.setdefault("class", css)
