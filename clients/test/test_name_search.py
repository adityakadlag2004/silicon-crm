"""First+last name search must match records that carry a middle name."""

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee
from clients.views.helpers import name_words_q


class NameWordsQTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.john = Client.objects.create(name="John Michael Smith")
        cls.jane = Client.objects.create(name="Jane Smith")
        cls.other = Client.objects.create(name="Robert Brown")

    def test_first_and_last_matches_across_middle_name(self):
        got = set(Client.objects.filter(name_words_q("name", "John Smith")))
        self.assertEqual(got, {self.john})

    def test_word_order_does_not_matter(self):
        got = set(Client.objects.filter(name_words_q("name", "smith john")))
        self.assertEqual(got, {self.john})

    def test_single_word_still_works(self):
        got = set(Client.objects.filter(name_words_q("name", "smith")))
        self.assertEqual(got, {self.john, self.jane})

    def test_no_match_when_a_word_is_absent(self):
        self.assertFalse(Client.objects.filter(name_words_q("name", "John Brown")).exists())


class SearchEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user("searcher", password="x")
        Employee.objects.create(user=user, role="employee", salary=0, active=True)
        cls.user = user
        Client.objects.create(name="John Michael Smith", phone="900000001")

    def test_client_search_finds_middle_name_record(self):
        http = TestClient()
        http.force_login(self.user)
        resp = http.get(reverse("clients:search_clients"), {"q": "John Smith"})
        texts = [r["text"] for r in resp.json()["results"]]
        self.assertTrue(any("John Michael Smith" in t for t in texts))
