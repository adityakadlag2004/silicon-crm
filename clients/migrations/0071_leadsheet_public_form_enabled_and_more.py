"""Add public lead-capture form fields to LeadSheet + per-column visibility flag.

`public_token` is the constant URL-safe identifier for each sheet's public
form. It must be unique, so we add it in three steps to support Postgres:
nullable column, per-row UUID backfill, then unique+not-null.
"""
import uuid

from django.db import migrations, models


def _backfill_tokens(apps, schema_editor):
    LeadSheet = apps.get_model("clients", "LeadSheet")
    for sheet in LeadSheet.objects.filter(public_token__isnull=True):
        sheet.public_token = uuid.uuid4()
        sheet.save(update_fields=["public_token"])


def _drop_tokens(apps, schema_editor):
    LeadSheet = apps.get_model("clients", "LeadSheet")
    LeadSheet.objects.update(public_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0070_alter_mfsnapshot_closing_aum_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="leadsheet",
            name="public_form_enabled",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="leadsheet",
            name="public_form_intro",
            field=models.TextField(blank=True, default="",
                help_text="Optional short message shown above the form (e.g. 'We'll get back to you within 24h')."),
        ),
        migrations.AddField(
            model_name="leadsheet",
            name="public_form_success_message",
            field=models.CharField(blank=True, default="", max_length=400,
                help_text="Message shown after a successful submission. Defaults to a generic thank-you."),
        ),
        migrations.AddField(
            model_name="leadsheet",
            name="public_form_title",
            field=models.CharField(blank=True, default="", max_length=200,
                help_text="Heading shown on the public form. Defaults to the sheet name."),
        ),
        # public_token: nullable, then backfilled per row, then unique+not-null.
        migrations.AddField(
            model_name="leadsheet",
            name="public_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(_backfill_tokens, _drop_tokens),
        migrations.AlterField(
            model_name="leadsheet",
            name="public_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
        migrations.AddField(
            model_name="leadsheetcolumn",
            name="show_on_public_form",
            field=models.BooleanField(default=False,
                help_text="Expose this column on the sheet's public lead-capture form."),
        ),
    ]
