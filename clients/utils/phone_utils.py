"""Phone normalization utilities.
Attempts to use the `phonenumbers` library if installed, otherwise falls back
to a simple India-centric normalizer (assumes 10-digit numbers are IN).

Exports:
- normalize_phone(raw, default_region='IN') -> (e164, wa_number)
  - e164: string like +911234567890 or None if invalid
  - wa_number: string suitable for wa.me (country code + number without plus), e.g. 911234567890

- is_valid_phone(raw) -> bool

"""

try:
    import phonenumbers
    from phonenumbers import NumberParseException
    _HAS_PN = True
except Exception:
    phonenumbers = None
    NumberParseException = Exception
    _HAS_PN = False

import re

_DIGITS = re.compile(r"\d+")


def normalize_phone(raw, default_region='IN'):
    """Normalize a raw phone string.

    Returns (e164, wa_number) or (None, None) if invalid.
    """
    if not raw:
        return None, None
    s = str(raw).strip()
    if _HAS_PN:
        try:
            pn = phonenumbers.parse(s, default_region)
            if not phonenumbers.is_valid_number(pn):
                # fall through to an aggressive digit-based fallback below
                pass
            e164 = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)
            # wa.me wants number without plus and without non-digits
            wa = re.sub(r"\D+", "", e164)
            if wa.startswith('0'):
                # improbable for E164, but trim leading zeros
                wa = wa.lstrip('0')
            return e164, wa
        except NumberParseException:
            # try aggressive fallback below
            pass
    else:
        # Fallback naive normalization (India-centric)
        # extract digits
        digits = ''.join(re.findall(r"\d", s))
        # Common exact matches
        if len(digits) == 10:
            wa = '91' + digits
            e164 = '+' + wa
            return e164, wa
        if len(digits) == 11 and digits.startswith('0'):
            digits = digits.lstrip('0')
            wa = '91' + digits
            return '+' + wa, wa
        if len(digits) >= 11 and digits.startswith('91'):
            wa = digits
            return '+' + wa, wa
        # Aggressive fallback: if there are at least 10 digits, assume last 10 are the local number
        if len(digits) >= 10:
            local = digits[-10:]
            wa = '91' + local
            return '+' + wa, wa
        # can't normalize
        return None, None

    # If phonenumbers was present but parsing/validation failed, try an aggressive digit-only fallback
    # (this helps with numbers stored with extensions or extra characters)
    digits = ''.join(re.findall(r"\d", s))
    if len(digits) >= 10:
        local = digits[-10:]
        wa = '91' + local
        return '+' + wa, wa
    return None, None


def is_valid_phone(raw, default_region='IN'):
    e164, wa = normalize_phone(raw, default_region=default_region)
    return e164 is not None
