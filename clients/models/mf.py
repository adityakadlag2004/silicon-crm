"""Mutual funds: ARN accounts, folios, transactions, RTA feed imports."""

import re

from django.conf import settings
from django.db import models
from .clients import Client


# ───────────────────────── Mutual Funds (RTA feeds) ─────────────────────────
# Free CAMS/KFintech distributor mailback files imported into the CRM so client
# folios and transactions stay current. The firm operates under two codes (the
# NJ-routed ARN and the direct/NSE ARN) — ArnAccount models each of them and
# every imported row is attributed to one.

RTA_CAMS = "CAMS"


RTA_KFIN = "KFIN"


RTA_CHOICES = [(RTA_CAMS, "CAMS"), (RTA_KFIN, "KFintech")]


def normalize_broker_code(raw):
    """Uppercase alphanumerics only: 'ARN-152880 ' → 'ARN152880'."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


class ArnAccount(models.Model):
    """One distribution code the firm transacts under.

    ``arn_code`` is matched against the broker/ARN column of RTA files.
    ``sub_broker_code``, when set, must ALSO match the sub-broker column —
    this is how NJ-routed business (NJ's ARN + our sub-broker code) is told
    apart from NJ's other partners.
    """

    label = models.CharField(max_length=80, unique=True, help_text="e.g. 'Direct (NSE)' or 'NJ'")
    arn_code = models.CharField(max_length=40, help_text="As it appears in RTA files, e.g. ARN-152880")
    sub_broker_code = models.CharField(max_length=40, blank=True, default="",
                                       help_text="Required for platform-routed business (e.g. NJ partner code)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return f"{self.label} ({self.arn_code})"

    def matches(self, broker_raw, sub_broker_raw=""):
        if normalize_broker_code(broker_raw) != normalize_broker_code(self.arn_code):
            return False
        if self.sub_broker_code:
            return normalize_broker_code(sub_broker_raw) == normalize_broker_code(self.sub_broker_code)
        return True

    @classmethod
    def resolve(cls, broker_raw, sub_broker_raw="", accounts=None):
        """Best ArnAccount for a file row; sub-broker matches beat plain ones."""
        candidates = [a for a in (accounts if accounts is not None else cls.objects.filter(is_active=True))
                      if a.matches(broker_raw, sub_broker_raw)]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (0 if a.sub_broker_code else 1))
        return candidates[0]


class MutualFundFolio(models.Model):
    """A client's folio at an AMC, as reported by the RTA feeds."""

    folio_number = models.CharField(max_length=40, db_index=True)
    amc_name = models.CharField(max_length=120, blank=True, default="")
    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    arn = models.ForeignKey(ArnAccount, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="folios")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="mf_folios")
    investor_name = models.CharField(max_length=200, blank=True, default="")
    pan = models.CharField(max_length=20, blank=True, default="", db_index=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("folio_number", "amc_name")
        ordering = ["investor_name", "folio_number"]

    def __str__(self):
        return f"{self.folio_number} · {self.amc_name or self.rta}"


class MutualFundTransaction(models.Model):
    """One row of an RTA transaction feed (WBR2 / KFintech equivalent)."""

    folio = models.ForeignKey(MutualFundFolio, on_delete=models.CASCADE, related_name="transactions")
    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    scheme_name = models.CharField(max_length=200, blank=True, default="")
    txn_type = models.CharField(max_length=80, blank=True, default="")
    txn_number = models.CharField(max_length=60, blank=True, default="")
    trade_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    units = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    nav = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    arn = models.ForeignKey(ArnAccount, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="mf_transactions")
    broker_code = models.CharField(max_length=40, blank=True, default="")
    sub_broker_code = models.CharField(max_length=40, blank=True, default="")
    # sha1 over the identifying columns — makes re-imported files no-ops.
    dedupe_key = models.CharField(max_length=40, unique=True)
    source_import = models.ForeignKey("RTAFeedImport", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="transactions")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_date", "-id"]
        indexes = [
            models.Index(fields=["trade_date"], name="mftxn_trade_date_idx"),
            models.Index(fields=["arn", "trade_date"], name="mftxn_arn_date_idx"),
        ]

    def __str__(self):
        return f"{self.folio.folio_number} {self.txn_type} ₹{self.amount} on {self.trade_date}"


class RTAFeedImport(models.Model):
    """Audit log: one processed feed file (from the mailbox or manual upload)."""

    SOURCE_EMAIL = "email"
    SOURCE_UPLOAD = "upload"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    source = models.CharField(max_length=10, choices=[(SOURCE_EMAIL, "Email"), (SOURCE_UPLOAD, "Upload")])
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=10, default=STATUS_PROCESSED, choices=[
        (STATUS_PROCESSED, "Processed"), (STATUS_FAILED, "Failed"), (STATUS_SKIPPED, "Skipped"),
    ])
    rows_total = models.IntegerField(default=0)
    rows_imported = models.IntegerField(default=0)
    rows_duplicate = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    folios_created = models.IntegerField(default=0)
    clients_linked = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} [{self.status}]"


class SipRegistration(models.Model):
    """One systematic-plan registration (SIP/STP/SWP) from RTA registration
    reports — CAMS 'Systematic Registration Status' and KFintech MFSD243.

    This is the firm's SIP register: new registrations arrive daily by feed,
    an explicit cease/cancel status in a feed marks the row ceased (and
    notifies admins), and the SIP-register screen derives 'completed'
    (end_date passed) and 'at-risk' (active but installments stopped
    arriving) states live.
    """

    STATUS_ACTIVE = "active"
    STATUS_CEASED = "ceased"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CEASED, "Ceased"),
    ]

    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    # KFintech carries a registration ref; CAMS registration files don't.
    registration_ref = models.CharField(max_length=60, blank=True, default="")
    folio_number = models.CharField(max_length=40, db_index=True)
    folio = models.ForeignKey(MutualFundFolio, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="sip_registrations")
    client = models.ForeignKey("Client", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="sip_registrations")
    pan = models.CharField(max_length=20, blank=True, default="")
    investor_name = models.CharField(max_length=200, blank=True, default="")
    amc_name = models.CharField(max_length=120, blank=True, default="")
    scheme_name = models.CharField(max_length=200, blank=True, default="")

    txn_type = models.CharField(max_length=20, blank=True, default="SIP",
                                help_text="SIP / STP / SWP as reported by the RTA")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    frequency = models.CharField(max_length=30, blank=True, default="")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    registered_on = models.DateField(null=True, blank=True)
    installments = models.IntegerField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_ACTIVE, db_index=True)
    ceased_on = models.DateField(null=True, blank=True)

    arn = models.ForeignKey(ArnAccount, null=True, blank=True,
                            on_delete=models.SET_NULL, related_name="sip_registrations")
    broker_code = models.CharField(max_length=40, blank=True, default="")
    sub_broker_code = models.CharField(max_length=40, blank=True, default="")

    # sha1 over the identity fields so re-imported reports upsert
    dedupe_key = models.CharField(max_length=40, unique=True)
    source_import = models.ForeignKey(RTAFeedImport, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="sip_registrations")
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-registered_on", "-first_seen_at"]
        indexes = [
            models.Index(fields=["status", "start_date"], name="sipreg_status_start_idx"),
        ]

    def __str__(self):
        return f"{self.txn_type} {self.scheme_name[:30]} ₹{self.amount} ({self.status})"
