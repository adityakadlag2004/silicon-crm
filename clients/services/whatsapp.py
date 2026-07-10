"""WhatsApp Business Cloud API (Meta, direct) sender.

Business-initiated messages on WhatsApp must use pre-approved *template*
messages, so this module only sends templates — never free-form text.

Activates only when the two env vars below are set; otherwise every call is a
silent no-op, exactly like services/push.py, so the app works unchanged in dev
and on machines without WhatsApp configured.

Setup (see .env.example):
  1. Create a Meta app → add the "WhatsApp" product.
  2. Register/verify the sender phone number in WhatsApp → API Setup and note
     its Phone number ID.
  3. Create a permanent System-User access token with whatsapp_business_messaging.
  4. On the server set:
       WHATSAPP_PHONE_NUMBER_ID=<the phone number ID>
       WHATSAPP_ACCESS_TOKEN=<the permanent token>
  5. In Meta → WhatsApp Manager → Message templates, get these two Utility
     templates approved (names must match TEMPLATE_* below):
       - task_assigned   : 6 body variables ({{1}}..{{6}})
       - task_daily_digest: 5 body variables ({{1}}..{{5}})
"""
import logging
import os

import requests

from ..utils.phone_utils import normalize_phone

logger = logging.getLogger(__name__)

API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
DEFAULT_LANG = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")

# Template names as registered in WhatsApp Manager.
TEMPLATE_TASK_ASSIGNED = "task_assigned"
TEMPLATE_TASK_DIGEST = "task_daily_digest"


def is_configured():
    """True when both required credentials are present."""
    return bool(
        os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        and os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    )


def send_template(to, template, variables, lang=None, created_by=None, client=None):
    """Send an approved template message via the Cloud API.

    `to` is any phone string (normalized to E.164 digits here). `variables` is
    the ordered list of body parameters ({{1}}, {{2}}, …). Best-effort: logs to
    MessageLog, never raises, returns True on a 2xx from Meta else False.
    Silent no-op (returns False) when WhatsApp isn't configured.
    """
    from ..models import MessageLog

    _e164, wa_number = normalize_phone(to)
    if not wa_number:
        logger.warning("WhatsApp: unusable phone %r for template %s", to, template)
        return False

    body_preview = " | ".join(str(v) for v in variables)

    if not is_configured():
        # Record intent so nothing is silently dropped, but don't mark as sent.
        MessageLog.objects.create(
            recipient_phone=wa_number, message_text=f"[{template}] {body_preview}",
            status="skipped", error="WhatsApp not configured",
            created_by=created_by, client=client,
        )
        return False

    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"].strip()
    token = os.environ["WHATSAPP_ACCESS_TOKEN"].strip()
    url = f"https://graph.facebook.com/{API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_number,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang or DEFAULT_LANG},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in variables],
            }],
        },
    }

    log = MessageLog.objects.create(
        recipient_phone=wa_number, message_text=f"[{template}] {body_preview}",
        status="queued", created_by=created_by, client=client,
    )
    try:
        resp = requests.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        if resp.status_code // 100 == 2:
            msg_id = ""
            try:
                msg_id = resp.json().get("messages", [{}])[0].get("id", "")
            except Exception:
                pass
            log.status = "sent"
            log.provider_message_id = msg_id
            log.error = ""
            from django.utils import timezone
            log.sent_at = timezone.now()
            log.save(update_fields=["status", "provider_message_id", "error", "sent_at"])
            return True
        log.status = "failed"
        log.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
        log.save(update_fields=["status", "error"])
        logger.warning("WhatsApp send failed (%s): %s", resp.status_code, resp.text[:300])
        return False
    except Exception as exc:
        log.status = "failed"
        log.error = str(exc)[:500]
        log.save(update_fields=["status", "error"])
        logger.exception("WhatsApp send raised for template %s", template)
        return False
