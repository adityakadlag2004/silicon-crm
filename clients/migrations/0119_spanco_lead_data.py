"""Carry every existing lead into SPANCO, then drop the old product grid.

**Every existing lead enters at Suspect** and is re-qualified under the new
method — the owner's call on 2026-08-13, and a defensible one: the old
pending / half-sold / processed value was derived from what had been sold,
not from a judgement about the relationship, so it is not a SPANCO position
worth carrying forward.

Nothing is lost even so. Where a lead stood before is written into its first
stage event ("Previously: Half Sold"), and every product row that carried
real information becomes a LeadInterest whose note spells out the old target
/ achieved / status. Lost leads stay lost; leads already converted keep their
client link. The blank Health-Life-Wealth rows that the old form and app
seeded onto every lead are the one thing left behind — they are the format
being replaced, and carry nothing.
"""

from django.db import migrations

# Old three-state pipeline, for the record kept on each lead's first event.
OLD_STAGE_LABELS = {
    "pending": "Pending",
    "half_sold": "Half Sold",
    "processed": "Processed",
}

# The three hard-coded products → the catalog rows they mean.
PRODUCT_MAP = {
    "health": ("HEALTH_INS", ["health"]),
    "life": ("LIFE_INS", ["life"]),
    "wealth": ("SIP", ["sip", "mutual"]),
}


def _resolve_product(Product, key, cache):
    if key in cache:
        return cache[key]
    code, name_hints = PRODUCT_MAP.get(key, (None, []))
    product = Product.objects.filter(code=code).first() if code else None
    for hint in name_hints:
        if product:
            break
        product = Product.objects.filter(name__icontains=hint, parent__isnull=True).first()
    cache[key] = product
    return product


def forwards(apps, schema_editor):
    Lead = apps.get_model("clients", "Lead")
    Progress = apps.get_model("clients", "LeadProductProgress")
    Interest = apps.get_model("clients", "LeadInterest")
    StageEvent = apps.get_model("clients", "LeadStageEvent")
    Product = apps.get_model("clients", "Product")

    # Where each lead stood, before we overwrite it — it goes on the event.
    was = dict(Lead.objects.values_list("id", "stage"))
    Lead.objects.all().update(stage="suspect")

    for lead in Lead.objects.all().only("id", "updated_at").iterator():
        Lead.objects.filter(pk=lead.pk).update(stage_changed_at=lead.updated_at)

    cache = {}
    interests, events = [], []
    status_labels = {"pending": "Pending", "half_sold": "Half Sold", "processed": "Processed"}
    for row in Progress.objects.iterator():
        carries_data = bool(row.target_amount or row.achieved_amount or row.remark
                            or row.status != "pending")
        if not carries_data:
            continue                      # a seeded blank row, not a requirement

        note_bits = [f"{row.product.title()} (pre-SPANCO)"]
        if row.target_amount:
            note_bits.append(f"target {row.target_amount:.0f}")
        if row.achieved_amount:
            note_bits.append(f"achieved {row.achieved_amount:.0f}")
        note_bits.append(status_labels.get(row.status, row.status))
        if row.remark:
            note_bits.append(row.remark)

        interests.append(Interest(
            lead_id=row.lead_id,
            product=_resolve_product(Product, row.product, cache),
            amount=row.target_amount or row.achieved_amount,
            note=", ".join(note_bits)[:255],
        ))
    Interest.objects.bulk_create(interests, batch_size=500, ignore_conflicts=True)

    for lead_id, old_stage in was.items():
        previously = OLD_STAGE_LABELS.get(old_stage, old_stage or "—")
        events.append(StageEvent(
            lead_id=lead_id, from_stage="", to_stage="suspect",
            note=f"Pipeline moved to SPANCO. Previously: {previously}.",
        ))
    StageEvent.objects.bulk_create(events, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0118_spanco_lead_pipeline'),
    ]

    operations = [
        # Must run while LeadProductProgress still exists.
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='LeadProductProgress',
        ),
    ]
