from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum, Q
from .models import Sale, Client, Notification, Employee, Product, AuditLog, Lead, InsuranceClaim, Task
from django.contrib.auth import get_user_model
from django.urls import reverse

@receiver([post_save, post_delete], sender=Sale)
def update_client_status(sender, instance, **kwargs):
    client = instance.client
    # Only approved sales define what products a client actually holds.
    sales = Sale.objects.filter(client=client, status=Sale.STATUS_APPROVED)

    code_to_name = {p.code: p.name for p in Product.objects.all().only("code", "name")}

    def _sum_amount(product_code, fallback_name, field_name):
        product_name = code_to_name.get(product_code, fallback_name)
        amount = sales.filter(Q(product_ref__code=product_code) | Q(product=product_name)).aggregate(total=Sum(field_name))["total"]
        return amount or 0

    client.sip_amount = _sum_amount("SIP", "SIP", "amount")
    client.life_cover = _sum_amount("LIFE_INS", "Life Insurance", "cover_amount")
    client.health_cover = _sum_amount("HEALTH_INS", "Health Insurance", "cover_amount")
    client.motor_insured_value = _sum_amount("MOTOR_INS", "Motor Insurance", "amount")
    client.pms_amount = _sum_amount("PMS", "PMS", "amount")

    # When the client is covered by the RTA SIP register, the feed is the
    # truth for the SIP columns — sales exist for incentives, not holdings.
    from .models import SipRegistration
    if SipRegistration.objects.filter(client=client).exists():
        client.sip_amount = SipRegistration.objects.filter(
            client=client, status=SipRegistration.STATUS_ACTIVE,
        ).aggregate(total=Sum("amount"))["total"] or 0

    client.sip_status = client.sip_amount > 0
    client.life_status = client.life_cover > 0
    client.health_status = client.health_cover > 0
    client.motor_status = client.motor_insured_value > 0
    client.pms_status = client.pms_amount > 0

    client.save()


@receiver(post_save, sender=Notification)
def push_on_notification(sender, instance, created, **kwargs):
    """Mirror every new in-app notification as an FCM push to the recipient's
    registered devices (Android app). No-op when Firebase isn't configured.

    Senders that deliver their own push (e.g. send_followup_reminders' ringing
    alarm) set `_skip_push` on the instance before save to avoid a duplicate
    tray notification while keeping the in-app Notification row."""
    if not created or getattr(instance, "_skip_push", False):
        return
    try:
        from .services.push import send_push_to_user
        send_push_to_user(
            instance.recipient,
            title=instance.title,
            body=instance.body,
            link=instance.link or "",
        )
    except Exception:
        # Push delivery must never break the transaction that created the
        # notification (sale save, lead assignment, etc.).
        import logging
        logging.getLogger(__name__).exception("Push notification dispatch failed")


@receiver(post_save, sender=Sale)
def notify_admins_on_sale(sender, instance, created, **kwargs):
    if not created:
        return

    User = get_user_model()
    # Deep-link admins to the sales screen (pending ones need approval).
    # The app routes "/clients/sales/…" to the native Sales screen; on the
    # web it opens the approvals page.
    try:
        sales_url = reverse("clients:approve_sales") if instance.status == Sale.STATUS_PENDING \
            else reverse("clients:all_sales")
    except Exception:
        sales_url = ""

    employee_name = getattr(instance.employee, "user", None)
    if employee_name and hasattr(employee_name, "username"):
        employee_name = employee_name.username
    else:
        employee_name = str(instance.employee)

    client_name = getattr(instance.client, "name", str(instance.client))
    amount_display = f"₹{instance.amount}"

    body = (
        f"{employee_name} logged a {instance.product} sale of {amount_display} "
        f"for client {client_name}."
    )

    admin_users = set(User.objects.filter(is_superuser=True))
    admin_users.update(
        User.objects.filter(employee__role="admin")
    )

    for admin_user in admin_users:
        Notification.objects.create(
            recipient=admin_user,
            title="New sale recorded",
            body=body,
            link=sales_url,
            related_sale=instance,
        )

# NOTE: month-close moved to services/targets.py (close_month command wraps it).


# ────────────────────────────────────────────────────────────────────────────
# Audit log: track Sale status transitions (approve/reject/etc).
# Capture the previous status in pre_save, then write the audit row in post_save.
# ────────────────────────────────────────────────────────────────────────────

@receiver(pre_save, sender=Sale)
def _audit_capture_old_sale_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._audit_old_status = None
        return
    try:
        prev = Sale.objects.only("status").get(pk=instance.pk)
        instance._audit_old_status = prev.status
    except Sale.DoesNotExist:
        instance._audit_old_status = None


_AUDIT_ACTION_BY_NEW_STATUS = {
    "approved": AuditLog.ACTION_SALE_APPROVED,
    "rejected": AuditLog.ACTION_SALE_REJECTED,
    "pending":  AuditLog.ACTION_SALE_PENDING,
}


@receiver(post_save, sender=Sale)
def _audit_log_sale_status_change(sender, instance, created, **kwargs):
    old_status = getattr(instance, "_audit_old_status", None)
    new_status = instance.status
    if created or old_status == new_status:
        return  # only log actual transitions

    action = _AUDIT_ACTION_BY_NEW_STATUS.get(new_status)
    if not action:
        return

    actor = getattr(instance, "_audit_actor", None)
    summary = (
        f"Sale #{instance.pk} for {instance.client.name if instance.client_id else '?'}: "
        f"{old_status} → {new_status}"
    )
    details = {
        "from": old_status,
        "to": new_status,
        "amount": str(instance.amount),
        "product": instance.product or "",
        "employee_id": instance.employee_id,
    }
    if new_status == "rejected" and instance.rejection_reason:
        details["rejection_reason"] = instance.rejection_reason

    AuditLog.objects.create(
        action=action,
        actor=actor,
        target_model="Sale",
        target_id=instance.pk,
        summary=summary,
        details=details,
    )


@receiver(post_delete, sender=Sale)
def _audit_log_sale_delete(sender, instance, **kwargs):
    actor = getattr(instance, "_audit_actor", None)
    AuditLog.objects.create(
        action=AuditLog.ACTION_SALE_DELETED,
        actor=actor,
        target_model="Sale",
        target_id=instance.pk,
        summary=f"Sale #{instance.pk} deleted (was {instance.status})",
        details={
            "was_status": instance.status,
            "amount": str(instance.amount),
            "product": instance.product or "",
            "employee_id": instance.employee_id,
        },
    )


@receiver(post_delete, sender=Client)
def _audit_log_client_delete(sender, instance, **kwargs):
    actor = getattr(instance, "_audit_actor", None)
    AuditLog.objects.create(
        action=AuditLog.ACTION_CLIENT_DELETED,
        actor=actor,
        target_model="Client",
        target_id=instance.pk,
        summary=f"Client #{instance.pk} '{instance.name}' deleted",
        details={"name": instance.name, "phone": instance.phone or "", "email": instance.email or ""},
    )

    # Best-effort: rename the linked Drive folder to flag it as archived.
    if instance.drive_folder_id:
        try:
            from .services.google_drive import archive_client_folder
            archive_client_folder(instance.drive_folder_id, instance.name, instance.pk)
        except Exception:
            pass  # Drive lifecycle should never block a CRM deletion


@receiver(post_delete, sender=Lead)
@receiver(post_delete, sender=InsuranceClaim)
def _delete_followup_tasks(sender, instance, **kwargs):
    """A follow-up is a Task pointed at a record by source_kind/source_id, not
    a child row, so nothing cascades when that record is deleted. Without this
    a deleted lead leaves its follow-ups behind to ring on someone's phone."""
    from .services import followups

    kind = followups.LEAD if sender is Lead else followups.CLAIM
    Task.objects.filter(source_kind=kind, source_id=instance.pk).delete()
