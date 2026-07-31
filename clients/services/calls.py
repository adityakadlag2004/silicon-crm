"""Call follow-up reporting shared by the web and app analytics screens.

Outcomes started being recorded in v4.27 ("Done" asks what the call
produced). One implementation so the two screens can't drift the way the
call counts once did.
"""
from django.db.models import Count

from ..models import CallFollowUp


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
