"""Claim workflow: stage transitions, notes, documents and reminders.

Keeps the claim views thin and the audit trail consistent — every meaningful
change routes through here, logs a ClaimActivity, and (for reminders) hooks
into the shared calendar + mobile-push pipeline.
"""
from __future__ import annotations

from django.utils import timezone

from ..models import ClaimActivity, ClaimDocument, ClaimReminder, InsuranceClaim

# Which date field each stage stamps when the claim reaches it.
_STAGE_DATE = {
    InsuranceClaim.STATUS_INTIMATED: "intimation_date",
    InsuranceClaim.STATUS_SUBMITTED: "submission_date",
    InsuranceClaim.STATUS_SETTLED: "settlement_date",
}


def log(claim, actor, action, detail=""):
    return ClaimActivity.objects.create(
        claim=claim, actor=actor, action=action, detail=detail[:1000])


def advance_stage(claim, new_status, actor, *, settled_amount=None, note=""):
    """Move a claim to a new stage, stamp the matching date, and log it.

    Stamps the stage's date only if it isn't already set, so re-saving doesn't
    rewrite history. ``settled_amount`` is applied when settling. Returns the
    claim.
    """
    valid = dict(InsuranceClaim.STATUS_CHOICES)
    if new_status not in valid or new_status == claim.status:
        return claim

    old = claim.get_status_display()
    claim.status = new_status

    date_field = _STAGE_DATE.get(new_status)
    if date_field and not getattr(claim, date_field):
        setattr(claim, date_field, timezone.localdate())
    if new_status == InsuranceClaim.STATUS_SETTLED and settled_amount is not None:
        claim.settled_amount = settled_amount

    claim.save()
    detail = f"{old} → {claim.get_status_display()}"
    if note:
        detail += f" — {note}"
    log(claim, actor, ClaimActivity.STATUS_CHANGED, detail)
    return claim


def add_note(claim, actor, text):
    text = (text or "").strip()
    if not text:
        return None
    return log(claim, actor, ClaimActivity.NOTE, text)


def _claims_drive_folder(client):
    """Client's Drive folder id — claim documents live with the client, so a
    claim's papers sit alongside the client's policy documents."""
    from .google_drive import get_or_create_client_folder
    folder_id, _url = get_or_create_client_folder(client.name, client.id)
    return folder_id


def upload_document(claim, uploaded_file, actor, *, kind="other"):
    """Upload one file to the client's Drive folder and record it.

    Returns (ClaimDocument or None, error_message or None). Never raises — a
    Drive hiccup must not lose the claim edit the user is making.
    """
    from .google_drive import DriveNotConfigured, upload_file

    name = f"Claim#{claim.pk} · {uploaded_file.name}"
    try:
        parent = _claims_drive_folder(claim.policy.client)
        file_id, link = upload_file(parent, name, uploaded_file.content_type, uploaded_file)
    except DriveNotConfigured:
        return None, "Google Drive is not configured — the document was not saved."
    except Exception:
        return None, f"Could not upload “{uploaded_file.name}”. Please try again."

    doc = ClaimDocument.objects.create(
        claim=claim, uploaded_by=actor, kind=kind,
        filename=uploaded_file.name[:255],
        mime=(uploaded_file.content_type or "")[:120],
        size=uploaded_file.size or 0,
        drive_file_id=file_id, drive_view_link=link,
    )
    log(claim, actor, ClaimActivity.DOCUMENT_ADDED,
        f"{doc.get_kind_display()}: {doc.filename}")
    return doc, None


def create_reminder(claim, actor, scheduled_at, note="", employee=None):
    """Schedule a follow-up on a claim.

    It surfaces on the common calendar and fires a mobile push at its time via
    the send_followup_reminders cron — the same pipeline as call/lead
    follow-ups, so nothing new has to be maintained.
    """
    employee = employee or claim.handled_by or getattr(actor, "employee", None)
    reminder = ClaimReminder.objects.create(
        claim=claim, employee=employee, scheduled_at=scheduled_at,
        note=(note or "").strip()[:255], created_by=actor)
    log(claim, actor, ClaimActivity.REMINDER_SET,
        f"{timezone.localtime(scheduled_at):%d %b %Y %H:%M}"
        + (f" — {reminder.note}" if reminder.note else ""))
    return reminder


def complete_reminder(reminder, actor):
    reminder.status = ClaimReminder.STATUS_DONE
    reminder.completed_at = timezone.now()
    reminder.save(update_fields=["status", "completed_at"])
    log(reminder.claim, actor, ClaimActivity.REMINDER_DONE,
        reminder.note or "Follow-up done")
    return reminder
