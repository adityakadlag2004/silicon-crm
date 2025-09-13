from django import forms
from .models import Sale, Client


class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["client", "product", "amount", "cover_amount", "date"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        employee = kwargs.pop("employee", None)
        super().__init__(*args, **kwargs)
        # Hide cover_amount by default, show only for insurance products (JS will handle in template)
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

