from django.test import TestCase
from django.contrib.auth import get_user_model
from app.models import Employee, Client, ClientMappingAudit

User = get_user_model()

class ClientReassignTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u1', password='pass')
        self.user2 = User.objects.create_user(username='u2', password='pass')
        self.emp1 = Employee.objects.create(user=self.user, role='employee')
        self.emp2 = Employee.objects.create(user=self.user2, role='employee')
        self.client = Client.objects.create(name='C1')

    def test_reassign_creates_audit(self):
        changed, prev, new = self.client.reassign_to(self.emp1, changed_by=self.user, note='initial')
        self.assertTrue(changed)
        self.assertEqual(self.client.mapped_to, self.emp1)

        changed2, prev2, new2 = self.client.reassign_to(self.emp2, changed_by=self.user2, note='handover')
        self.assertTrue(changed2)
        self.client.refresh_from_db()
        self.assertEqual(self.client.mapped_to, self.emp2)

        audits = ClientMappingAudit.objects.filter(client=self.client).order_by('changed_at')
        self.assertEqual(audits.count(), 2)
        first = audits[0]
        self.assertIsNone(first.previous_employee)  # first reassign previous was None
        self.assertEqual(first.new_employee, self.emp1)
        self.assertEqual(first.changed_by, self.user)
        self.assertEqual(audits[1].new_employee, self.emp2)
        self.assertEqual(audits[1].changed_by, self.user2)
