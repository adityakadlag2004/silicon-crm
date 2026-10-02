"""SPANCO lead pipeline: stage moves, the funnel, conversion.

One implementation, called by the web views, the app API and the reports —
a stage may not be set by assigning `lead.stage` anywhere else, or the move
goes unrecorded and the funnel starts lying.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Case, Count, DecimalField, F, Min, Q, Sum, When
from django.urls import reverse
from django.utils import timezone

from .. import permissions
from ..models import Client, Employee, Lead, LeadInterest, LeadRemark, LeadStageEvent, Task
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


# ── Documents (quotations and papers) ──────────────────────────────────────
# Drive is the record: files are listed straight from the lead's folder, so a
# quotation dropped into the folder from Drive itself shows up here too. Every
# function returns an error string instead of raising — Drive being down must
# never take the lead page with it.

def documents(lead):
    """(files, error). Files as Drive returns them; [] when there is no folder."""
    if not lead.drive_folder_id:
        return [], None
    from .google_drive import DriveNotConfigured, list_files
    try:
        return list_files(lead.drive_folder_id), None
    except DriveNotConfigured as e:
        return [], str(e)
    except Exception:
        return [], "Could not read this lead's Drive folder."


def ensure_folder(lead):
    """The lead's Drive folder id, creating "Leads/<name> (#id)" on first use. Raises."""
    if not lead.drive_folder_id:
        from .google_drive import get_or_create_lead_folder
        lead.drive_folder_id = get_or_create_lead_folder(lead.customer_name, lead.pk)
        lead.save(update_fields=["drive_folder_id"])
    return lead.drive_folder_id


def upload_document(lead, uploaded_file, user=None):
    """Upload one file into the lead's folder. Returns an error string or None."""
    from .google_drive import DriveNotConfigured, upload_file
    try:
        upload_file(ensure_folder(lead), uploaded_file.name,
                    uploaded_file.content_type, uploaded_file)
    except DriveNotConfigured as e:
        return str(e)
    except Exception:
        return f"Could not upload “{uploaded_file.name}”. Please try again."
    add_remark(lead, f"Uploaded document: {uploaded_file.name}", user=user)
    return None


def find_document(lead, file_id):
    """The file if it sits in this lead's folder, else None.

    The download/delete endpoints take a Drive file id from the URL; without
    this check they would stream or delete any file the Drive account can see.
    """
    files, _err = documents(lead)
    return next((f for f in files if f["id"] == file_id), None)


def delete_document(lead, file_id, user=None):
    """Delete one of the lead's files from Drive. Returns an error string or None."""
    doc = find_document(lead, file_id)
    if doc is None:
        return "That file is not in this lead's folder."
    from .google_drive import delete_file
    if not delete_file(file_id):
        return f"Could not delete “{doc['name']}”."
    add_remark(lead, f"Deleted document: {doc['name']}", user=user)
    return None


def delete_folder(lead, user=None):
    """Permanently delete the lead's Drive folder and all its files. Error string or None."""
    if not lead.drive_folder_id:
        return None
    from .google_drive import DriveNotConfigured, delete_folder as drive_delete_folder
    try:
        drive_delete_folder(lead.drive_folder_id)
    except DriveNotConfigured as e:
        return str(e)
    except Exception:
        return "Could not delete the Drive folder. Please try again."
    lead.drive_folder_id = ""
    lead.save(update_fields=["drive_folder_id"])
    add_remark(lead, "Deleted the lead's Drive folder and its documents.", user=user)
    return None


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
    # A lead that moved is being worked, so any request to drop it is moot.
    withdrawn = _close_loss_request(lead, user, "Withdrawn — the lead moved stage.")
    lead.save(update_fields=["stage", "stage_changed_at", "is_discarded", "lost_reason",
                             "updated_at", *withdrawn])
    notify_team(lead, user, f"{lead.customer_name} → {event.to_label}",
                note or f"Moved from {event.from_label} to {event.to_label}.")
    return event


def can_decide_loss(user):
    return permissions.is_admin_or_manager(user)


def mark_lost(lead, user=None, reason=""):
    """Park a lead. The stage it died at is kept — that is the weak point.

    A premium lead (`Lead.LOSS_SIGNOFF_TIERS`) dropped by anyone but an admin
    or manager is NOT lost: the drop becomes a request for sign-off and the
    request's event is returned (`to_stage == LOSS_REQUESTED`). The rule lives
    here because both the web view and the app call this — a guard in either
    one alone leaves the other door open. `user=None` is the system (seeds,
    commands) and is never asked.
    """
    if lead.is_discarded:
        return None
    if user is not None and not can_decide_loss(user) and lead.needs_loss_signoff:
        return request_loss(lead, user, reason)
    _close_loss_request(lead, user, "Approved — lead marked lost.", done=True)
    event = LeadStageEvent.objects.create(
        lead=lead,
        from_stage=lead.stage,
        to_stage=LeadStageEvent.LOST,
        note=reason,
        created_by=user if (user and user.is_authenticated) else None,
    )
    lead.is_discarded = True
    lead.lost_reason = (reason or "")[:255]
    lead.save(update_fields=["is_discarded", "lost_reason", "loss_requested_at",
                             "loss_requested_by", "updated_at"])
    followups.cancel_open(followups.LEAD, lead.pk, actor=user,
                         reason="Lead marked lost — follow-up cancelled.")
    notify_team(lead, user, f"{lead.customer_name} marked lost",
                reason or f"Lost at {lead.get_stage_display()}.")
    return event


LOSS_TASK_PREFIX = "lossreq:"


def _deciders(exclude_user=None):
    """Every active admin and manager — the people who can sign off a loss."""
    return [
        e for e in Employee.objects.filter(active=True, role__in=["admin", "manager"])
        .select_related("user")
        if e.user_id and e.user_id != getattr(exclude_user, "id", None)
    ]


def request_loss(lead, user, reason=""):
    """Ask an admin/manager to agree before a premium lead is dropped.

    One task per decider as a group (each closes their own copy), pointed at
    the lead like a follow-up so it shows on the lead and dies with it. Medium
    priority: it is a decision to make today, not an alarm. A second request
    while one is waiting is a no-op (None) — double taps and offline replays.
    """
    if lead.loss_requested_at:
        return None
    event = LeadStageEvent.objects.create(
        lead=lead, from_stage=lead.stage, to_stage=LeadStageEvent.LOSS_REQUESTED,
        note=reason, created_by=user if (user and user.is_authenticated) else None,
    )
    lead.loss_requested_at = timezone.now()
    lead.loss_requested_by = user if (user and user.is_authenticated) else None
    lead.save(update_fields=["loss_requested_at", "loss_requested_by", "updated_at"])

    from .tasks import notify_task
    from ..templatetags.custom_filters import inr
    by = getattr(user, "username", None) or "Someone"
    title = f"Sign off: drop {lead.customer_name}?"
    body = "\n".join([
        f"{by} wants to mark this lead lost.",
        f"Reason: {reason or '(none given)'}",
        f"Lead size: ₹{inr(lead.total_value)} a year · {lead.get_stage_display()}",
        "Approve on the lead page (Mark lost) or keep it in the pipeline.",
        reverse("clients:lead_detail", args=[lead.pk]),
    ])
    today = timezone.localdate()
    for emp in _deciders(exclude_user=user) or [None]:
        task = Task.objects.create(
            title=title[:255], description=body, category=followups.category(),
            priority=Task.PRIORITY_MEDIUM, created_by=followups.system_user(),
            assigned_to=emp, client=lead.converted_client, due_date=today,
            source_kind=followups.LEAD, source_id=lead.pk,
            assign_group=f"{LOSS_TASK_PREFIX}{event.pk}",
        )
        notify_task(task, user, title, f"{by}: {reason or 'no reason given'}", event="assigned")
    notify_team(lead, user, f"{lead.customer_name} — loss sent for sign-off",
                reason or "Waiting for an admin or manager to agree.")
    return event


def _close_loss_request(lead, user, why, done=False):
    """Clear a pending loss request and close its sign-off tasks.

    Returns the fields it changed so the caller can save them in its own
    write; [] when nothing was pending.
    """
    if not lead.loss_requested_at:
        return []
    from .tasks import log_activity
    from ..models import TaskActivity
    for task in Task.objects.filter(
        source_kind=followups.LEAD, source_id=lead.pk, is_deleted=False,
        assign_group__startswith=LOSS_TASK_PREFIX, status__in=Task.OPEN_STATUSES,
    ):
        task.status = Task.STATUS_COMPLETED if done else Task.STATUS_CANCELLED
        task.save(update_fields=["status", "updated_at"])
        log_activity(task, user, TaskActivity.STATUS_CHANGED, why)
    lead.loss_requested_at = None
    lead.loss_requested_by = None
    return ["loss_requested_at", "loss_requested_by"]


def decline_loss(lead, user, note=""):
    """A decider keeps the lead in the pipeline. Logged, and the team is told."""
    if not lead.loss_requested_at:
        return None
    requester = lead.loss_requested_by
    fields = _close_loss_request(lead, user, "Declined — lead stays in the pipeline.", done=True)
    lead.save(update_fields=[*fields, "updated_at"])
    event = LeadStageEvent.objects.create(
        lead=lead, from_stage=lead.stage, to_stage=lead.stage,
        note="Loss declined" + (f": {note}" if note else " — keep working it."),
        created_by=user if (user and user.is_authenticated) else None,
    )
    notify_team(lead, user, f"Keep working {lead.customer_name}",
                note or "The request to drop this lead was declined.")
    if requester and requester.id != getattr(user, "id", None) and not any(
            e.user_id == requester.id for e in lead.team()):
        from .tasks import create_notification
        create_notification(requester, f"Keep working {lead.customer_name}",
                            note or "The request to drop this lead was declined.",
                            link=reverse("clients:lead_detail", args=[lead.pk]), event=None)
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


def value_expr():
    """A lead's yearly value in SQL: interest amounts summed, a SIP's ×12.

    The twin of `LeadInterest.annual_value` — change both or neither.
    """
    codes = LeadInterest.MONTHLY_CODES
    monthly = (Q(interests__product__code__in=codes)
               | Q(interests__product__parent__code__in=codes))
    return Sum(Case(
        When(monthly, then=F("interests__amount") * 12),
        default=F("interests__amount"),
        output_field=DecimalField(max_digits=16, decimal_places=2),
    ))


def with_value(qs):
    """Annotate `deal_value` — the yearly value `Lead.total_value` reads."""
    return qs.annotate(deal_value=value_expr())


# Size filters, read off the same tiers as the chips: each is "this tier and
# everything bigger", so "High & up" is the Premium + High one-click view.
SIZES = {
    key: (label if i == 0 else f"{label} & up", floor)
    for i, (floor, key, label, _c) in enumerate(Lead.VALUE_TIERS) if floor
}
SIZES["none"] = ("No amount", None)


def filter_size(qs, size):
    """Narrow `qs` to a `SIZES` band; anything else leaves it alone.

    A pk subquery rather than filtering an annotation in place: the callers
    go on to count, group and aggregate the result, and an aggregate filter
    would ride along into all of them.
    """
    if size not in SIZES:
        return qs
    floor = SIZES[size][1]
    valued = with_value(Lead.objects.all())
    valued = (valued.filter(Q(deal_value__isnull=True) | Q(deal_value=0)) if floor is None
              else valued.filter(deal_value__gte=floor))
    return qs.filter(pk__in=valued.values("pk"))


def stage_values(qs):
    """{stage: {value, forecast}} for the leads in `qs`, in one query.

    The caller picks the leads — live ones for a forecast, the lost tab for
    what was lost where. Forecast is value × `Lead.STAGE_PROBABILITY`.
    `pipeline` is the open pipeline (everything before Order); Order is
    booked business, reported on its own so it never inflates what is still
    to win.
    """
    rows = qs.values("stage").order_by().annotate(v=value_expr())
    out = {s: {"value": 0.0, "forecast": 0.0} for s in Lead.STAGE_SEQUENCE}
    for row in rows:
        if row["stage"] in out:
            v = float(row["v"] or 0)
            out[row["stage"]] = {"value": v,
                                 "forecast": v * Lead.STAGE_PROBABILITY[row["stage"]]}
    open_stages = Lead.STAGE_SEQUENCE[:-1]
    out["pipeline"] = {
        "value": sum(out[s]["value"] for s in open_stages),
        "forecast": sum(out[s]["forecast"] for s in open_stages),
    }
    return out


PREMIUM_QUIET_DAYS = 7


def quiet_premium_leads(qs=None, days=PREMIUM_QUIET_DAYS):
    """Live premium leads nobody has touched in `days`.

    Touched = moved stage, got a remark, or had a follow-up opened, closed or
    rescheduled inside the window; an open follow-up means someone is on it.
    Order is excluded (booked — convert it) and so is a lead waiting on a
    loss sign-off (a manager has it). Small by construction: premium only.
    """
    cutoff = timezone.now() - timedelta(days=days)
    floor = SIZES["premium"][1]
    leads = list(
        with_value(qs if qs is not None else Lead.objects.all())
        .filter(is_discarded=False, loss_requested_at__isnull=True,
                stage_changed_at__lt=cutoff, deal_value__gte=floor)
        .exclude(stage=Lead.STAGE_ORDER)
        .select_related("assigned_to__user")
    )
    ids = [lead.pk for lead in leads]
    lead_tasks = Task.objects.filter(source_kind=followups.LEAD, source_id__in=ids,
                                     is_deleted=False)
    busy = set(lead_tasks.filter(status__in=Task.OPEN_STATUSES).values_list("source_id", flat=True))
    busy |= set(lead_tasks.filter(updated_at__gte=cutoff).values_list("source_id", flat=True))
    busy |= set(LeadRemark.objects.filter(lead_id__in=ids, created_at__gte=cutoff)
                .values_list("lead_id", flat=True))
    return [lead for lead in leads if lead.pk not in busy]


# How a lead list can be ordered. Value is the default: the list exists to
# work the biggest business first, and a lead with no amount captured sinks
# to the bottom rather than floating above every real one.
SORTS = {
    "value": ("Highest value", [F("deal_value").desc(nulls_last=True), "-stage_changed_at"]),
    "oldest": ("Oldest lead", ["created_at"]),
    "recent": ("Recently moved", ["-stage_changed_at", "-updated_at"]),
}


def ordered(qs, sort):
    """`with_value(qs)` in a `SORTS` order; an unknown key means value."""
    return with_value(qs).order_by(*SORTS.get(sort, SORTS["value"])[1])


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
        with_value(qs.filter(is_discarded=False, stage__in=stages))
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
        # the biggest business, then the one that has sat longest.
        rows.sort(key=lambda r: (r["followups"] > 0, -r["lead"].total_value,
                                 -(r["days_in_stage"] or 0)))
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
