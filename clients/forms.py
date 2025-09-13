from django import forms
from .models import Sale, Client


class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["client", "product", "amount", "date"]  # include date for add sale
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        employee = kwargs.pop("employee", None)
        super().__init__(*args, **kwargs)

        if employee and employee.role == "employee":
            self.fields["client"].queryset = Client.objects.all()
        else:
            self.fields["client"].queryset = Client.objects.all()


class AdminSaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["client", "employee", "product", "amount", "date"]
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

