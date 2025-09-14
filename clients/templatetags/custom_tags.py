from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

@register.filter
def div(value, arg):
    try:
        return float(value) / float(arg) if arg else 0
    except:
        return 0

@register.filter
def mul(value, arg):
    try:
        return float(value) * float(arg)
    except:
        return 0
