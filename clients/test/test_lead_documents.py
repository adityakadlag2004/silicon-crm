"""A lead's documents (quotations) live in Drive under "Leads/<name> (#id)".

Drive is mocked: these pin the folder lifecycle, that a file id from the URL
must belong to the lead's folder, and who may delete the whole folder.

Run: .venv/bin/python manage.py test clients.test.test_lead_documents
"""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Employee, Lead, LeadRemark

DRIVE = "clients.services.google_drive"
FILES = [{"id": "f1", "name": "Quote.pdf", "mimeType": "application/pdf",
          "size": "2048", "modifiedTime": "2026-09-15T10:00:00Z"}]


def _employee(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


class LeadDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("ld_emp")
        cls.other = _employee("ld_other")
        cls.admin = _employee("ld_admin", role="admin")
        cls.lead = Lead.objects.create(customer_name="Sunita Joshi", assigned_to=cls.emp)

    def _as(self, emp):
        c = TestClient()
        c.force_login(emp.user)
        return c

    def _with_folder(self):
        Lead.objects.filter(pk=self.lead.pk).update(drive_folder_id="folder1")

    def test_upload_creates_the_folder_once_and_logs_a_remark(self):
        c = self._as(self.emp)
        with mock.patch(f"{DRIVE}.get_or_create_lead_folder", return_value="folder1") as mk, \
             mock.patch(f"{DRIVE}.upload_file", return_value=("f1", "")) as up:
            for name in ("a.pdf", "b.pdf"):
                c.post(reverse("clients:lead_document_upload", args=[self.lead.pk]),
                       {"document": SimpleUploadedFile(name, b"x", "application/pdf")})
        mk.assert_called_once_with("Sunita Joshi", self.lead.pk)
        self.assertEqual(up.call_count, 2)
        self.assertEqual(up.call_args[0][0], "folder1")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.drive_folder_id, "folder1")
        self.assertTrue(LeadRemark.objects.filter(lead=self.lead, text__contains="b.pdf").exists())

    def test_download_only_streams_files_inside_the_leads_folder(self):
        self._with_folder()
        c = self._as(self.emp)
        with mock.patch(f"{DRIVE}.list_files", return_value=FILES), \
             mock.patch(f"{DRIVE}.stream_file", return_value=(b"PDF", "application/pdf")) as st:
            foreign = c.get(reverse("clients:lead_document", args=[self.lead.pk, "someone-elses"]))
            self.assertEqual(foreign.status_code, 404)
            st.assert_not_called()
            resp = c.get(reverse("clients:lead_document", args=[self.lead.pk, "f1"]) + "?download=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"PDF")
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_delete_refuses_a_file_outside_the_folder(self):
        self._with_folder()
        with mock.patch(f"{DRIVE}.list_files", return_value=FILES), \
             mock.patch(f"{DRIVE}.delete_file", return_value=True) as rm:
            self._as(self.emp).post(
                reverse("clients:lead_document_delete", args=[self.lead.pk, "someone-elses"]))
            rm.assert_not_called()
            self._as(self.emp).post(reverse("clients:lead_document_delete", args=[self.lead.pk, "f1"]))
        rm.assert_called_once_with("f1")

    def test_only_admin_or_manager_deletes_the_whole_folder(self):
        self._with_folder()
        url = reverse("clients:lead_drive_folder_delete", args=[self.lead.pk])
        with mock.patch(f"{DRIVE}.delete_folder") as rm:
            self.assertEqual(self._as(self.emp).post(url).status_code, 403)
            rm.assert_not_called()
            self._as(self.admin).post(url)
        rm.assert_called_once_with("folder1")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.drive_folder_id, "")

    def test_someone_not_on_the_lead_cannot_reach_its_documents(self):
        self._with_folder()
        with mock.patch(f"{DRIVE}.list_files", return_value=FILES):
            resp = self._as(self.other).get(reverse("clients:lead_document", args=[self.lead.pk, "f1"]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_page_survives_drive_being_down(self):
        self._with_folder()
        with mock.patch(f"{DRIVE}.list_files", side_effect=RuntimeError("boom")):
            resp = self._as(self.emp).get(reverse("clients:lead_detail", args=[self.lead.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Could not read this lead")

    def test_app_lists_files_and_gates_folder_delete(self):
        self._with_folder()
        with mock.patch(f"{DRIVE}.list_files", return_value=FILES):
            data = self._as(self.emp).get(
                reverse("clients:app_lead_documents", args=[self.lead.pk])).json()
        self.assertTrue(data["has_folder"])
        self.assertFalse(data["can_delete_folder"])
        self.assertEqual(data["files"][0]["url"], f"/clients/leads/{self.lead.pk}/documents/f1/")
        self.assertEqual(data["files"][0]["size_label"], "2.0\xa0KB")

        url = reverse("clients:app_lead_document_delete", args=[self.lead.pk])
        body = json.dumps({"folder": True})
        with mock.patch(f"{DRIVE}.delete_folder") as rm:
            self.assertEqual(self._as(self.emp).post(url, body, content_type="application/json").status_code, 403)
            self.assertEqual(self._as(self.admin).post(url, body, content_type="application/json").status_code, 200)
        rm.assert_called_once_with("folder1")
