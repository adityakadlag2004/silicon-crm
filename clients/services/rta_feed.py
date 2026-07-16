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
    "broker": ["BROKCODE", "BRCODE", "BROKER", "BROKERCODE", "ARN", "ARNCODE", "AGENTCODE",
               "TDBROKER", "TDAGENT"],
    "sub_broker": ["SUBBROK", "SBCODE", "SUBBROKER", "SUBBROKERCODE", "SUBBRCODE", "SUBARN",
                   "SUBARNCODE"],
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
    """CSV/TXT bytes → (headers, row dicts). Sniffs , ; | ~ or tab
    (KFintech's MFSD211 investor master is tilde-delimited)."""
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delimiter = max(",;|~\t", key=sample.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    return list(reader.fieldnames or []), rows


def _excel_rows(file_name, data):
    """Yield rows (lists of cell values) from the first sheet of an Excel file.

    The engine is picked from the file's magic bytes, not its extension —
    KFintech routinely names xlsx content ".xls" (zip magic PK.. = xlsx,
    OLE2 magic = real legacy xls)."""
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
        # OLE2 container without a readable workbook = password-protected
        # workbook (some AMC rejection notices). Not worth a decryption
        # dependency — rejection rows are skipped even when readable.
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
        if is_master_file or (amount is None and units is None and not txn_type):
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
    if rejection_rows:
        feed_import.notes += (
            f"\n{rejection_rows} rejection-notice row(s) skipped — AMC rejection "
            f"reports are informational, not transactions."
        )


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


# ─── Sale ↔ RTA cross-check (Approve Sales evidence) ────────────────────────

SIP_TYPE_TOKENS = ("SIP", "SYSTEMATIC")

# How far around the sale date a matching RTA transaction may fall. SIPs
# especially can start weeks after registration.
SALE_MATCH_DAYS_BEFORE = 7
SALE_MATCH_DAYS_AFTER = 45


def _txn_matches_mode(txn_type, mode):
    from ..models import Product

    upper = (txn_type or "").upper()
    if mode == Product.RTA_MATCH_SIP:
        return any(tok in upper for tok in SIP_TYPE_TOKENS)
    if mode == Product.RTA_MATCH_LUMPSUM:
        return not any(tok in upper for tok in SIP_TYPE_TOKENS)
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

    from django.utils import timezone

    from ..models import MutualFundTransaction

    txns = list(
        MutualFundTransaction.objects.filter(folio__client=client)
        .select_related("folio").order_by("trade_date", "id")
    )
    if not txns:
        return None

    today = timezone.localdate()
    sip_cutoff = today - timedelta(days=35)
    year_cutoff = today - timedelta(days=365)

    monthly_sip = Decimal("0")
    inflow_12m = Decimal("0")
    outflow_12m = Decimal("0")
    units = defaultdict(lambda: Decimal("0"))
    latest_nav = {}

    for txn in txns:
        amount = txn.amount or Decimal("0")
        outflow = _is_outflow(txn.txn_type) or amount < 0
        if txn.trade_date:
            if txn.trade_date >= sip_cutoff and not outflow and \
                    any(tok in (txn.txn_type or "").upper() for tok in SIP_TYPE_TOKENS):
                monthly_sip += abs(amount)
            if txn.trade_date >= year_cutoff:
                if outflow:
                    outflow_12m += abs(amount)
                else:
                    inflow_12m += abs(amount)
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
        "monthly_sip": monthly_sip,
        "inflow_12m": inflow_12m,
        "outflow_12m": outflow_12m,
        "est_value": est_value if est_value > 0 else None,
        "txn_count": len(txns),
        "last_txn_date": max((t.trade_date for t in txns if t.trade_date), default=None),
    }


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
    from ..models import RTA_CAMS, RTA_KFIN

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
        max_messages = int(os.environ.get("RTA_FEED_MAX_MESSAGES", "50"))
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
            # KFintech subscription feeds come as download links, not
            # attachments — follow them when the mail carried no file.
            if rta_hint == RTA_KFIN and message_imports == 0:
                for url in _kfin_report_links(message):
                    imports.append(_import_kfin_link(url))
            mail.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return imports
