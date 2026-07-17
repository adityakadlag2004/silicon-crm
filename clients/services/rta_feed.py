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
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email import message_from_bytes
from email.utils import parseaddr

from django.db import transaction

logger = logging.getLogger(__name__)

DATA_EXTENSIONS = (".dbf", ".csv", ".txt", ".xls", ".xlsx")
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
                 "TRXNNATURE", "TRANTYPE", "NATURE", "TRDESC", "TDTRTYPE"],
    "txn_number": ["TRXNNO", "TDTRNO", "TRANSACTIONNO", "TXNNO", "TRNO", "TRXNID"],
    "trade_date": ["TRADDATE", "TDTRDT", "TRADEDATE", "TXNDATE", "TRANDATE", "TRDATE", "DATE",
                   "TRANSACTIONDATE", "TRXNDATE"],
    "amount": ["AMOUNT", "TDAMT", "AMT", "TRXNAMOUNT", "NETAMOUNT", "GROSSAMT"],
    "units": ["UNITS", "TDUNITS", "UNIT", "TRXNUNITS"],
    "nav": ["PURPRICE", "NAV", "TDNAV", "PRICE", "TRADENAV"],
    "broker": ["BROKCODE", "BRCODE", "BROKER", "BROKERCODE", "ARN", "ARNCODE",
               "TDBROKER"],
    # KFintech DBFs often leave TD_BROKER blank and put the ARN in TD_AGENT —
    # used as a per-row fallback when the broker cell is empty/junk.
    "broker_alt": ["TDAGENT", "AGENTCODE", "AGENT"],
    "sub_broker": ["SUBBROK", "SBCODE", "SUBBROKER", "SUBBROKERCODE", "SUBBRCODE", "SUBARN",
                   "SUBARNCODE"],
}

_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                 "%d/%m/%y", "%m/%d/%Y", "%d %b %Y")

# Column aliases for SIP/STP/SWP *registration* reports (CAMS 'Systematic
# Registration Status', KFintech MFSD243). Written from the real CAMS file
# of 2026-07-11; KFintech names get extended when its first file lands.
SIP_FIELD_ALIASES = {
    "folio": FIELD_ALIASES["folio"],
    "pan": FIELD_ALIASES["pan"],
    "investor_name": FIELD_ALIASES["investor_name"],
    "amc": FIELD_ALIASES["amc"],
    "scheme": ["SCHEMENAME", "SCHEME", "FUNDDESC", "SCHNAME", "SCHEMEDESC"],
    "txn_type": ["TRANSACTIONTYPE", "TRXNTYPE", "TRNTYPE", "REGTYPE", "SIPTYPE"],
    "amount": ["AMOUNT", "INSTALMENTAMOUNT", "INSTALLMENTAMOUNT", "SIPAMOUNT", "TDAMT"],
    "start_date": ["FROMDATE", "STARTDATE", "SIPSTARTDATE", "REGFROMDATE"],
    "end_date": ["TODATE", "ENDDATE", "SIPENDDATE", "REGTODATE"],
    "registered_on": ["REGISTRATIONDATE", "REGDATE", "SIPREGDT", "REGISTEREDON"],
    "installments": ["NOOFINSTALMENTS", "NOOFINSTALLMENTS", "INSTALMENTS", "NOOFINST"],
    "frequency": ["FREQUENCY", "PERIODICITY", "SIPFREQUENCY"],
    "status": ["STATUS", "SIPSTATUS", "REGSTATUS", "REGNSTATUS"],
    "cease_date": ["TERMINATEDATE", "TERMINATIONDATE", "CEASEDATE", "STOPDATE"],
    "registration_ref": ["UKRN", "SIPREFNO", "REGNO", "SIPREGNO", "XSIPREGNO",
                         "SIPREFERENCENO", "REGREFNO"],
    "broker": FIELD_ALIASES["broker"],
    "broker_name": ["AGENTNAME", "BROKERNAME", "AGENTNM", "BROKERNM"],
    "sub_broker": ["SUBBROKERARN", "SUBBROKERRMCODE"] + FIELD_ALIASES["sub_broker"],
}


def _norm_header(name):
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def _build_header_map(headers, aliases=None):
    """{our_field: actual_header} for every alias present in `headers`."""
    normed = {_norm_header(h): h for h in headers}
    mapping = {}
    for field, field_aliases in (aliases or FIELD_ALIASES).items():
        for alias in field_aliases:
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
    """CSV/TXT bytes → (headers, row dicts). Sniffs , ; | ~ or tab
    (KFintech's MFSD211 investor master is tilde-delimited)."""
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delimiter = max(",;|~\t", key=sample.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    return list(reader.fieldnames or []), rows


def _patch_msoffcrypto_biff():
    """msoffcrypto 6.0's legacy-XLS record iterator crashes on files with
    trailing padding bytes (`if not h` only catches a fully-empty read, so a
    1–3 byte tail hits `unpack` and raises struct.error). CAMS encrypts its
    Systematic-Registration .xls files this way — the password verifies but
    decrypt() dies. Patch the EOF check to `len(h) < 4`. Guarded so it
    silently no-ops if the library internals change."""
    try:
        from struct import unpack as _unpack
        from msoffcrypto.format import xls97

        if getattr(xls97._BIFFStream.iter_record, "_ki_patched", False):
            return

        def iter_record(self):
            while True:
                h = self.data.read(4)
                if len(h) < 4:  # empty OR short trailing buffer = EOF
                    break
                num, size = _unpack("<HH", h)
                yield num, size, io.BytesIO(self.data.read(size))

        iter_record._ki_patched = True
        xls97._BIFFStream.iter_record = iter_record
    except Exception:  # noqa: BLE001 — never let a patch failure break imports
        pass


def _decrypt_office(data):
    """Decrypt a password-protected Excel workbook (legacy XLS or OOXML
    inside OLE2) with the configured password candidates.

    Returns decrypted bytes, None if the file isn't encrypted, or raises
    ValueError when it is encrypted but no candidate opens it (CAMS protects
    its Systematic Registration Status report this way)."""
    import msoffcrypto
    from msoffcrypto.exceptions import DecryptionError, InvalidKeyError

    _patch_msoffcrypto_biff()
    office = msoffcrypto.OfficeFile(io.BytesIO(data))
    try:
        if not office.is_encrypted():
            return None
    except Exception:  # noqa: BLE001 — treat unreadable metadata as not encrypted
        return None
    for pw in _password_candidates():
        if not pw:
            continue
        try:
            office = msoffcrypto.OfficeFile(io.BytesIO(data))
            office.load_key(password=pw)
            out = io.BytesIO()
            office.decrypt(out)
            return out.getvalue()
        except (DecryptionError, InvalidKeyError, Exception):  # noqa: BLE001
            continue
    raise ValueError(
        "Encrypted Excel workbook — none of the configured passwords opened it. "
        "Add the file password to RTA_FEED_ZIP_PASSWORDS, or ignore if this is "
        "an AMC rejection notice."
    )


def _excel_rows(file_name, data):
    """Yield rows (lists of cell values) from the first sheet of an Excel file.

    The engine is picked from the file's magic bytes, not its extension —
    KFintech routinely names xlsx content ".xls" (zip magic PK.. = xlsx,
    OLE2 magic = real legacy xls). Password-protected workbooks are
    decrypted first with the configured password candidates."""
    if data[:4] == b"\xd0\xcf\x11\xe0":
        decrypted = _decrypt_office(data)
        if decrypted is not None:
            data = decrypted
    if data[:4] == b"PK\x03\x04" or file_name.lower().endswith(".xlsx"):
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for row in workbook.worksheets[0].iter_rows(values_only=True):
                yield list(row)
        finally:
            workbook.close()
        return

    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.biffh.XLRDError as exc:
        # e.g. an OLE2 wrapper msoffcrypto couldn't see through
        raise ValueError(
            "Encrypted or unsupported Excel workbook. AMC rejection notices "
            "can be ignored; if this is a data feed, request CSV or DBF format."
        ) from exc
    sheet = book.sheet_by_index(0)
    for r in range(sheet.nrows):
        row = []
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
            else:
                row.append(cell.value)
        yield row


def _read_excel(file_name, data):
    """Excel → (headers, row dicts). RTA/NJ sheets often start with title rows,
    so the header is the first row with at least 3 non-empty text cells."""
    rows = list(_excel_rows(file_name, data))
    header_idx = None
    for i, row in enumerate(rows[:15]):
        texty = [v for v in row if isinstance(v, str) and v.strip()]
        if len(texty) >= 3:
            header_idx = i
            break
    if header_idx is None:
        return [], []
    headers = [str(v).strip() if v is not None else "" for v in rows[header_idx]]
    dict_rows = []
    for row in rows[header_idx + 1:]:
        if not any(v not in (None, "") for v in row):
            continue
        dict_rows.append({h: row[i] if i < len(row) else None for i, h in enumerate(headers) if h})
    return [h for h in headers if h], dict_rows


def read_data_file(file_name, data):
    """Return (headers, raw row dicts) for a .dbf/.csv/.txt/.xls(x) payload."""
    lower = file_name.lower()
    if lower.endswith((".xls", ".xlsx")):
        return _read_excel(file_name, data)
    if lower.endswith(".dbf"):
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
                    f"Could not decrypt '{info.filename}' — none of the configured "
                    f"passwords worked. Add the feed password to RTA_FEED_ZIP_PASSWORDS "
                    f"(subscription feeds use the password chosen on the RTA portal)."
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
    rejection_rows = 0
    # A file with no transaction-type column is a folio/investor master
    # (WBR9, MFSD211/311). Masters may still carry unit-balance columns that
    # match the amount/units aliases — never turn those into transactions.
    is_master_file = "txn_type" not in header_map

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

        # AMC "Transaction Rejections" notices carry folio columns but list
        # transactions that did NOT happen — importing them would corrupt
        # the ledger, so they're counted and skipped.
        if "reject" in _clean(get(row, "txn_type")).lower():
            feed_import.rows_skipped += 1
            rejection_rows += 1
            continue

        amc = _clean(get(row, "amc"))[:120]
        pan = _normalize_pan(_clean(get(row, "pan")))[:20]
        investor = _clean(get(row, "investor_name"))[:200]
        broker = _clean(get(row, "broker"))[:40]
        if broker.upper() in ("0", "NOT PROVIDED", "NA", "N.A."):
            broker = ""
        if not broker:
            broker = _clean(get(row, "broker_alt"))[:40]
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

        # a broker-less row inside a folio we've already attributed belongs
        # to that folio's ARN (KFin leaves the broker cell blank on many rows)
        if arn is None and not broker and folio.arn_id:
            arn = folio.arn

        amount = _parse_decimal(get(row, "amount"))
        units = _parse_decimal(get(row, "units"), places=4)
        txn_type = _clean(get(row, "txn_type"))
        if is_master_file or (amount is None and units is None and not txn_type):
            feed_import.rows_imported += 1  # folio-master row
            continue

        trade_date = _parse_date(get(row, "trade_date"))
        txn_number = _clean(get(row, "txn_number"))
        key = _dedupe_key(rta, amc, folio_no, txn_number, txn_type, trade_date, amount, units)
        txn, created = MutualFundTransaction.objects.get_or_create(
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
            # re-imports are self-healing: fill in attribution an earlier
            # import missed (e.g. before the TD_AGENT fallback existed)
            if arn and txn.arn_id is None:
                txn.arn = arn
                if broker and not txn.broker_code:
                    txn.broker_code = broker[:40]
                txn.save(update_fields=["arn", "broker_code"])

    if unmatched_brokers:
        listing = ", ".join(sorted(unmatched_brokers)[:20])
        feed_import.notes += f"\nUnmatched broker codes (add under MF → ARN codes?): {listing}"
    if rejection_rows:
        feed_import.notes += (
            f"\n{rejection_rows} rejection-notice row(s) skipped — AMC rejection "
            f"reports are informational, not transactions."
        )


def _parse_int(value):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _is_sip_registration_file(file_name, headers):
    """SIP/STP/SWP registration reports get routed to the SIP register, not
    the transaction ledger (their rows are plans, not executed trades)."""
    name = (file_name or "").upper()
    if "MFSD243" in name or "SYSTEMATIC_REGISTRATION" in name:
        return True
    normed = {_norm_header(h) for h in headers}
    return "REGISTRATIONDATE" in normed and ("FROMDATE" in normed or "NOOFINSTALMENTS" in normed)


_CEASE_TOKENS = ("cease", "cancel", "terminat", "stop", "reject", "expire")


def import_sip_registrations(rows, headers, *, rta, feed_import):
    """Upsert SipRegistration rows from a registration report. A feed row
    whose status turns to ceased/cancelled marks the registration ceased and
    notifies the admins."""
    from django.contrib.auth.models import User
    from django.urls import reverse
    from django.utils import timezone

    from ..models import ArnAccount, MutualFundFolio, Notification, SipRegistration

    header_map = _build_header_map(headers, SIP_FIELD_ALIASES)
    if "folio" not in header_map:
        feed_import.notes += (
            f"\nSIP registration file: no folio column recognised — headers: "
            f"{', '.join(map(str, headers))}"
        )
        return

    accounts = list(ArnAccount.objects.filter(is_active=True))
    pan_to_client = _client_by_pan()
    newly_ceased = []

    def get(row, field):
        header = header_map.get(field)
        return row.get(header) if header else None

    for row in rows:
        feed_import.rows_total += 1
        folio_no = _clean(get(row, "folio"))[:40]
        if not folio_no:
            feed_import.rows_skipped += 1
            continue

        txn_type = (_clean(get(row, "txn_type")) or "SIP").upper()[:20]
        amount = _parse_decimal(get(row, "amount"))
        start_date = _parse_date(get(row, "start_date"))
        scheme = _clean(get(row, "scheme"))[:200]
        ref = _clean(get(row, "registration_ref"))[:60]
        key = hashlib.sha1("|".join([
            rta or "", folio_no, scheme, str(amount or ""), str(start_date or ""),
            txn_type, ref,
        ]).encode()).hexdigest()

        rta_status = _clean(get(row, "status"))[:40]
        is_ceased = any(tok in rta_status.lower() for tok in _CEASE_TOKENS)
        cease_date = _parse_date(get(row, "cease_date"))
        pan = _normalize_pan(_clean(get(row, "pan")))[:20]
        broker = _clean(get(row, "broker"))[:40]
        sub_broker = _clean(get(row, "sub_broker"))[:40]
        arn = ArnAccount.resolve(broker, sub_broker, accounts=accounts) if broker else None
        folio = MutualFundFolio.objects.filter(folio_number=folio_no).first()
        client_id = (folio.client_id if folio and folio.client_id else None) or pan_to_client.get(pan)

        reg, created = SipRegistration.objects.get_or_create(
            dedupe_key=key,
            defaults={
                "rta": rta or "", "registration_ref": ref, "folio_number": folio_no,
                "folio": folio, "client_id": client_id, "pan": pan,
                "investor_name": _clean(get(row, "investor_name"))[:200],
                "amc_name": _clean(get(row, "amc"))[:120], "scheme_name": scheme,
                "txn_type": txn_type, "amount": amount,
                "frequency": _clean(get(row, "frequency"))[:30],
                "start_date": start_date,
                "end_date": _parse_date(get(row, "end_date")),
                "registered_on": _parse_date(get(row, "registered_on")),
                "installments": _parse_int(get(row, "installments")),
                "broker_code": broker, "sub_broker_code": sub_broker, "arn": arn,
                "broker_name": _clean(get(row, "broker_name"))[:120],
                "rta_status": rta_status,
                "source_import": feed_import,
            },
        )
        if created:
            feed_import.rows_imported += 1
            if is_ceased:
                reg.status = SipRegistration.STATUS_CEASED
                reg.ceased_on = cease_date or timezone.localdate()
                reg.save(update_fields=["status", "ceased_on", "updated_at"])
        else:
            feed_import.rows_duplicate += 1
            changed = []
            if rta_status and reg.rta_status != rta_status:
                reg.rta_status = rta_status
                changed.append("rta_status")
            broker_name = _clean(get(row, "broker_name"))[:120]
            if broker_name and not reg.broker_name:
                reg.broker_name = broker_name
                changed.append("broker_name")
            if is_ceased and reg.status == SipRegistration.STATUS_ACTIVE:
                reg.status = SipRegistration.STATUS_CEASED
                reg.ceased_on = cease_date or timezone.localdate()
                changed += ["status", "ceased_on"]
                newly_ceased.append(reg)
            elif is_ceased and cease_date and reg.ceased_on != cease_date:
                # feed carries the authoritative terminate date — correct ours
                reg.ceased_on = cease_date
                changed.append("ceased_on")
            if client_id and reg.client_id is None:
                reg.client_id = client_id
                changed.append("client_id")
            if folio and reg.folio_id is None:
                reg.folio = folio
                changed.append("folio")
            if changed:
                reg.save(update_fields=changed + ["updated_at"])

    refresh_client_sip_fields()

    if newly_ceased:
        admins = list(User.objects.filter(employee__role="admin", employee__active=True))
        link = reverse("clients:mf_sips") + "?tab=ceased"
        for reg in newly_ceased:
            who = reg.investor_name or f"folio {reg.folio_number}"
            for admin_user in admins:
                Notification.objects.create(
                    recipient=admin_user,
                    title=f"SIP ceased: {who}",
                    body=f"{reg.scheme_name} — ₹{reg.amount or 0}/instalment reported ceased by the RTA.",
                    link=link,
                )


def import_feed_container(file_name, data, *, source, rta_hint="", user=None):
    """Import one container file (zip or bare data file). Returns the
    RTAFeedImport log row; a container already imported is logged as skipped."""
    from ..models import RTAFeedImport

    sha = hashlib.sha256(data).hexdigest()
    # The identical-file guard protects the hourly mail cron from re-parsing.
    # Manual uploads are deliberate — always process them: row-level dedupe
    # keeps them safe and re-imports heal attribution/status the original
    # import missed.
    if (source == RTAFeedImport.SOURCE_EMAIL
            and RTAFeedImport.objects.filter(file_sha256=sha,
                                             status=RTAFeedImport.STATUS_PROCESSED).exists()):
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
                if _is_sip_registration_file(inner_name, headers) or _is_sip_registration_file(file_name, headers):
                    import_sip_registrations(rows, headers, rta=rta, feed_import=feed_import)
                    inner_notes.append(f"{inner_name}: {len(rows)} rows (SIP register)")
                    continue
                if "folio" not in header_map:
                    inner_notes.append(
                        f"{inner_name}: no folio column recognised — headers: {', '.join(map(str, headers))}"
                    )
                    continue
                import_rows(rows, header_map, rta=rta, feed_import=feed_import)
                inner_notes.append(f"{inner_name}: {len(rows)} rows")
            if not found_data_file:
                inner_notes.append("No data files (.dbf/.csv/.txt/.xls) found inside.")
            if feed_import.rows_total == 0 and feed_import.rows_imported == 0:
                # Parsed fine but nothing importable (e.g. NIGO/brokerage reports
                # with no folio column) — skipped, not an error. Real parse and
                # decrypt failures take the except path below and stay 'failed'.
                feed_import.status = RTAFeedImport.STATUS_SKIPPED
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


# Client names in this CRM carry routing tags at the end ("Aditya Kadlag NSE",
# "... NJ"); these are stripped before comparing against RTA investor names.
_NAME_SUFFIX_TOKENS = {"NSE", "NJ"}


def _name_tokens(name):
    words = re.sub(r"[^A-Z ]", " ", (name or "").upper()).split()
    while words and words[-1] in _NAME_SUFFIX_TOKENS:
        words.pop()
    return words


def suggest_folio_matches():
    """Pair unlinked folios with clients by name for the Match Folios screen.

    Unlinked folios are grouped by (PAN, investor name) — one row per
    investor identity, so a single action links every folio of that PAN.
    Match levels: 3 = full name match, 2 = one name contains the other,
    1 = same first two words. Sorted strongest first.
    """
    from ..models import Client, MutualFundFolio

    groups = {}
    for folio in MutualFundFolio.objects.filter(client__isnull=True).order_by("investor_name"):
        tokens = _name_tokens(folio.investor_name)
        if not tokens:
            continue
        key = (folio.pan, " ".join(tokens))
        group = groups.setdefault(key, {
            "pan": folio.pan, "investor_name": folio.investor_name,
            "tokens": tokens, "folios": [],
        })
        group["folios"].append(folio)

    by_full, by_first = {}, {}
    for client in Client.objects.all():
        tokens = _name_tokens(client.name)
        if not tokens:
            continue
        by_full.setdefault(" ".join(tokens), []).append((client, tokens))
        by_first.setdefault(tokens[0], []).append((client, tokens))

    LEVEL_LABELS = {3: "exact name", 2: "name contains", 1: "first + last name"}
    suggestions = []
    for group in groups.values():
        tokens = group["tokens"]
        best = None
        full_hits = by_full.get(" ".join(tokens))
        if full_hits:
            best = (3, full_hits[0][0])
        else:
            for client, client_tokens in by_first.get(tokens[0], []):
                folio_set, client_set = set(tokens), set(client_tokens)
                # middle names: "ADITYA KADLAG" ⊆ "ADITYA SUNIL KADLAG"
                if len(folio_set & client_set) >= 2 and (
                        folio_set <= client_set or client_set <= folio_set):
                    level = 2
                elif (len(tokens) >= 2 and len(client_tokens) >= 2
                      and (tokens[:2] == client_tokens[:2]
                           or tokens[-1] == client_tokens[-1])):
                    level = 1
                else:
                    continue
                if best is None or level > best[0]:
                    best = (level, client)
        if best is None:
            continue
        level, client = best
        suggestions.append({
            "pan": group["pan"],
            "investor_name": group["investor_name"],
            "folios": group["folios"],
            "client": client,
            "level": level,
            "level_label": LEVEL_LABELS[level],
        })
    suggestions.sort(key=lambda s: (-s["level"], s["investor_name"]))
    return suggestions


def relink_folios():
    """Re-run PAN auto-linking over unlinked folios AND unlinked SIP
    registrations (e.g. after adding client PANs). Returns the number of
    records newly linked."""
    from ..models import MutualFundFolio, SipRegistration

    pan_to_client = _client_by_pan()
    linked = 0
    for folio in MutualFundFolio.objects.filter(client__isnull=True).exclude(pan=""):
        client_id = pan_to_client.get(folio.pan)
        if client_id:
            folio.client_id = client_id
            folio.save(update_fields=["client_id", "updated_at"])
            linked += 1

    for reg in SipRegistration.objects.filter(client__isnull=True).select_related("folio"):
        client_id = (
            (reg.folio.client_id if reg.folio_id else None)
            or pan_to_client.get(reg.pan)
        )
        if not client_id and not reg.folio_id:
            # folio may have been created after the registration was imported
            folio = MutualFundFolio.objects.filter(folio_number=reg.folio_number).first()
            if folio:
                reg.folio = folio
                client_id = folio.client_id
        if client_id:
            reg.client_id = client_id
            reg.save(update_fields=["client_id", "folio", "updated_at"])
            linked += 1
    refresh_client_sip_fields()
    return linked


# ─── Sale ↔ RTA cross-check (Approve Sales evidence) ────────────────────────

SIP_TYPE_TOKENS = ("SIP", "SYSTEMATIC")


def _is_sip_type(txn_type):
    """SIP-installment detection across both RTAs' vocabularies — CAMS
    reports installments as bare 'SIN'."""
    upper = (txn_type or "").upper()
    return any(tok in upper for tok in SIP_TYPE_TOKENS) or upper.strip() == "SIN"

# How far around the sale date a matching RTA transaction may fall. SIPs
# especially can start weeks after registration.
SALE_MATCH_DAYS_BEFORE = 7
SALE_MATCH_DAYS_AFTER = 45


def _txn_matches_mode(txn_type, mode):
    from ..models import Product

    if mode == Product.RTA_MATCH_SIP:
        return _is_sip_type(txn_type)
    if mode == Product.RTA_MATCH_LUMPSUM:
        return not _is_sip_type(txn_type)
    return True  # RTA_MATCH_ANY


def rta_evidence_for_sale(sale):
    """Cross-check one pending sale against imported RTA transactions.

    Returns None when the sale's product isn't RTA-linked. Otherwise a dict:
      status  — 'matched' (amount+type+window hit), 'unmatched' (nothing
                comparable yet), or 'no_pan' (client has no PAN to match on)
      txns    — the matching transactions (up to 5)
      nearby  — when unmatched: the client's other feed transactions in the
                window, so the approver still sees what the RTA reported
    Purely advisory: approval stays a human decision.
    """
    from django.db.models import Q

    from ..models import MutualFundFolio, MutualFundTransaction

    product = getattr(sale, "product_ref", None)
    if not product or not product.rta_match:
        return None

    evidence = {"mode": product.rta_match, "status": "no_pan", "txns": [], "nearby": []}
    pan = _normalize_pan(getattr(sale.client, "pan", "") or "")
    folio_filter = Q(client=sale.client)
    if pan:
        folio_filter |= Q(pan=pan)
    folios = MutualFundFolio.objects.filter(folio_filter)
    if not pan and not folios.exists():
        return evidence

    window_start = sale.date - timedelta(days=SALE_MATCH_DAYS_BEFORE)
    window_end = sale.date + timedelta(days=SALE_MATCH_DAYS_AFTER)
    txns = list(
        MutualFundTransaction.objects.filter(
            folio__in=folios, trade_date__range=(window_start, window_end)
        ).select_related("folio").order_by("trade_date")[:200]
    )

    tolerance = max(sale.amount * Decimal("0.02"), Decimal("10"))
    matched, nearby = [], []
    for txn in txns:
        type_ok = _txn_matches_mode(txn.txn_type, product.rta_match)
        amount_ok = txn.amount is not None and abs(txn.amount - sale.amount) <= tolerance
        if type_ok and amount_ok:
            matched.append(txn)
        else:
            nearby.append(txn)

    evidence["status"] = "matched" if matched else "unmatched"
    evidence["txns"] = matched[:5]
    evidence["nearby"] = [] if matched else nearby[:3]
    return evidence


REDEMPTION_TOKENS = ("REDEMPTION", "REDEEM", "SWITCH OUT", "SWITCHOUT", "SWO", "SELL")


def _is_outflow(txn_type):
    return any(tok in (txn_type or "").upper() for tok in REDEMPTION_TOKENS)


def mf_summary_for_client(client):
    """Live MF snapshot for the client profile, recomputed from the imported
    feed on every view: detected monthly SIP (SIP-type inflows in the last 35
    days), 12-month in/outflows, and an *estimated* current value (net units ×
    the last NAV seen per scheme — the AUM feed, once subscribed, is the exact
    number). Returns None when the client has no imported transactions."""
    from collections import defaultdict

    from django.db.models import Sum
    from django.utils import timezone

    from ..models import MutualFundTransaction, SipRegistration

    txns = list(
        MutualFundTransaction.objects.filter(folio__client=client)
        .select_related("folio").order_by("trade_date", "id")
    )
    # the SIP register is authoritative for the live SIP figure — installment
    # inference below is the fallback for clients without register rows
    register_sip = SipRegistration.objects.filter(
        client=client, status=SipRegistration.STATUS_ACTIVE,
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    if not txns:
        if not register_sip:
            return None
        return {
            "monthly_sip": register_sip, "inflow_12m": Decimal("0"),
            "outflow_12m": Decimal("0"), "lumpsum_12m": Decimal("0"),
            "est_value": None, "txn_count": 0, "last_txn_date": None,
        }

    today = timezone.localdate()
    sip_cutoff = today - timedelta(days=35)
    year_cutoff = today - timedelta(days=365)

    monthly_sip = Decimal("0")
    inflow_12m = Decimal("0")
    outflow_12m = Decimal("0")
    lumpsum_12m = Decimal("0")
    units = defaultdict(lambda: Decimal("0"))
    latest_nav = {}

    for txn in txns:
        amount = txn.amount or Decimal("0")
        outflow = _is_outflow(txn.txn_type) or amount < 0
        if txn.trade_date:
            if txn.trade_date >= sip_cutoff and not outflow and _is_sip_type(txn.txn_type):
                monthly_sip += abs(amount)
            if txn.trade_date >= year_cutoff:
                if outflow:
                    outflow_12m += abs(amount)
                else:
                    inflow_12m += abs(amount)
                    if not _is_sip_type(txn.txn_type):
                        lumpsum_12m += abs(amount)
        if txn.units is not None:
            scheme_key = (txn.folio_id, txn.scheme_name)
            delta = txn.units
            if outflow and delta > 0:  # some feeds store redemptions unsigned
                delta = -delta
            units[scheme_key] += delta
            if txn.nav:
                latest_nav[scheme_key] = txn.nav

    est_value = sum(
        (held * latest_nav[key] for key, held in units.items() if held > 0 and key in latest_nav),
        Decimal("0"),
    )

    return {
        "monthly_sip": register_sip if register_sip > 0 else monthly_sip,
        "inflow_12m": inflow_12m,
        "outflow_12m": outflow_12m,
        "lumpsum_12m": lumpsum_12m,
        "est_value": est_value if est_value > 0 else None,
        "txn_count": len(txns),
        "last_txn_date": max((t.trade_date for t in txns if t.trade_date), default=None),
    }


def refresh_client_sip_fields(client_ids=None):
    """Keep Client.sip_amount/sip_status in step with the SIP register.

    For any client that has register rows, the RTA feed is the truth for the
    SIP product columns (sales exist for incentives, not holdings). Clients
    without register rows keep their sales-derived values. Returns the
    number of clients updated.
    """
    from django.db.models import Sum

    from ..models import Client, SipRegistration

    reg_clients = SipRegistration.objects.filter(client__isnull=False)
    if client_ids is not None:
        reg_clients = reg_clients.filter(client_id__in=client_ids)
    covered_ids = set(reg_clients.values_list("client_id", flat=True).distinct())
    totals = {
        row["client_id"]: row["t"] or Decimal("0")
        for row in reg_clients.filter(status=SipRegistration.STATUS_ACTIVE)
        .values("client_id").annotate(t=Sum("amount"))
    }
    updated = 0
    for client in Client.objects.filter(id__in=covered_ids):
        total = totals.get(client.id, Decimal("0"))
        status = total > 0
        if client.sip_amount != total or client.sip_status != status:
            client.sip_amount = total
            client.sip_status = status
            client.save(update_fields=["sip_amount", "sip_status"])
            updated += 1
    return updated


def cob_opportunities():
    """Change-of-Broker targets: SIP installment streams running in our
    clients' folios under some OTHER broker's code (their trail goes to that
    broker until a COB is filed).

    One entry per (folio, outside broker): the client, scheme(s), inferred
    monthly amount (most recent installment), first/last installment dates,
    and whether the stream still looks live (installment within 45 days).
    """
    from collections import defaultdict

    from django.utils import timezone

    from ..models import MutualFundTransaction

    today = timezone.localdate()
    live_cutoff = today - timedelta(days=45)

    txns = (
        MutualFundTransaction.objects.filter(arn__isnull=True)
        .exclude(broker_code="").select_related("folio__client")
        .order_by("trade_date")
    )
    groups = {}
    for txn in txns:
        if not _is_sip_type(txn.txn_type):
            continue
        key = (txn.folio_id, txn.broker_code)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "folio": txn.folio, "client": txn.folio.client if txn.folio else None,
                "broker_code": txn.broker_code, "schemes": set(),
                "n": 0, "total": Decimal("0"),
                "first_date": txn.trade_date, "last_date": txn.trade_date,
                "monthly": Decimal("0"),
            }
        g["n"] += 1
        g["total"] += abs(txn.amount or Decimal("0"))
        if txn.scheme_name:
            g["schemes"].add(txn.scheme_name)
        if txn.trade_date:
            if g["first_date"] is None or txn.trade_date < g["first_date"]:
                g["first_date"] = txn.trade_date
            if g["last_date"] is None or txn.trade_date > g["last_date"]:
                g["last_date"] = txn.trade_date
                g["monthly"] = abs(txn.amount or Decimal("0"))

    # agent names harvested from KFintech SIP-registration rows identify
    # who a foreign code belongs to
    from ..models import SipRegistration

    name_map = dict(
        SipRegistration.objects.exclude(broker_name="").exclude(broker_code="")
        .values_list("broker_code", "broker_name")
    )
    results = []
    for g in groups.values():
        g["live"] = bool(g["last_date"] and g["last_date"] >= live_cutoff)
        g["schemes"] = sorted(g["schemes"])
        g["broker_name"] = name_map.get(g["broker_code"], "")
        results.append(g)
    results.sort(key=lambda g: (not g["live"], -g["monthly"]))
    return results


def outside_flows_by_client(days=365):
    """All money movement (any transaction type) in our clients' folios under
    other brokers' codes, grouped per client — the 'my client also invests
    elsewhere' view."""
    from collections import defaultdict

    from django.utils import timezone

    from ..models import MutualFundTransaction

    cutoff = timezone.localdate() - timedelta(days=days)
    txns = (
        MutualFundTransaction.objects.filter(
            arn__isnull=True, trade_date__gte=cutoff, folio__client__isnull=False)
        .exclude(broker_code="").select_related("folio__client")
    )
    per_client = {}
    for txn in txns:
        client = txn.folio.client
        g = per_client.get(client.id)
        if g is None:
            g = per_client[client.id] = {
                "client": client, "total": Decimal("0"), "n": 0,
                "codes": set(), "folios": set(), "last_date": None,
            }
        g["total"] += abs(txn.amount or Decimal("0"))
        g["n"] += 1
        g["codes"].add(txn.broker_code)
        g["folios"].add(txn.folio.folio_number)
        if txn.trade_date and (g["last_date"] is None or txn.trade_date > g["last_date"]):
            g["last_date"] = txn.trade_date
    results = sorted(per_client.values(), key=lambda g: -g["total"])
    for g in results:
        g["codes"] = sorted(g["codes"])
        g["folios"] = sorted(g["folios"])
    return results


# ─── Mailbox fetcher (cron) ─────────────────────────────────────────────────

def _configured_mailboxes():
    """Feed mailboxes from env. The primary uses RTA_FEED_IMAP_*; additional
    mailboxes (e.g. the NJ-registered email) use the same names suffixed with
    _2, _3, _4 — see .env.example."""
    mailboxes = []
    for suffix in ("", "_2", "_3", "_4"):
        host = os.environ.get(f"RTA_FEED_IMAP_HOST{suffix}", "").strip()
        user = os.environ.get(f"RTA_FEED_IMAP_USER{suffix}", "").strip()
        password = os.environ.get(f"RTA_FEED_IMAP_PASSWORD{suffix}", "").strip()
        if host and user and password:
            mailboxes.append({
                "host": host, "user": user, "password": password,
                "port": int(os.environ.get(f"RTA_FEED_IMAP_PORT{suffix}", "993")),
                "folder": os.environ.get(f"RTA_FEED_IMAP_FOLDER{suffix}", "INBOX"),
            })
    return mailboxes


def fetch_from_mailbox():
    """Pull unread feed emails from every configured mailbox and import their
    attachments. Silently no-ops when no RTA_FEED_IMAP_* mailbox is configured.
    Returns the list of RTAFeedImport rows created."""
    imports = []
    for box in _configured_mailboxes():
        try:
            imports.extend(_fetch_one_mailbox(box))
        except Exception:  # noqa: BLE001 — one broken mailbox must not block the rest
            logger.exception("RTA feed mailbox fetch failed for %s", box["user"])
    return imports


def _decode_kfin_tracking_link(href):
    """KFintech wraps real URLs in an scdelivery.kfintech.com tracker whose
    `u=` param is the target URL with every character shifted +1
    ("https://" → "iuuqt;00"). Returns the decoded URL, or None."""
    try:
        parsed = urllib.parse.urlparse(href)
    except ValueError:
        return None
    if "scdelivery.kfintech.com" not in (parsed.netloc or ""):
        return None
    wrapped = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
    if not wrapped or wrapped == "undefined":
        return None
    decoded = "".join(chr(ord(c) - 1) for c in wrapped)
    return decoded if decoded.startswith("http") else None


def _kfin_report_links(message):
    """Download links for subscribed KFintech reports.

    KFintech subscription feeds (e.g. MFSD307 Transaction Feeds) arrive as a
    'Click Here' link, not an attachment. Only report-request URLs on
    mfs.kfintech.com qualify — the FUNCODES-PRODCODE scheme master and
    marketing links are ignored."""
    links = []
    for part in message.walk():
        if part.get_content_type() not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode("utf-8", "ignore")
        for href in re.findall(r'https?://[^\s"\'<>]+', text):
            real = _decode_kfin_tracking_link(href)
            if not real:
                continue
            host = urllib.parse.urlparse(real).netloc.lower()
            if host != "mfs.kfintech.com" or "funcodes" in real.lower():
                continue
            if "/requests/" in real.lower() and real not in links:
                links.append(real)
    return links


def _import_kfin_link(url):
    """Download one KFintech report link and run it through the importer.
    Failures (expired link, HTML error page) land in the import log."""
    from ..models import RTAFeedImport, RTA_KFIN

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=90) as response:
            disposition = response.headers.get("Content-Disposition", "")
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 — one dead link must not kill the batch
        return RTAFeedImport.objects.create(
            source=RTAFeedImport.SOURCE_EMAIL, file_name=url[:255], file_sha256="",
            rta=RTA_KFIN, status=RTAFeedImport.STATUS_FAILED,
            notes=f"Report link download failed: {exc}",
        )

    name_match = re.search(r'filename="?([^";]+)', disposition)
    if name_match:
        file_name = name_match.group(1).strip()
    elif payload[:2] == b"PK":
        file_name = "kfin_report.zip"
    else:
        return RTAFeedImport.objects.create(
            source=RTAFeedImport.SOURCE_EMAIL, file_name=url[:255],
            file_sha256=hashlib.sha256(payload).hexdigest(),
            rta=RTA_KFIN, status=RTAFeedImport.STATUS_FAILED,
            notes=("Report link did not return a data file (expired link?): "
                   + payload[:80].decode("utf-8", "replace")),
        )
    return import_feed_container(file_name, payload, source=RTAFeedImport.SOURCE_EMAIL,
                                 rta_hint=RTA_KFIN)


def _fetch_one_mailbox(box):
    from ..models import RTA_CAMS, RTA_KFIN, RTAFeedImport

    senders = [s.strip().lower() for s in
               os.environ.get("RTA_FEED_SENDERS", "camsonline.com,kfintech.com,karvy.com").split(",")
               if s.strip()]

    imports = []
    mail = imaplib.IMAP4_SSL(box["host"], box["port"])
    try:
        mail.login(box["user"], box["password"])
        mail.select(box["folder"])
        # A long-lived AMFI-registered inbox can hold years of unread RTA
        # mail — automation only needs the fresh files, so scope the search
        # to recent days per sender and cap how many messages one run eats.
        days = int(os.environ.get("RTA_FEED_SINCE_DAYS", "7"))
        max_messages = int(os.environ.get("RTA_FEED_MAX_MESSAGES", "200"))
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        message_ids = []
        if senders:
            for sender in senders:
                _, data = mail.search(None, f'(UNSEEN FROM "{sender}" SINCE "{since}")')
                for num in (data[0] or b"").split():
                    if num not in message_ids:
                        message_ids.append(num)
        else:
            _, data = mail.search(None, f'(UNSEEN SINCE "{since}")')
            message_ids = list((data[0] or b"").split())
        message_ids.sort(key=int)
        for num in message_ids[-max_messages:]:
            # PEEK so scanning never marks mail read — only messages we actually
            # process get flagged Seen below. Keeps shared mailboxes untouched.
            _, msg_data = mail.fetch(num, "(BODY.PEEK[])")
            message = message_from_bytes(msg_data[0][1])
            from_addr = parseaddr(message.get("From", ""))[1].lower()
            if senders and not any(s in from_addr for s in senders):
                continue
            rta_hint = ""
            if "cams" in from_addr:
                rta_hint = RTA_CAMS
            elif "kfin" in from_addr or "karvy" in from_addr:
                rta_hint = RTA_KFIN
            message_imports = 0
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
                message_imports += 1
            # KFintech mailback reports come as download links, not
            # attachments — follow them when the mail carried no file.
            # 'Subscribed ...' mails are the series-3 subscription feeds:
            # password-locked and redundant with the series-2 mailback
            # reports (decision 2026-07-16) — not followed.
            subject = str(message.get("Subject") or "").lower()
            keep_unread = False
            if (rta_hint == RTA_KFIN and message_imports == 0
                    and not subject.startswith("subscribed")):
                for url in _kfin_report_links(message):
                    result = _import_kfin_link(url)
                    imports.append(result)
                    # a dead/not-ready link is transient — leave the mail
                    # unread so the next hourly run retries it
                    if (result.status == RTAFeedImport.STATUS_FAILED
                            and "link" in (result.notes or "").lower()):
                        keep_unread = True
            if not keep_unread:
                mail.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return imports
