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
