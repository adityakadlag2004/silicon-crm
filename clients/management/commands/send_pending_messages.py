from django.core.management.base import BaseCommand
from django.utils import timezone
from clients.models import MessageLog

class Command(BaseCommand):
    help = 'Process queued MessageLog records and simulate sending (scaffold for real provider integration)'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100, help='Maximum messages to process')

    def handle(self, *args, **options):
        limit = options['limit']
        qs = MessageLog.objects.filter(status='queued').order_by('created_at')[:limit]
        total = qs.count()
        self.stdout.write(f'Processing {total} queued messages...')
        processed = 0
        for m in qs:
            try:
                # TODO: replace this block with real provider integration (Twilio, Gupshup, etc.)
                # For now we simulate a successful send and write a provider_message_id
                m.status = 'sent'
                m.provider_message_id = f"SIMULATED-{m.id}-{int(timezone.now().timestamp())}"
                m.sent_at = timezone.now()
                m.error = ''
                m.save(update_fields=['status', 'provider_message_id', 'sent_at', 'error'])
                processed += 1
                self.stdout.write(self.style.SUCCESS(f"Sent message {m.id} to {m.recipient_phone}"))
            except Exception as e:
                m.status = 'failed'
                m.error = str(e)
                m.save(update_fields=['status', 'error'])
                self.stdout.write(self.style.ERROR(f"Failed sending {m.id}: {e}"))
        self.stdout.write(self.style.SUCCESS(f'Done. Processed: {processed}'))
