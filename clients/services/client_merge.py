"""Merging duplicate Client profiles.

Accidentally-created duplicates hold real business records (sales, renewals,
tasks, MF folios, call follow-ups…), so plain deletion would cascade them
away. merge_clients() repoints every relation from the duplicate onto the
kept profile, copies over any contact fields the kept profile is missing,
then deletes the emptied duplicate.
"""
import logging

from django.db import transaction
from django.db.models import Count

logger = logging.getLogger(__name__)

# Simple fields copied from the duplicate when the kept profile lacks them.
_FILL_FIELDS = (
    "email", "phone", "pan", "address", "date_of_birth", "mapped_to",
    "sip_amount", "sip_topup", "lumsum_investment",
    "health_cover", "health_topup", "health_product",
    "life_cover", "life_product",
    "motor_insured_value", "motor_product",
    "pms_amount", "pms_start_date",
    "drive_folder_id", "drive_folder_url",
)


def business_record_counts_bulk(clients):
    """{client id: {relation label: count}} for many clients at once.

    Same answer as business_record_counts() per client, but one grouped query
    per relation instead of one per relation *per client* — the KYC duplicates
    screen counts hundreds of profiles in a single render.
    """
    from ..models import Client

    ids = [getattr(c, "pk", c) for c in clients]
    counts = {cid: {} for cid in ids}
    if not ids:
        return counts

    for rel in Client._meta.related_objects:
        accessor = rel.get_accessor_name()
        if not accessor:  # related_name="+" — no reverse accessor to report
            continue
        field_name = rel.field.name
        rows = (rel.related_model._default_manager
                .filter(**{f"{field_name}__in": ids})
                .values(field_name)
                .annotate(n=Count("pk")))
        for row in rows:
            n = row["n"]
            if n:
                counts[row[field_name]][accessor] = 1 if rel.one_to_one else n
    return counts


def business_record_counts(client):
    """{relation label: count} of every non-empty reverse relation — used to
    decide whether a plain delete is safe and to describe what a merge moves."""
    return business_record_counts_bulk([client])[client.pk]


@transaction.atomic
def merge_clients(keep, remove):
    """Move everything from `remove` onto `keep`, then delete `remove`.
    Returns {relation label: rows moved}. Raises ValueError on self-merge."""
    from ..models import Client

    if keep.pk == remove.pk:
        raise ValueError("Cannot merge a client into itself.")

    moved = {}
    for rel in Client._meta.related_objects:
        field_name = rel.field.name
        if rel.one_to_one:
            other = getattr(remove, rel.get_accessor_name(), None)
            if other is not None and getattr(keep, rel.get_accessor_name(), None) is None:
                setattr(other, field_name, keep)
                other.save(update_fields=[field_name])
                moved[rel.get_accessor_name()] = 1
            continue
        n = rel.related_model.objects.filter(**{field_name: remove}).update(**{field_name: keep})
        if n:
            moved[rel.get_accessor_name()] = n

    changed = []
    for field in _FILL_FIELDS:
        if not getattr(keep, field, None) and getattr(remove, field, None):
            setattr(keep, field, getattr(remove, field))
            changed.append(field)
    # Boolean product flags: true on either profile stays true.
    for flag in ("sip_status", "health_status", "life_status", "motor_status", "pms_status"):
        if getattr(remove, flag) and not getattr(keep, flag):
            setattr(keep, flag, True)
            changed.append(flag)
    if changed:
        keep.save(update_fields=changed)

    logger.info("Merged client #%s into #%s; moved %s", remove.pk, keep.pk, moved or "nothing")
    remove.delete()
    return moved
