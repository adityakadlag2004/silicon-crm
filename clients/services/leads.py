"""SPANCO lead pipeline: stage moves, the funnel, conversion.

One implementation, called by the web views, the app API and the reports —
a stage may not be set by assigning `lead.stage` anywhere else, or the move
goes unrecorded and the funnel starts lying.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Min, Q
from django.urls import reverse
from django.utils import timezone

from ..models import Client, Lead, LeadRemark, LeadStageEvent, Task
from . import followups

VALID_STAGES = dict(Lead.STAGE_CHOICES)


def notify_team(lead, actor, title, body):
    """Tell everyone else working this lead what just happened.

    A lead shared between two people is only actually shared if each one hears
    what the other did — otherwise both ring the same client on the same
    morning, which is worse than one person owning it. The actor is skipped:
    nobody needs a notification about their own action.
    """
    from .tasks import create_notification

    link = reverse("clients:lead_detail", args=[lead.pk])
    actor_id = getattr(actor, "id", None)
    sent = 0
    for emp in lead.team():
        if emp.user_id and emp.user_id != actor_id:
            if create_notification(emp.user, title, body, link=link, event=None):
                sent += 1
    return sent


def add_remark(lead, text, user=None):
    """Log a remark and tell the rest of the lead's team about it.

    Both write paths (the web page and the app) call this — a note only one of
    two people can see is how the pair drift apart.
    """
    remark = LeadRemark.objects.create(lead=lead, text=(text or "")[:2000], created_by=user)
    by = getattr(user, "username", None) or "Someone"
    notify_team(lead, user, f"Note on {lead.customer_name}", f"{by}: {remark.text[:180]}")
    return remark


def schedule_followup(lead, when, note="", actor=None):
    """Book a follow-up on a lead; the rest of its team is told it is booked.

    The task itself still rings the lead's OWNER — `followups._owner` decides
    that — but a collaborator who does not know a call is already dated is a
    collaborator who books a second one.
    """
    task = followups.schedule(followups.LEAD, lead, when, note=note, actor=actor)
    local = timezone.localtime(when)
    notify_team(lead, actor, f"Follow-up booked — {lead.customer_name}",
                f"{local:%d %b, %H:%M}" + (f" · {note}" if note else ""))
    return task


def set_stage(lead, stage, user=None, note=""):
    """Move a lead to a SPANCO stage, logging the move. Returns the event.

    Moving a lost lead back into the pipeline reopens it — that is what
    picking a stage for it means.
    """
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown SPANCO stage: {stage!r}")
    if stage == lead.stage and not lead.is_discarded:
        # The lead is already here. With no note that is plainly a no-op; with
        # one it is still a duplicate if the last event said exactly the same
        # thing — which is what a double-submitted form and a replayed offline
        # write both look like. The offline outbox replays writes, so the guard
        # has to live here rather than in one caller.
        last = lead.stage_events.first()
        if not note or (last and last.to_stage == stage and (last.note or "") == note):
            return None

    event = LeadStageEvent.objects.create(
        lead=lead,
        from_stage=LeadStageEvent.LOST if lead.is_discarded else lead.stage,
        to_stage=stage,
        note=note,
        created_by=user if (user and user.is_authenticated) else None,
    )
    lead.stage = stage
    lead.stage_changed_at = timezone.now()
    lead.is_discarded = False
    lead.lost_reason = ""
    lead.save(update_fields=["stage", "stage_changed_at", "is_discarded", "lost_reason", "updated_at"])
    notify_team(lead, user, f"{lead.customer_name} → {event.to_label}",
                note or f"Moved from {event.from_label} to {event.to_label}.")
    return event


def mark_lost(lead, user=None, reason=""):
    """Park a lead. The stage it died at is kept — that is the weak point."""
    if lead.is_discarded:
        return None
    event = LeadStageEvent.objects.create(
        lead=lead,
        from_stage=lead.stage,
        to_stage=LeadStageEvent.LOST,
        note=reason,
        created_by=user if (user and user.is_authenticated) else None,
    )
    lead.is_discarded = True
    lead.lost_reason = (reason or "")[:255]
    lead.save(update_fields=["is_discarded", "lost_reason", "updated_at"])
    followups.cancel_open(followups.LEAD, lead.pk, actor=user,
                         reason="Lead marked lost — follow-up cancelled.")
    notify_team(lead, user, f"{lead.customer_name} marked lost",
                reason or f"Lost at {lead.get_stage_display()}.")
    return event


def reopen(lead, user=None):
    """Bring a lost lead back at the stage it was lost from."""
    if not lead.is_discarded:
        return None
    return set_stage(lead, lead.stage, user=user, note="Reopened")


def funnel(qs):
    """Stage-by-stage funnel for a Lead queryset.

    A lead standing at Negotiation has, by definition, passed Suspect through
    Approach, so "reached" counts every lead at or beyond the stage — lost
    ones included, since they did get that far before dying. That makes the
    stage-to-stage conversion honest without replaying the event log.
    """
    rows = qs.values("stage").order_by().annotate(
        total=Count("id"),
        lost=Count("id", filter=Q(is_discarded=True)),
    )
    at = {r["stage"]: r["total"] for r in rows}
    lost_at = {r["stage"]: r["lost"] for r in rows}
    total = sum(at.values())

    out = []
    previous_reached = None
    for index, (stage, label) in enumerate(Lead.STAGE_CHOICES):
        reached = sum(at.get(s, 0) for s in Lead.STAGE_SEQUENCE[index:])
        out.append({
            "stage": stage,
            "label": label,
            "help": Lead.STAGE_HELP[stage],
            "color": Lead.STAGE_COLORS[stage],
            "standing": at.get(stage, 0),          # leads sitting here right now
            "lost_here": lost_at.get(stage, 0),    # dropped at this step
            "reached": reached,                    # got at least this far
            "reached_pct": round(reached / total * 100, 1) if total else 0.0,
            # Conversion from the previous step — the number that shows which
            # step of the method the team is weakest at.
            "step_pct": (
                round(reached / previous_reached * 100, 1)
                if previous_reached else None
            ),
        })
        previous_reached = reached or None
    return out


def stage_counts(qs):
    """{stage: leads standing there} for KPI tiles, in one query."""
    rows = qs.values("stage").order_by().annotate(total=Count("id"))
    counts = {s: 0 for s in Lead.STAGE_SEQUENCE}
    for row in rows:
        if row["stage"] in counts:
            counts[row["stage"]] = row["total"]
    return counts


@transaction.atomic
def convert_to_client(lead, user=None):
    """Create the Client record for a booked lead (Order stage).

    Nothing about cover or SIP is copied across: `signals.update_client_status`
    recomputes those from the client's approved sales, so copying a lead's
    indicative numbers would only plant figures that the first sale overwrites.
    """
    if lead.converted_client_id:
        raise ValueError("This lead has already been converted.")
    if lead.stage != Lead.STAGE_ORDER:
        raise ValueError("Only leads at the Order stage can be converted to clients.")

    client = Client.objects.create(
        name=lead.customer_name,
        phone=lead.phone or None,
        email=lead.email or None,
        mapped_to=lead.assigned_to,
        status="Mapped" if lead.assigned_to else "Unmapped",
    )
    lead.converted_client = client
    lead.save(update_fields=["converted_client", "updated_at"])
    LeadStageEvent.objects.create(
        lead=lead,
        from_stage=lead.stage,
        to_stage=lead.stage,
        note=f"Converted to client #{client.id}",
        created_by=user if (user and user.is_authenticated) else None,
    )
    # The lead is won — chasing it is finished, so its open follow-ups close
    # rather than ringing a phone about work nobody will do.
    followups.cancel_open(followups.LEAD, lead.pk, actor=user,
                         reason=f"Lead converted to client #{client.id}.")
    notify_team(lead, user, f"{lead.customer_name} converted",
                f"Now client #{client.id}.")
    return client


def needs_attention(qs, *, stale_days=14, limit=12):
    """Live-pipeline leads nobody is currently chasing.

    A calendar can only show what somebody dated, so the deals quietly dying
    are precisely the ones with nothing on it. Two ways that happens, and both
    look identical on the agenda — as nothing at all:

      no_followup   at Approach/Negotiation/Conclusion with no open follow-up
                    task against the lead
      stalled       hasn't changed stage in `stale_days`

    Returns rows ready to render: the lead, its stage colour, who owns it, and
    how long it has sat there.
    """
    live = qs.filter(is_discarded=False, stage__in=Lead.STAGE_HOT).select_related(
        "assigned_to__user").prefetch_related("collaborators__user")

    chased = set(
        Task.objects.filter(
            is_deleted=False, source_kind="lead", status__in=Task.OPEN_STATUSES,
        ).values_list("source_id", flat=True)
    )
    cutoff = timezone.now() - timedelta(days=stale_days)

    rows = []
    for lead in live.order_by("stage_changed_at"):
        unchased = lead.id not in chased
        stalled = bool(lead.stage_changed_at and lead.stage_changed_at < cutoff)
        if not (unchased or stalled):
            continue
        days = ((timezone.now() - lead.stage_changed_at).days
                if lead.stage_changed_at else None)
        rows.append({
            "lead": lead,
            "stage_label": VALID_STAGES.get(lead.stage, lead.stage),
            "stage_color": Lead.STAGE_COLORS.get(lead.stage, "#6B7280"),
            "owner": (lead.assigned_to.user.username
                      if lead.assigned_to and lead.assigned_to.user_id else ""),
            "shared_with": [e.user.username for e in lead.collaborators.all()
                            if e.user_id and e.pk != lead.assigned_to_id],
            "days_in_stage": days,
            "no_followup": unchased,
            "stalled": stalled,
        })
    # Nothing scheduled is worse than slow, so those float to the top; within
    # each group the lead that has sat longest goes first.
    rows.sort(key=lambda r: (not r["no_followup"], -(r["days_in_stage"] or 0)))
    return rows[:limit], len(rows)


# Everything from Approach onwards: a lead that has been qualified and worked
# is a lead somebody has to act on. Suspect and Prospect are the top of the
# funnel and belong on the list screen, not on a dashboard that is asking
# "what do I do today?".
BOARD_STAGES = Lead.STAGE_SEQUENCE[Lead.STAGE_SEQUENCE.index(Lead.STAGE_APPROACH):]


def board(qs, *, stages=None, per_stage=8, stale_days=14):
    """One page per stage, each carrying that stage's live leads.

    `needs_attention` answers "who is drifting"; this answers "show me the
    book" — every worked lead, with whether it is being chased on the card
    rather than as a separate list. Two lists of the same leads on one screen
    is what the phone had, and it made the chased ones invisible.

    Three queries whatever the size of the pipeline: the leads, the open
    follow-up tasks (count + next due date per lead), and nothing per row.
    """
    from ..models import Task

    stages = stages or BOARD_STAGES
    live = list(
        qs.filter(is_discarded=False, stage__in=stages)
        .select_related("assigned_to__user")
        .order_by("stage_changed_at")
    )

    chase = {
        row["source_id"]: row
        for row in Task.objects.filter(
            is_deleted=False, source_kind="lead",
            source_id__in=[lead.id for lead in live],
            status__in=Task.OPEN_STATUSES,
        ).values("source_id").order_by().annotate(n=Count("id"), next_due=Min("due_date"))
    }

    now = timezone.now()
    cutoff = now - timedelta(days=stale_days)
    pages = []
    for stage in stages:
        rows = []
        for lead in live:
            if lead.stage != stage:
                continue
            open_chase = chase.get(lead.id)
            rows.append({
                "lead": lead,
                "owner": (lead.assigned_to.user.get_full_name()
                          or lead.assigned_to.user.username)
                if lead.assigned_to and lead.assigned_to.user_id else "",
                "days_in_stage": ((now - lead.stage_changed_at).days
                                  if lead.stage_changed_at else None),
                "followups": open_chase["n"] if open_chase else 0,
                "next_followup": open_chase["next_due"] if open_chase else None,
                "stalled": bool(lead.stage_changed_at and lead.stage_changed_at < cutoff),
            })
        # Unchased first — they are the reason to look at this screen — then
        # the one that has sat longest.
        rows.sort(key=lambda r: (r["followups"] > 0, -(r["days_in_stage"] or 0)))
        pages.append({
            "stage": stage,
            "label": VALID_STAGES.get(stage, stage),
            "help": Lead.STAGE_HELP.get(stage, ""),
            "count": len(rows),
            "unchased": sum(1 for r in rows if not r["followups"]),
            "rows": rows[:per_stage],
            "has_more": len(rows) > per_stage,
        })
    return pages
