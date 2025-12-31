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
