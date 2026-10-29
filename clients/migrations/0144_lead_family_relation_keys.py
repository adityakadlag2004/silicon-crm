"""Map the free-text family relations onto the tree's fixed vocabulary.

`relation` was typed by hand ("Wife", "Father in law", "beta"); the family
tree places people by key. Anything not recognised becomes "other" with the
typed word kept at the top of `notes`, so nothing anyone wrote is lost.

Kept apart from 0143: that one builds the `via` index, and a deferred CREATE
INDEX cannot follow a bulk row update in one Postgres transaction.
"""
import re

from django.db import migrations

WORDS = {
    "spouse": "spouse", "wife": "spouse", "husband": "spouse",
    "father": "father", "dad": "father", "papa": "father",
    "mother": "mother", "mom": "mother", "mummy": "mother", "maa": "mother",
    "son": "son", "daughter": "daughter",
    "brother": "brother", "sister": "sister",
    "daughter in law": "daughter_in_law", "son in law": "son_in_law",
    "grandson": "grandson", "grand son": "grandson",
    "granddaughter": "granddaughter", "grand daughter": "granddaughter",
    "father in law": "father_in_law", "mother in law": "mother_in_law",
}
KEYS = set(WORDS.values()) | {"other"}


def relation_key(text):
    """(key, leftover) — leftover is the typed word when it had to become "other"."""
    raw = (text or "").strip()
    if raw in KEYS:
        return raw, ""
    word = re.sub(r"[\s_\-]+", " ", raw.lower()).strip()
    if word in WORDS:
        return WORDS[word], ""
    return "other", raw


def forwards(apps, schema_editor):
    Member = apps.get_model("clients", "LeadFamilyMember")
    for m in Member.objects.all().only("id", "relation", "notes"):
        key, leftover = relation_key(m.relation)
        if key == m.relation:
            continue
        m.relation = key
        if leftover:
            m.notes = f"Relation: {leftover}\n{m.notes}".strip()
        m.save(update_fields=["relation", "notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0143_lead_profile_family_tree_goals"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
