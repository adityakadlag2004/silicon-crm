"""WhatsApp messaging views: bulk send, preview page, CSV export."""
import csv
import json
from urllib.parse import quote as urlquote

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST, require_GET

from ..models import Client, MessageTemplate, MessageLog
from ..utils.phone_utils import normalize_phone


def _get_sender_name(request):
    """Resolve a display name for the current user."""
    try:
        if hasattr(request.user, "get_full_name") and request.user.get_full_name():
            return request.user.get_full_name()
        if hasattr(request.user, "employee") and getattr(request.user.employee, "name", None):
            return request.user.employee.name
        return getattr(request.user, "username", "")
    except Exception:
        return getattr(request.user, "username", "")


@login_required
@require_POST
def bulk_whatsapp(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        template_id = data.get("template_id")
        client_ids = data.get("client_ids", [])
        preview_only = data.get("preview", False)

        if not template_id or not client_ids:
            return JsonResponse({"error": "Missing template_id or client_ids"}, status=400)

        template = MessageTemplate.objects.filter(id=template_id).first()
        if not template:
            return JsonResponse({"error": "Template not found"}, status=404)

        clients = Client.objects.filter(id__in=client_ids)
        if not clients.exists():
            return JsonResponse({"error": "No valid clients found"}, status=404)

        messages_preview = []
        skipped = []
        sender_name = _get_sender_name(request)

        for client in clients:
            e164, wa_number = normalize_phone(client.phone)
            if not e164:
                skipped.append({"id": client.id, "name": client.name, "phone": client.phone})
                continue

            try:
                rendered = template.render(client, extra_context={"sender_name": sender_name})
            except Exception:
                rendered = template.content

            messages_preview.append({
                "id": client.id,
                "client": client.name,
                "phone": e164,
                "wa_number": wa_number,
                "message": rendered,
            })

        if preview_only:
            return JsonResponse({"messages_preview": messages_preview, "sent_count": 0, "skipped": skipped})

        queued_count = 0
        for msg in messages_preview:
            try:
                cli = Client.objects.get(id=msg.get("id"))
            except Exception:
                cli = None
            MessageLog.objects.create(
                template=template,
                client=cli,
                recipient_phone=msg["phone"],
                message_text=msg["message"],
                status="queued",
                created_by=request.user,
            )
            queued_count += 1

        return JsonResponse({"messages_preview": messages_preview, "sent_count": queued_count, "skipped": skipped})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_GET
def wa_preview_page(request):
    """Render page with wa.me links and QR codes for selected clients."""
    template_id = request.GET.get("template_id")
    client_ids = request.GET.get("client_ids", "")
    if not template_id or not client_ids:
        return HttpResponse("Missing parameters", status=400)
    try:
        tpl = MessageTemplate.objects.get(id=int(template_id))
    except Exception:
        return HttpResponse("Template not found", status=404)

    ids = [int(x) for x in client_ids.split(",") if x.strip().isdigit()]
    clients = Client.objects.filter(id__in=ids)
    sender_name = _get_sender_name(request)
    previews = []

    for c in clients:
        e164, wa = normalize_phone(c.phone)
        if not e164:
            continue
        msg = tpl.render(c, extra_context={"sender_name": sender_name})
        if len(msg) > 1000:
            msg = msg[:1000]
        link = f"https://wa.me/{wa}?text={urlquote(msg)}"
        qr_src = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urlquote(link)}"
        previews.append({
            "client": c.name,
            "phone": e164,
            "wa": wa,
            "message": msg,
            "link": link,
            "qr": qr_src,
        })

    return render(request, "clients/wa_preview_page.html", {"previews": previews, "template": tpl})


@login_required
@require_GET
def wa_preview_csv(request):
    template_id = request.GET.get("template_id")
    client_ids = request.GET.get("client_ids", "")
    if not template_id or not client_ids:
        return HttpResponse("Missing parameters", status=400)
    try:
        tpl = MessageTemplate.objects.get(id=int(template_id))
    except Exception:
        return HttpResponse("Template not found", status=404)

    ids = [int(x) for x in client_ids.split(",") if x.strip().isdigit()]
    clients = Client.objects.filter(id__in=ids)
    sender_name = _get_sender_name(request)

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="wa_previews.csv"'
    writer = csv.writer(resp)
    writer.writerow(["client_id", "client_name", "phone_e164", "wa_number", "message", "wa_link"])

    for c in clients:
        e164, wa = normalize_phone(c.phone)
        if not e164:
            continue
        msg = tpl.render(c, extra_context={"sender_name": sender_name})
        if len(msg) > 1000:
            msg = msg[:1000]
        link = f"https://wa.me/{wa}?text={urlquote(msg)}"
        writer.writerow([c.id, c.name, e164, wa, msg, link])

    return resp
