"""Everything a lead page holds about the person: the family tree, goals,
the profile and the catch-up card.

Run: .venv/bin/python manage.py test clients.test.test_lead_family_tree
"""
import importlib
from datetime import date

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Employee, Lead, LeadFamilyMember, LeadGoal
from clients.services import leads as lead_service

relation_key = importlib.import_module(
    "clients.migrations.0144_lead_family_relation_keys").relation_key


def _employee(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


def _born(years_ago):
    today = timezone.localdate()
    return date(today.year - years_ago, 1, 1)


class FamilyTreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("ft_emp")
        cls.lead = Lead.objects.create(customer_name="Ramesh Patil", assigned_to=cls.emp)

    def _add(self, name, relation, **kw):
        return LeadFamilyMember.objects.create(lead=self.lead, name=name, relation=relation, **kw)

    def test_everyone_lands_in_their_slot_and_nobody_is_dropped(self):
        father = self._add("Vasant", "father")
        wife = self._add("Sunita", "spouse")
        younger = self._add("Amit", "son", date_of_birth=_born(20))
        elder = self._add("Priya", "daughter", date_of_birth=_born(30))
        son_in_law = self._add("Kunal", "son_in_law", via=elder)
        grandkid = self._add("Anaya", "granddaughter", via=elder)
        unlinked = self._add("Ved", "grandson")            # no `via` yet
        sister = self._add("Meena", "sister")
        mil = self._add("Kamala", "mother_in_law")
        second_wife = self._add("Asha", "spouse")

        tree = lead_service.family_tree(self.lead)

        self.assertEqual((tree["father"], tree["mother"]), (father, None))
        self.assertIn(tree["spouse"], (wife, second_wife))
        self.assertEqual(tree["siblings"], [sister])
        self.assertEqual([u["member"] for u in tree["children"]], [elder, younger])  # eldest first
        priya = tree["children"][0]
        self.assertEqual((priya["spouse"], priya["kids"]), (son_in_law, [grandkid]))
        self.assertEqual(tree["children"][1]["spouse"], None)
        placed = {tree["father"], tree["spouse"], sister, elder, younger, son_in_law, grandkid}
        self.assertEqual(set(tree["others"]), {unlinked, mil, wife, second_wife} - placed)
        self.assertEqual(len(placed) + len(tree["others"]), self.lead.family_members.count())

    def test_only_a_grown_child_gets_slots_for_a_spouse_and_children(self):
        self._add("Kid", "son", date_of_birth=_born(10))
        self._add("Unknown age", "daughter")
        grown = {u["member"].name: u["grown"] for u in lead_service.family_tree(self.lead)["children"]}
        self.assertEqual(grown, {"Kid": False, "Unknown age": True})


class LeadPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("lp_emp")
        cls.other = _employee("lp_other")
        cls.lead = Lead.objects.create(customer_name="Kavita Deshmukh", assigned_to=cls.emp)
        cls.other_lead = Lead.objects.create(customer_name="Someone Else", assigned_to=cls.other)

    def _client(self, emp=None):
        c = TestClient()
        c.force_login((emp or self.emp).user)
        return c

    def _page(self):
        return self._client().get(reverse("clients:lead_detail", args=[self.lead.pk])).content.decode()

    def test_a_lead_with_no_family_still_shows_the_whole_tree_to_fill_in(self):
        html = self._page()
        for slot in ("Add father", "Add mother", "Add spouse", "Add child", "Add sibling"):
            self.assertIn(f'aria-label="{slot}"', html)
        self.assertIn("ki-fnode-self", html)
        self.assertIn("None booked", html)       # catch-up: no follow-up yet

    def test_catch_up_leads_with_the_last_conversation_and_history_merges_moves(self):
        lead_service.add_remark(self.lead, "Wants a term quote by Friday", user=self.emp.user)
        lead_service.set_stage(self.lead, Lead.STAGE_PROSPECT, user=self.emp.user, note="Budget confirmed")
        html = self._page()
        catch_up = html[html.index('id="catch-up"'):html.index('id="family"')]
        self.assertIn("Wants a term quote by Friday", catch_up)
        history = html[html.index("bi-clock-history\"></i> History"):]
        self.assertIn("Budget confirmed", history)
        self.assertIn("Wants a term quote by Friday", history)

    def test_add_edit_and_remove_a_relative(self):
        c = self._client()
        add = reverse("clients:lead_family_add", args=[self.lead.pk])
        c.post(add, {"name": "Rohan", "relation": "son", "date_of_birth": "1995-04-02"})
        rohan = self.lead.family_members.get()
        c.post(add, {"name": "Neha", "relation": "daughter_in_law", "via": rohan.pk})
        neha = self.lead.family_members.get(name="Neha")
        self.assertEqual(neha.via, rohan)

        edit = reverse("clients:lead_family_edit", args=[self.lead.pk, neha.pk])
        c.post(edit, {"name": "Neha R", "relation": "daughter_in_law", "via": rohan.pk, "phone": "9876543210.0"})
        neha.refresh_from_db()
        self.assertEqual((neha.name, neha.phone), ("Neha R", "9876543210"))

        c.post(edit, {"delete": "1"})
        self.assertFalse(self.lead.family_members.filter(pk=neha.pk).exists())

    def test_via_must_be_one_of_this_leads_children(self):
        theirs = LeadFamilyMember.objects.create(lead=self.other_lead, name="Not ours", relation="son")
        resp = self._client().post(reverse("clients:lead_family_add", args=[self.lead.pk]),
                                   {"name": "Sneaky", "relation": "grandson", "via": theirs.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.lead.family_members.exists())

    def test_via_is_dropped_for_a_relation_that_does_not_hang_off_a_child(self):
        son = LeadFamilyMember.objects.create(lead=self.lead, name="Son", relation="son")
        self._client().post(reverse("clients:lead_family_add", args=[self.lead.pk]),
                            {"name": "Dad", "relation": "father", "via": son.pk})
        self.assertIsNone(self.lead.family_members.get(name="Dad").via)

    def test_a_lead_you_cannot_see_cannot_be_written_to(self):
        resp = self._client(self.other).post(reverse("clients:lead_family_add", args=[self.lead.pk]),
                                             {"name": "X", "relation": "father"})
        self.assertEqual(resp.status_code, 404)
        member = LeadFamilyMember.objects.create(lead=self.other_lead, name="Theirs", relation="father")
        resp = self._client().post(reverse("clients:lead_family_edit", args=[self.lead.pk, member.pk]),
                                   {"delete": "1"})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(LeadFamilyMember.objects.filter(pk=member.pk).exists())

    def test_the_lead_form_does_not_blank_a_link_set_on_the_tree(self):
        son = LeadFamilyMember.objects.create(lead=self.lead, name="Son", relation="son")
        dil = LeadFamilyMember.objects.create(lead=self.lead, name="DIL", relation="daughter_in_law", via=son)
        payload = {
            "customer_name": self.lead.customer_name, "assigned_to": self.emp.pk,
            "family-TOTAL_FORMS": "2", "family-INITIAL_FORMS": "2",
            "family-MIN_NUM_FORMS": "0", "family-MAX_NUM_FORMS": "1000",
            "interest-TOTAL_FORMS": "0", "interest-INITIAL_FORMS": "0",
            "interest-MIN_NUM_FORMS": "0", "interest-MAX_NUM_FORMS": "1000",
        }
        for i, m in enumerate(sorted([son, dil], key=lambda m: m.name)):
            payload.update({f"family-{i}-id": m.pk, f"family-{i}-name": m.name,
                            f"family-{i}-relation": m.relation})
        resp = self._client().post(reverse("clients:lead_update", args=[self.lead.pk]), payload)
        self.assertEqual(resp.status_code, 302)
        dil.refresh_from_db()
        self.assertEqual(dil.via, son)

    def test_goals_add_edit_remove_and_stay_inside_the_family(self):
        c = self._client()
        kid = LeadFamilyMember.objects.create(lead=self.lead, name="Isha", relation="daughter")
        c.post(reverse("clients:lead_goal_add", args=[self.lead.pk]),
               {"kind": "child_education", "member": kid.pk, "target_amount": "2500000", "target_year": "2034"})
        goal = self.lead.goals.get()
        self.assertEqual((goal.member, goal.target_year), (kid, 2034))
        self.assertIn("Child&#x27;s education", self._page())

        theirs = LeadFamilyMember.objects.create(lead=self.other_lead, name="Not ours", relation="son")
        edit = reverse("clients:lead_goal_edit", args=[self.lead.pk, goal.pk])
        c.post(edit, {"kind": "house", "member": theirs.pk})
        goal.refresh_from_db()
        self.assertEqual(goal.kind, "child_education")   # refused, unchanged

        c.post(edit, {"delete": "1"})
        self.assertFalse(LeadGoal.objects.exists())


class ProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("pr_emp", role="admin")

    def test_a_converted_lead_hands_its_date_of_birth_to_the_client(self):
        lead = Lead.objects.create(customer_name="Anil Kale", assigned_to=self.emp,
                                   stage=Lead.STAGE_ORDER, date_of_birth=date(1980, 6, 15))
        client = lead_service.convert_to_client(lead, user=self.emp.user)
        self.assertEqual(client.date_of_birth, date(1980, 6, 15))

    def test_the_lead_form_refuses_a_future_birth_date(self):
        from clients.forms import LeadForm
        form = LeadForm({"customer_name": "X", "assigned_to": self.emp.pk, "stage": "suspect",
                         "date_of_birth": "2999-01-01"}, user=self.emp.user)
        self.assertIn("date_of_birth", form.errors)


class RelationMigrationTests(TestCase):
    def test_typed_relations_map_onto_the_tree_and_unknown_words_are_kept(self):
        self.assertEqual(relation_key("Wife"), ("spouse", ""))
        self.assertEqual(relation_key(" Father-in-law "), ("father_in_law", ""))
        self.assertEqual(relation_key("daughter_in_law"), ("daughter_in_law", ""))
        self.assertEqual(relation_key("Grand Son"), ("grandson", ""))
        self.assertEqual(relation_key("Mama"), ("other", "Mama"))
        self.assertEqual(relation_key(""), ("other", ""))
