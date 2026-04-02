from django import forms
from django_select2.forms import ModelSelect2Widget
from django.forms import inlineformset_factory
from .models import Sale, Client, Employee, Lead, LeadFamilyMember, LeadProductProgress, FirmSettings, Renewal, Product


def _is_health_product_name(product_name):
    product = Product.objects.filter(name=(product_name or "").strip()).only("code", "name").first()
    if product:
        return product.code == "HEALTH_INS" or (product.name or "").strip().lower() == "health insurance"
    return (product_name or "").strip().lower() == "health insurance"


def _renewal_type_from_product(product_ref):
    if not product_ref:
        return Renewal.PRODUCT_TYPE_OTHER
    if product_ref.code == "LIFE_INS" or (product_ref.name or "").strip().lower() == "life insurance":
        return Renewal.PRODUCT_TYPE_LIFE
    if product_ref.code == "HEALTH_INS" or (product_ref.name or "").strip().lower() == "health insurance":
        return Renewal.PRODUCT_TYPE_HEALTH
    return Renewal.PRODUCT_TYPE_OTHER


class SalePolicyTypeMixin:
    def _configure_policy_field(self):
        if "policy_type" in self.fields:
            self.fields["policy_type"].required = False
            self.fields["policy_type"].widget = forms.RadioSelect(
                choices=Sale.POLICY_TYPE_CHOICES,
            )

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("product")
        policy_type = (cleaned_data.get("policy_type") or "").strip()

        if _is_health_product_name(product) and not policy_type:
            self.add_error("policy_type", "Select Port or Fresh for Health Insurance.")
        elif not _is_health_product_name(product):
            cleaned_data["policy_type"] = ""

        return cleaned_data

class SaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select())

    class Meta:
        model = Sale
        fields = ["client", "product", "amount", "cover_amount", "policy_type", "date"]
        widgets = {
            "client": ModelSelect2Widget(
                model=Client,
                search_fields=["name__icontains", "phone__icontains", "email__icontains"],
                attrs={"data-placeholder": "Search Client"}
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        employee = kwargs.pop("employee", None)
        super().__init__(*args, **kwargs)
        product_choices = [
            (p.name, p.name)
            for p in Product.objects.filter(is_active=True, archived_at__isnull=True).filter(
                domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]
            )
        ]
        if self.instance and self.instance.pk and self.instance.product:
            existing = self.instance.product
            if existing and all(existing != v for v, _ in product_choices):
                product_choices.append((existing, existing))
        self.fields["product"].choices = product_choices
        self.fields["cover_amount"].required = False
        self._configure_policy_field()


class AdminSaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select())

    class Meta:
        model = Sale
        fields = ["client", "employee", "product", "amount", "cover_amount", "policy_type", "date"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        product_choices = [
            (p.name, p.name)
            for p in Product.objects.filter(is_active=True, archived_at__isnull=True).filter(
                domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]
            )
        ]
        if self.instance and self.instance.pk and self.instance.product:
            existing = self.instance.product
            if existing and all(existing != v for v, _ in product_choices):
                product_choices.append((existing, existing))
        self.fields["product"].choices = product_choices
        if "employee" in self.fields:
            self.fields["employee"].queryset = Employee.objects.filter(active=True)
        self.fields["cover_amount"].required = False
        self._configure_policy_field()

class EditSaleForm(SalePolicyTypeMixin, forms.ModelForm):
    product = forms.ChoiceField(choices=(), widget=forms.Select())

    class Meta:
        model = Sale
        fields = ["product", "amount", "policy_type", "date"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        product_choices = [
            (p.name, p.name)
            for p in Product.objects.filter(is_active=True, archived_at__isnull=True).filter(
                domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]
            )
        ]
        if self.instance and self.instance.pk and self.instance.product:
            existing = self.instance.product
            if existing and all(existing != v for v, _ in product_choices):
                product_choices.append((existing, existing))
        self.fields["product"].choices = product_choices
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

class CallingListUploadForm(forms.Form):
    title = forms.CharField(max_length=255)
    file = forms.FileField()
    daily_calls = forms.IntegerField(
        min_value=1,
        initial=5,
        help_text="Number of calls per employee per day"
    )
    employees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(role="employee", active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple
    )

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
