"""Permanently remove the Mutual Funds / RTA-feed module.

Owner's call 2026-09-02: the CAMS/KFintech feed, the folios, transactions and
the SIP register it built were not used, so the whole module goes — models,
screens, cron and the Product.rta_match flag that fed the sale cross-check.

This DROPS the tables and their data. Run scripts/backup_db.sh first.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0125_renewal_policy_doc_submitted'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mutualfundfolio',
            name='arn',
        ),
        migrations.RemoveField(
            model_name='sipregistration',
            name='arn',
        ),
        migrations.RemoveField(
            model_name='mutualfundtransaction',
            name='arn',
        ),
        migrations.AlterUniqueTogether(
            name='mutualfundfolio',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='mutualfundfolio',
            name='client',
        ),
        migrations.RemoveField(
            model_name='sipregistration',
            name='folio',
        ),
        migrations.RemoveField(
            model_name='mutualfundtransaction',
            name='folio',
        ),
        migrations.RemoveField(
            model_name='mutualfundtransaction',
            name='source_import',
        ),
        migrations.RemoveField(
            model_name='rtafeedimport',
            name='uploaded_by',
        ),
        migrations.RemoveField(
            model_name='sipregistration',
            name='source_import',
        ),
        migrations.RemoveField(
            model_name='sipregistration',
            name='client',
        ),
        migrations.RemoveField(
            model_name='product',
            name='rta_match',
        ),
        migrations.DeleteModel(
            name='ArnAccount',
        ),
        migrations.DeleteModel(
            name='MutualFundFolio',
        ),
        migrations.DeleteModel(
            name='MutualFundTransaction',
        ),
        migrations.DeleteModel(
            name='RTAFeedImport',
        ),
        migrations.DeleteModel(
            name='SipRegistration',
        ),
    ]
