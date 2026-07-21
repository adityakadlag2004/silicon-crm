from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Safe dictionary lookup"""
    if dictionary is None:
        return None
    try:
        return dictionary.get(key)
    except Exception:
        return None

@register.filter
def div(value, divisor):
    """Safe division filter"""
    try:
        return float(value) / float(divisor)
    except (ZeroDivisionError, TypeError, ValueError):
        return 0

@register.filter
def mul(value, multiplier):
    """Safe multiplication filter"""
    try:
        return float(value) * float(multiplier)
    except (TypeError, ValueError):
        return 0


@register.filter
def indian_number(value, decimal_places=2):
    """Format a number using Indian digit grouping (e.g., 1,23,45,678.90)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value

    is_negative = num < 0
    num = abs(num)

    dec_places = int(decimal_places) if decimal_places is not None else 0
    formatted = f"{num:.{dec_places}f}"

    if '.' in formatted:
        int_part, dec_part = formatted.split('.')
    else:
        int_part, dec_part = formatted, ''

    if len(int_part) > 3:
        last_three = int_part[-3:]
        remaining = int_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.append(remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.append(remaining)
        groups.reverse()
        int_part = ','.join(groups + [last_three])

    if dec_part:
        int_part = f"{int_part}.{dec_part}"

    return f"-{int_part}" if is_negative else int_part


@register.filter
def inr(value):
    """Indian digit grouping: 2063297 -> '20,63,297' (last 3 digits, then
    pairs). Rounds decimals, keeps the minus sign, blanks stay 0."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return "0"
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return sign + ",".join(pairs) + "," + tail


# ── Record-shell helpers (monogram avatars) ──────────────────────────────
# Every module screen leads with a circular two-letter avatar. Initials come
# from the record's name; the colour is derived from that same name so a given
# client keeps one colour everywhere without storing anything.

_MONO_COLORS = [
    "#C2410C", "#B45309", "#15803D", "#0F766E", "#0369A1",
    "#4338CA", "#7E22CE", "#A21CAF", "#BE123C", "#57534E",
]


@register.filter
def monogram(value):
    """Two-letter initials for `value`: "Parekh & Family" -> "PA"."""
    text = str(value or "").strip()
    if not text:
        return "—"
    words = [w for w in text.split() if w[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][:1] + words[1][:1]).upper()
    return text[:2].upper()


@register.filter
def monocolor(value):
    """Stable accent colour for `value` — same name always same colour."""
    text = str(value or "")
    if not text:
        return _MONO_COLORS[0]
    return _MONO_COLORS[sum(ord(c) for c in text) % len(_MONO_COLORS)]


# ── Data masking ─────────────────────────────────────────────────────────
# Not every RM needs full contact details on screen. These mirror the
# reference CRM's masking: enough to recognise a record, not enough to
# exfiltrate a contact list. Masking is display-only — the database and the
# tap-to-call/mailto links still carry the real value.

@register.filter
def mask_phone(value):
    """98******10 — keeps the first two and last two digits."""
    digits = "".join(c for c in str(value or "") if c.isdigit())
    if len(digits) < 6:
        return digits or "—"
    return f"{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


@register.filter
def mask_email(value):
    """rah***a@gmail.com — keeps the domain and the first/last local chars."""
    text = str(value or "").strip()
    if "@" not in text:
        return text or "—"
    local, _, domain = text.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[:3]}***{local[-1]}@{domain}"


@register.filter
def mask_pan(value):
    """ABCD****1F — PAN is a KYC identifier; never show it whole in a list."""
    text = str(value or "").strip().upper()
    if len(text) < 6:
        return text or "—"
    return f"{text[:4]}{'*' * (len(text) - 6)}{text[-2:]}"


# ── Claim stage stepper helpers ──────────────────────────────────────────
_CLAIM_STAGE_ORDER = ["intimated", "file_received", "submitted", "settled"]
_CLAIM_STAGE_LABELS = {
    "intimated": "Intimated", "file_received": "File Received",
    "submitted": "Submitted", "settled": "Settled", "rejected": "Rejected",
}


@register.filter
def claim_status_label(status):
    return _CLAIM_STAGE_LABELS.get(status, status)


@register.filter
def claim_stage_reached(current_status, stage):
    """True when a claim at `current_status` has reached/passed `stage`.

    Rejected claims never count as having reached later stages.
    """
    if current_status == "rejected":
        return False
    try:
        return _CLAIM_STAGE_ORDER.index(current_status) >= _CLAIM_STAGE_ORDER.index(stage)
    except ValueError:
        return False
