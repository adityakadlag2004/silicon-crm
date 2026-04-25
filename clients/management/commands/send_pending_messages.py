from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from clients.models import MessageLog


class Command(BaseCommand):
    help = 'Process queued MessageLog records (SIMULATION MODE — no real messages sent)'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100, help='Maximum messages to process')

    def handle(self, *args, **options):
        limit = options['limit']

        # Loud simulation warning — prevents silent "delivery" in production
        self.stderr.write(self.style.WARNING(
            "\n*** WARNING: send_pending_messages is running in SIMULATION MODE.       ***\n"
            "*** No messages are actually sent to recipients.                        ***\n"
            "*** Implement real provider integration (Twilio, Gupshup, etc.)         ***\n"
            "*** before scheduling this command in production.                       ***\n"
        ))

        whatsapp_provider = getattr(settings, 'WHATSAPP_PROVIDER', None)
        if not whatsapp_provider:
            self.stderr.write(self.style.WARNING(
                "WHATSAPP_PROVIDER is not configured in settings. "
                "Messages will be marked as simulated-sent without actual delivery."
            ))

        messages = list(MessageLog.objects.filter(status='queued').order_by('created_at')[:limit])
        total = len(messages)
        self.stdout.write(f'Processing {total} queued messages (SIMULATED)...')
        processed = 0
        for m in messages:
            try:
                # TODO: Replace this block with real provider integration (Twilio, Gupshup, etc.)
                m.status = 'sent'
                m.provider_message_id = f"SIMULATED-{m.id}-{int(timezone.now().timestamp())}"
                m.sent_at = timezone.now()
                m.error = ''
                m.save(update_fields=['status', 'provider_message_id', 'sent_at', 'error'])
                processed += 1
                self.stdout.write(self.style.WARNING(
                    f"[SIMULATED] Message {m.id} to {m.recipient_phone} marked sent (not actually delivered)."
                ))
            except Exception as e:
                m.status = 'failed'
                m.error = str(e)
                m.save(update_fields=['status', 'error'])
                self.stdout.write(self.style.ERROR(f"Failed processing {m.id}: {e}"))

        self.stdout.write(self.style.WARNING(
            f'Done. Processed (simulated): {processed}. '
            'Replace this stub with real provider code before use in production.'
        ))
