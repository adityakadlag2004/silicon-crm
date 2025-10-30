from django import forms
from .models import Sale, Client


from django import forms
from django_select2.forms import ModelSelect2Widget
from .models import Sale, Client, Employee

class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["client", "product", "amount", "cover_amount", "date"]
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
        self.fields["cover_amount"].required = False


class AdminSaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["client", "employee", "product", "amount", "cover_amount", "date"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

class EditSaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["product", "amount", "date"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

class CallingListUploadForm(forms.Form):
    title = forms.CharField(max_length=255)
    file = forms.FileField()
    daily_calls = forms.IntegerField(
        min_value=1,
        initial=5,
        help_text="Number of calls per employee per day"
    )
    employees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(role="employee"),
        required=False,
        widget=forms.CheckboxSelectMultiple
    )

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "name", "email", "phone", "pan", "address", "mapped_to",
            "sip_status", "sip_amount", "sip_topup",
            "life_status", "life_cover", "life_product",
            "health_status", "health_cover", "health_topup", "health_product",
            "motor_status", "motor_insured_value", "motor_product",
            "pms_status", "pms_amount", "pms_start_date",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "pms_start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})
        # optional: uppercase PAN
        if "pan" in self.fields:
            self.fields["pan"].widget.attrs["style"] = "text-transform: uppercase;"
