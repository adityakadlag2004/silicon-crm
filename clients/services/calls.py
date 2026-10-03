"""Call follow-up reporting shared by the web and app analytics screens.

Outcomes started being recorded in v4.27 ("Done" asks what the call
produced). One implementation so the two screens can't drift the way the
call counts once did.
"""
from django.db.models import Count, F, Func, Value
from django.db.models.functions import Right

from ..models import CallFollowUp, Client, Lead
from ..utils.phone_utils import digits10


def outcome_breakdown(start, end, employee_id=None):
    """Follow-ups closed between `start` and `end` (local dates), by outcome.

    Returns rows in the model's own order, always all of them (zeros
    included — "nobody recorded a conversion this week" is the finding), plus
    a trailing "Not recorded" row for closures made without picking one.
    """
    qs = CallFollowUp.objects.filter(
        status=CallFollowUp.STATUS_DONE,
        completed_at__date__gte=start,
        completed_at__date__lte=end,
    )
    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    counts = dict(qs.values_list("outcome").annotate(n=Count("id")))
    rows = [
        {"key": key, "label": label, "count": counts.get(key, 0)}
        for key, label in CallFollowUp.OUTCOME_CHOICES
    ]
    rows.append({"key": "", "label": "Not recorded", "count": counts.get("", 0)})
    return rows


def caller_names(phones):
    """``{digits10: (name, kind, record_id)}`` for a batch of phone numbers.

    A follow-up stores its client link once, at creation. That leaves two gaps
    the agenda used to show as a bare number: the number belongs to a **lead**
    rather than a client (nothing ever looked in the lead table), and a client
    added *after* the call was logged never gets linked retroactively.

    Resolved at display time so both are covered without editing a single row,
    and batched — one query per table for the whole screen, never one per item.
    A client wins over a lead: they are the closer relationship.
    """
    keys = {k for k in (digits10(p) for p in phones) if k}
    if not keys:
        return {}

    # The last ten digits in SQL — `digits10`'s twin — so the match is one IN
    # lookup per row. An OR of `phone LIKE '%…'` per number was numbers × rows
    # comparisons, and missed a phone stored with spaces ("+91 94234 40791").
    last10 = Right(Func(F("phone"), Value(r"\D"), Value(""), Value("g"),
                        function="regexp_replace"), 10)

    def _index(qs, name_field):
        found = {}
        for row in qs.annotate(d10=last10).filter(d10__in=keys).values("id", name_field, "phone"):
            key = digits10(row["phone"])
            # First row wins: a stable answer beats an arbitrary one when two
            # records share a number.
            if key in keys and key not in found:
                found[key] = (row[name_field], row["id"])
        return found

    leads = _index(Lead.objects.filter(is_discarded=False), "customer_name")
    clients = _index(Client.objects.all(), "name")

    out = {k: (v[0], "lead", v[1]) for k, v in leads.items()}
    out.update({k: (v[0], "client", v[1]) for k, v in clients.items()})
    return out
