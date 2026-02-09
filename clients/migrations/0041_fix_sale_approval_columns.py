from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0039_sale_approved_at_sale_approved_by_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE clients_sale ADD COLUMN IF NOT EXISTS status varchar(20) DEFAULT 'pending' NOT NULL;
            ALTER TABLE clients_sale ADD COLUMN IF NOT EXISTS approved_by_id integer NULL;
            ALTER TABLE clients_sale ADD COLUMN IF NOT EXISTS rejection_reason text DEFAULT '' NOT NULL;
            ALTER TABLE clients_sale ADD COLUMN IF NOT EXISTS approved_at timestamp with time zone NULL;
            CREATE INDEX IF NOT EXISTS clients_sale_status_idx ON clients_sale (status);
            """,
            reverse_sql="""-- no-op reverse""",
        ),
    ]
