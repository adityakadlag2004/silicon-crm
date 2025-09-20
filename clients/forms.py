from django import forms
from .models import Sale, Client


from django import forms
from django_select2.forms import ModelSelect2Widget
from .models import Sale, Client

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

