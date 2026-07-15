"""RTA distributor mailback feeds (CAMS + KFintech) → CRM folios/transactions.

Both RTAs email ARN holders free daily "mailback" files (CAMS WBR reports,
KFintech DSS files) as password-protected zips of DBF/CSV data. This module
ingests those files — from an IMAP mailbox (cron) or a manual upload — and
upserts MutualFundFolio / MutualFundTransaction rows, auto-linking folios to
Clients by PAN.

Column names differ per RTA and per report, so mapping is alias-driven and
tolerant: a file whose headers we don't recognise is logged as failed with its
header list in the notes, ready for an alias-table extension once the real
file is seen. Re-importing the same file is a no-op (sha256 + per-row dedupe).

Like services/push.py, the mailbox fetcher silently no-ops when the
RTA_FEED_IMAP_* env vars are not configured.
"""
import csv
import hashlib
import imaplib
import io
import logging
import os
import re
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email import message_from_bytes
from email.utils import parseaddr

from django.db import transaction

logger = logging.getLogger(__name__)

DATA_EXTENSIONS = (".dbf", ".csv", ".txt")
CONTAINER_EXTENSIONS = (".zip",) + DATA_EXTENSIONS

# Feed column aliases, keyed by our field name. Headers are compared after
# uppercasing and stripping non-alphanumerics ("TD_ACNO" → "TDACNO").
# Covers CAMS WBR2/WBR9-style and KFintech (ex-Karvy) MFSD-style files.
FIELD_ALIASES = {
    "folio": ["FOLIO", "FOLIONO", "FOLIONUMBER", "FOLIOCHK", "ACNO", "TDACNO", "FOLIONUM"],
    "amc": ["AMCCODE", "AMC", "AMCNAME", "FMCODE", "FUNDNAME", "COMPANY"],
    "scheme": ["SCHEME", "SCHEMENAME", "SCHNAME", "FUNDDESC", "SCHEMEDESC", "PRODNAME",
               "PRODCODE", "PRODUCTCODE", "SCHEMECODE", "TDFUND", "PLANDESC"],
    "investor_name": ["INVNAME", "INVESTORNAME", "NAME", "INVESTOR", "CLIENTNAME", "HOLDERNAME"],
    "pan": ["PAN", "PANNO", "PANNUMBER", "PANGNO", "INVPAN", "PAN1"],
    "txn_type": ["TRXNTYPE", "TRXNDESC", "TRNDESC", "TRANSACTIONTYPE", "TXNTYPE", "TRNTYPE",
                 "TRXNNATURE", "TRANTYPE", "NATURE"],
    "txn_number": ["TRXNNO", "TDTRNO", "TRANSACTIONNO", "TXNNO", "TRNO", "TRXNID"],
    "trade_date": ["TRADDATE", "TDTRDT", "TRADEDATE", "TXNDATE", "TRANDATE", "TRDATE", "DATE",
                   "TRANSACTIONDATE", "TRXNDATE"],
    "amount": ["AMOUNT", "TDAMT", "AMT", "TRXNAMOUNT", "NETAMOUNT", "GROSSAMT"],
    "units": ["UNITS", "TDUNITS", "UNIT", "TRXNUNITS"],
    "nav": ["PURPRICE", "NAV", "TDNAV", "PRICE", "TRADENAV"],
    "broker": ["BROKCODE", "BRCODE", "BROKER", "BROKERCODE", "ARN", "ARNCODE", "AGENTCODE"],
    "sub_broker": ["SUBBROK", "SBCODE", "SUBBROKER", "SUBBROKERCODE", "SUBBRCODE", "SUBARN"],
}

_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                 "%d/%m/%y", "%m/%d/%Y", "%d %b %Y")


def _norm_header(name):
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def _build_header_map(headers):
    """{our_field: actual_header} for every alias present in `headers`."""
    normed = {_norm_header(h): h for h in headers}
    mapping = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normed:
                mapping[field] = normed[alias]
                break
    return mapping


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(value, places=2):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
        return d.quantize(Decimal(1).scaleb(-places))
    except (InvalidOperation, ValueError):
        return None


def _clean(value):
    return str(value).strip() if value is not None else ""


# ─── File readers ────────────────────────────────────────────────────────────

def _read_dbf(data):
    """DBF bytes → (headers, row dicts). dbfread needs a real file path."""
    from dbfread import DBF

    with tempfile.NamedTemporaryFile(suffix=".dbf", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        table = DBF(path, ignore_missing_memofile=True, encoding="latin-1",
                    char_decode_errors="ignore")
        rows = [dict(rec) for rec in table]
        return list(table.field_names), rows
    finally:
        os.unlink(path)


def _read_delimited(data):
    """CSV/TXT bytes → (headers, row dicts). Sniffs , ; | or tab."""
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delimiter = max(",;|\t", key=sample.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    return list(reader.fieldnames or []), rows


def read_data_file(file_name, data):
    """Return (headers, raw row dicts) for a .dbf/.csv/.txt payload."""
    if file_name.lower().endswith(".dbf"):
        return _read_dbf(data)
    return _read_delimited(data)


def extract_data_files(file_name, data, passwords):
    """Yield (inner_name, bytes) data files from a container (zip or bare file).

    RTA zips are password-protected with the ARN code; `passwords` is the list
    of candidates to try (empty string = unencrypted).
    """
    lower = file_name.lower()
    if not lower.endswith(".zip"):
        if lower.endswith(DATA_EXTENSIONS):
            yield file_name, data
        return

    import pyzipper

    with pyzipper.AESZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(DATA_EXTENSIONS):
                continue
            content = None
            for pw in passwords:
                try:
                    content = zf.read(info.filename, pwd=pw.encode() if pw else None)
                    break
                except Exception:  # noqa: BLE001 — wrong password / unsupported crypto
                    continue
            if content is None:
                raise ValueError(
                    f"Could not decrypt '{info.filename}' — none of the ARN-based "
                    f"passwords worked. Check the ARN codes under MF settings."
                )
            yield info.filename, content


def _password_candidates():
    """Zip password guesses: each active ARN code in common spellings, plus
    RTA_FEED_ZIP_PASSWORDS extras. Empty string first (unencrypted zips)."""
    from ..models import ArnAccount, normalize_broker_code

    candidates = [""]
    for account in ArnAccount.objects.filter(is_active=True):
        raw = account.arn_code.strip()
        norm = normalize_broker_code(raw)                 # ARN152880
        digits = re.sub(r"[^0-9]", "", raw)               # 152880
        for candidate in (raw, norm, digits, f"ARN-{digits}" if digits else ""):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    extra = os.environ.get("RTA_FEED_ZIP_PASSWORDS", "")
    for candidate in (p.strip() for p in extra.split(",")):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    # Zip passwords are case-sensitive; users type ARNs in either case.
    candidates += [c.lower() for c in candidates if c and c.lower() not in candidates]
    return candidates


def detect_rta(file_name, headers=None, default=""):
    """Guess which RTA a file came from, from its name or column style."""
    from ..models import RTA_CAMS, RTA_KFIN

    name = (file_name or "").upper()
    if "WBR" in name or "CAMS" in name:
        return RTA_CAMS
    if any(tag in name for tag in ("KFIN", "KARVY", "MFSD")):
        return RTA_KFIN
    normed = {_norm_header(h) for h in (headers or [])}
    if normed & {"TDACNO", "FMCODE", "TDTRNO", "TDAMT"}:
        return RTA_KFIN
    if normed & {"AMCCODE", "TRXNNO", "BROKCODE"}:
        return RTA_CAMS
    return default


# ─── Importer ────────────────────────────────────────────────────────────────

def _normalize_pan(raw):
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def _client_by_pan():
    """{normalized PAN: client} for unambiguous PANs only."""
    from ..models import Client

    mapping = {}
    ambiguous = set()
    for client_id, pan in Client.objects.exclude(pan__isnull=True).exclude(pan="").values_list("id", "pan"):
        key = _normalize_pan(pan)
        if not key:
            continue
        if key in mapping:
            ambiguous.add(key)
        mapping[key] = client_id
    for key in ambiguous:
        mapping.pop(key, None)
    return mapping


def _dedupe_key(rta, amc, folio, txn_number, txn_type, trade_date, amount, units):
    blob = "|".join(str(part or "") for part in
                    (rta, amc, folio, txn_number, txn_type, trade_date, amount, units))
    return hashlib.sha1(blob.encode()).hexdigest()


def import_rows(rows, header_map, *, rta, feed_import):
    """Upsert folios + transactions from mapped feed rows, updating the
    counters on `feed_import`. Rows without an amount/units/txn-type are
    treated as folio-master rows (WBR9 style) and only upsert the folio."""
    from ..models import ArnAccount, MutualFundFolio, MutualFundTransaction

    accounts = list(ArnAccount.objects.filter(is_active=True))
    pan_to_client = _client_by_pan()
    unmatched_brokers = set()

    def get(row, field):
        header = header_map.get(field)
        return row.get(header) if header else None

    folio_cache = {}
    for row in rows:
        feed_import.rows_total += 1
        folio_no = _clean(get(row, "folio"))[:40]
        if not folio_no:
            feed_import.rows_skipped += 1
            continue

        amc = _clean(get(row, "amc"))[:120]
        pan = _normalize_pan(_clean(get(row, "pan")))[:20]
        investor = _clean(get(row, "investor_name"))[:200]
        broker = _clean(get(row, "broker"))[:40]
        sub_broker = _clean(get(row, "sub_broker"))[:40]
        arn = ArnAccount.resolve(broker, sub_broker, accounts=accounts) if broker else None
        if broker and arn is None:
            unmatched_brokers.add(f"{broker}/{sub_broker}" if sub_broker else broker)

        cache_key = (folio_no, amc)
        folio = folio_cache.get(cache_key)
        if folio is None:
            folio, created = MutualFundFolio.objects.get_or_create(
                folio_number=folio_no, amc_name=amc,
                defaults={"rta": rta, "investor_name": investor, "pan": pan},
            )
            if created:
                feed_import.folios_created += 1
            folio_cache[cache_key] = folio

        # Enrich the folio with anything the row knows that it doesn't.
        changed = []
        if investor and not folio.investor_name:
            folio.investor_name = investor
            changed.append("investor_name")
        if pan and not folio.pan:
            folio.pan = pan
            changed.append("pan")
        if rta and not folio.rta:
            folio.rta = rta
            changed.append("rta")
        if arn and folio.arn_id is None:
            folio.arn = arn
            changed.append("arn")
        if folio.client_id is None and folio.pan and folio.pan in pan_to_client:
            folio.client_id = pan_to_client[folio.pan]
            changed.append("client_id")
            feed_import.clients_linked += 1
        if changed:
            folio.save(update_fields=changed + ["updated_at"])

        amount = _parse_decimal(get(row, "amount"))
        units = _parse_decimal(get(row, "units"), places=4)
        txn_type = _clean(get(row, "txn_type"))
        if amount is None and units is None and not txn_type:
            feed_import.rows_imported += 1  # folio-master row
            continue

        trade_date = _parse_date(get(row, "trade_date"))
        txn_number = _clean(get(row, "txn_number"))
        key = _dedupe_key(rta, amc, folio_no, txn_number, txn_type, trade_date, amount, units)
        _, created = MutualFundTransaction.objects.get_or_create(
            dedupe_key=key,
            defaults={
                "folio": folio, "rta": rta, "scheme_name": _clean(get(row, "scheme"))[:200],
                "txn_type": txn_type[:80], "txn_number": txn_number[:60],
                "trade_date": trade_date, "amount": amount, "units": units,
                "nav": _parse_decimal(get(row, "nav"), places=4),
                "arn": arn, "broker_code": broker[:40], "sub_broker_code": sub_broker[:40],
                "source_import": feed_import,
            },
        )
        if created:
            feed_import.rows_imported += 1
        else:
            feed_import.rows_duplicate += 1

    if unmatched_brokers:
        listing = ", ".join(sorted(unmatched_brokers)[:20])
        feed_import.notes += f"\nUnmatched broker codes (add under MF → ARN codes?): {listing}"


def import_feed_container(file_name, data, *, source, rta_hint="", user=None):
    """Import one container file (zip or bare data file). Returns the
    RTAFeedImport log row; a container already imported is logged as skipped."""
    from ..models import RTAFeedImport

    sha = hashlib.sha256(data).hexdigest()
    if RTAFeedImport.objects.filter(file_sha256=sha, status=RTAFeedImport.STATUS_PROCESSED).exists():
        return RTAFeedImport.objects.create(
            source=source, file_name=file_name[:255], file_sha256=sha, rta=rta_hint,
            status=RTAFeedImport.STATUS_SKIPPED, notes="Identical file already imported.",
            uploaded_by=user,
        )

    # Created before the atomic block so a failed import still leaves a log row
    # (the folio/transaction changes themselves are rolled back).
    feed_import = RTAFeedImport.objects.create(
        source=source, file_name=file_name[:255], file_sha256=sha,
        rta=rta_hint, uploaded_by=user,
    )
    inner_notes = []
    try:
        with transaction.atomic():
            found_data_file = False
            for inner_name, content in extract_data_files(file_name, data, _password_candidates()):
                found_data_file = True
                headers, rows = read_data_file(inner_name, content)
                header_map = _build_header_map(headers)
                rta = detect_rta(inner_name, headers, rta_hint) or detect_rta(file_name, headers, rta_hint)
                if not feed_import.rta and rta:
                    feed_import.rta = rta
                if "folio" not in header_map:
                    inner_notes.append(
                        f"{inner_name}: no folio column recognised — headers: {', '.join(map(str, headers))}"
                    )
                    continue
                import_rows(rows, header_map, rta=rta, feed_import=feed_import)
                inner_notes.append(f"{inner_name}: {len(rows)} rows")
            if not found_data_file:
                inner_notes.append("No .dbf/.csv/.txt data files found inside.")
            if feed_import.rows_total == 0 and feed_import.rows_imported == 0:
                feed_import.status = RTAFeedImport.STATUS_FAILED
            feed_import.notes = ("\n".join(inner_notes) + feed_import.notes).strip()
            feed_import.save()
    except Exception as exc:  # noqa: BLE001 — one bad file must not kill the batch
        logger.exception("RTA feed import failed for %s", file_name)
        feed_import.status = RTAFeedImport.STATUS_FAILED
        # Row changes were rolled back, so zero the counters to match reality.
        feed_import.rows_total = feed_import.rows_imported = feed_import.rows_duplicate = 0
        feed_import.rows_skipped = feed_import.folios_created = feed_import.clients_linked = 0
        feed_import.notes = ("\n".join(inner_notes + [f"Error: {exc}"])).strip()
        feed_import.save()
    return feed_import


def relink_folios():
    """Re-run PAN auto-linking over unlinked folios (e.g. after adding client
    PANs). Returns the number of folios newly linked."""
    from ..models import MutualFundFolio

    pan_to_client = _client_by_pan()
    linked = 0
    for folio in MutualFundFolio.objects.filter(client__isnull=True).exclude(pan=""):
        client_id = pan_to_client.get(folio.pan)
        if client_id:
            folio.client_id = client_id
            folio.save(update_fields=["client_id", "updated_at"])
            linked += 1
    return linked


# ─── Mailbox fetcher (cron) ─────────────────────────────────────────────────

def fetch_from_mailbox():
    """Pull unread RTA mailback emails over IMAP and import their attachments.

    Silently no-ops unless RTA_FEED_IMAP_HOST/USER/PASSWORD are configured.
    Returns the list of RTAFeedImport rows created.
    """
    from ..models import RTA_CAMS, RTA_KFIN

    host = os.environ.get("RTA_FEED_IMAP_HOST", "").strip()
    user = os.environ.get("RTA_FEED_IMAP_USER", "").strip()
    password = os.environ.get("RTA_FEED_IMAP_PASSWORD", "").strip()
    if not (host and user and password):
        return []

    port = int(os.environ.get("RTA_FEED_IMAP_PORT", "993"))
    folder = os.environ.get("RTA_FEED_IMAP_FOLDER", "INBOX")
    senders = [s.strip().lower() for s in
               os.environ.get("RTA_FEED_SENDERS", "camsonline.com,kfintech.com,karvy.com").split(",")
               if s.strip()]

    imports = []
    mail = imaplib.IMAP4_SSL(host, port)
    try:
        mail.login(user, password)
        mail.select(folder)
        _, data = mail.search(None, "UNSEEN")
        for num in data[0].split():
            _, msg_data = mail.fetch(num, "(RFC822)")
            message = message_from_bytes(msg_data[0][1])
            from_addr = parseaddr(message.get("From", ""))[1].lower()
            if senders and not any(s in from_addr for s in senders):
                continue
            rta_hint = ""
            if "cams" in from_addr:
                rta_hint = RTA_CAMS
            elif "kfin" in from_addr or "karvy" in from_addr:
                rta_hint = RTA_KFIN
            for part in message.walk():
                file_name = part.get_filename() or ""
                if not file_name.lower().endswith(CONTAINER_EXTENSIONS):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                imports.append(import_feed_container(
                    file_name, payload, source="email", rta_hint=rta_hint,
                ))
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return imports
