"""Mutual Funds RTA-feed pipeline tests: file parsing (CSV/DBF/zip), the
importer (folio upsert, PAN auto-link, ARN attribution incl. the NJ
sub-broker case, dedupe), and the admin screens.

Run: .venv/bin/python manage.py test clients.test.test_mf_rta_feeds -v 2
"""
import io
import struct

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client as TestClient
from django.test import TestCase
from django.urls import reverse

from clients.models import (
    ArnAccount,
    Client,
    Employee,
    MutualFundFolio,
    MutualFundTransaction,
    RTAFeedImport,
    RTA_CAMS,
    RTA_KFIN,
)
from clients.services import rta_feed

CAMS_CSV = (
    "AMC_CODE,FOLIO_NO,INV_NAME,PAN,TRXNTYPE,TRXNNO,TRADDATE,AMOUNT,UNITS,PURPRICE,BROKCODE,SUBBROK,SCHEME\n"
    "H,1234567/89,Rahul Sharma,ABCDE1234F,Purchase,T0001,01-Jul-2026,10000.00,100.5000,99.5025,ARN-777,,HDFC Flexi Cap Dir\n"
    "H,1234567/89,Rahul Sharma,ABCDE1234F,SIP,T0002,05-Jul-2026,5000.00,50.2500,99.5025,ARN-777,,HDFC Flexi Cap Dir\n"
    "I,555111,Priya Patel,FGHIJ5678K,Purchase,T0003,02-Jul-2026,20000.00,80.0000,250.0000,ARN-0155,SB123,ICICI Bluechip\n"
).encode()

KFIN_CSV = (
    "FMCODE,TD_ACNO,INVNAME,PANGNO,TRNDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_NAV,BRCODE,SBCODE,FUNDDESC\n"
    "102,777888,Rahul Sharma,ABCDE1234F,Systematic,K001,03/07/2026,3000.00,10.1234,296.3400,ARN-777,,Axis Small Cap\n"
).encode()


def make_dbf(fields, records):
    """Minimal dBase-III file: all fields are character type.
    `fields` = [(name, length)], `records` = [dict]."""
    header_size = 32 + 32 * len(fields) + 1
    record_size = 1 + sum(length for _, length in fields)
    out = bytearray()
    out += bytes([0x03, 26, 7, 15])                      # version, last-update date
    out += struct.pack("<I", len(records))
    out += struct.pack("<HH", header_size, record_size)
    out += b"\x00" * 20
    for name, length in fields:
        out += name.encode().ljust(11, b"\x00")
        out += b"C" + b"\x00" * 4 + bytes([length, 0]) + b"\x00" * 14
    out += b"\x0d"
    for rec in records:
        out += b" "
        for name, length in fields:
            out += str(rec.get(name, "")).encode()[:length].ljust(length, b" ")
    out += b"\x1a"
    return bytes(out)


class ParserTests(TestCase):
    def test_csv_headers_and_rows(self):
        headers, rows = rta_feed.read_data_file("wbr2.csv", CAMS_CSV)
        self.assertIn("FOLIO_NO", headers)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["INV_NAME"], "Rahul Sharma")

    def test_header_alias_mapping_cams_and_kfin(self):
        cams_map = rta_feed._build_header_map(
            ["AMC_CODE", "FOLIO_NO", "INV_NAME", "PAN", "TRXNTYPE", "TRADDATE", "AMOUNT", "BROKCODE"])
        self.assertEqual(cams_map["folio"], "FOLIO_NO")
        self.assertEqual(cams_map["broker"], "BROKCODE")
        kfin_map = rta_feed._build_header_map(
            ["FMCODE", "TD_ACNO", "PANGNO", "TRNDESC", "TD_TRDT", "TD_AMT", "BRCODE"])
        self.assertEqual(kfin_map["folio"], "TD_ACNO")
        self.assertEqual(kfin_map["pan"], "PANGNO")

    def test_dbf_roundtrip(self):
        data = make_dbf(
            [("FOLIO_NO", 12), ("INV_NAME", 20), ("AMOUNT", 10)],
            [{"FOLIO_NO": "99887766", "INV_NAME": "Test Investor", "AMOUNT": "1500.00"}],
        )
        headers, rows = rta_feed.read_data_file("wbr9.dbf", data)
        self.assertEqual(headers, ["FOLIO_NO", "INV_NAME", "AMOUNT"])
        self.assertEqual(rows[0]["FOLIO_NO"].strip(), "99887766")

    def test_date_and_decimal_parsing(self):
        self.assertEqual(str(rta_feed._parse_date("01-Jul-2026")), "2026-07-01")
        self.assertEqual(str(rta_feed._parse_date("03/07/2026")), "2026-07-03")
        self.assertIsNone(rta_feed._parse_date("garbage"))
        self.assertEqual(str(rta_feed._parse_decimal("1,00,000.50")), "100000.50")
        self.assertIsNone(rta_feed._parse_decimal("N/A"))

    def test_detect_rta(self):
        self.assertEqual(rta_feed.detect_rta("WBR2_ARN777_140726.dbf"), RTA_CAMS)
        self.assertEqual(rta_feed.detect_rta("MFSD201_daily.csv"), RTA_KFIN)
        self.assertEqual(rta_feed.detect_rta("feed.csv", ["TD_ACNO", "FMCODE"]), RTA_KFIN)
        self.assertEqual(rta_feed.detect_rta("feed.csv", ["AMC_CODE", "TRXNNO"]), RTA_CAMS)

    def test_password_protected_zip_extraction(self):
        import pyzipper

        ArnAccount.objects.create(label="Direct (NSE)", arn_code="ARN-777")
        buf = io.BytesIO()
        with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(b"ARN777")  # RTA zips use the ARN code, no hyphen
            zf.writestr("WBR2_data.csv", CAMS_CSV)
        files = list(rta_feed.extract_data_files("mailback.zip", buf.getvalue(),
                                                 rta_feed._password_candidates()))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][0], "WBR2_data.csv")
        self.assertEqual(files[0][1], CAMS_CSV)

    def test_zip_with_wrong_password_reports_clearly(self):
        import pyzipper

        buf = io.BytesIO()
        with pyzipper.AESZipFile(buf, "w", encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(b"something-else")
            zf.writestr("WBR2_data.csv", CAMS_CSV)
        with self.assertRaises(ValueError):
            list(rta_feed.extract_data_files("mailback.zip", buf.getvalue(), ["ARN777"]))


class ImporterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.direct = ArnAccount.objects.create(label="Direct (NSE)", arn_code="ARN-777")
        cls.nj = ArnAccount.objects.create(label="NJ", arn_code="ARN-0155", sub_broker_code="SB123")
        cls.rahul = Client.objects.create(name="Rahul Sharma", pan="ABCDE1234F")

    def test_import_creates_folios_transactions_and_links_by_pan(self):
        feed_import = rta_feed.import_feed_container(
            "WBR2_140726.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(feed_import.rows_imported, 3)
        self.assertEqual(feed_import.folios_created, 2)
        self.assertEqual(MutualFundTransaction.objects.count(), 3)

        rahul_folio = MutualFundFolio.objects.get(folio_number="1234567/89")
        self.assertEqual(rahul_folio.client, self.rahul)      # PAN auto-link
        self.assertEqual(rahul_folio.arn, self.direct)
        self.assertEqual(rahul_folio.rta, RTA_CAMS)

    def test_nj_subbroker_attribution(self):
        rta_feed.import_feed_container("WBR2_140726.csv", CAMS_CSV,
                                       source=RTAFeedImport.SOURCE_UPLOAD)
        priya_folio = MutualFundFolio.objects.get(folio_number="555111")
        self.assertEqual(priya_folio.arn, self.nj)
        txn = priya_folio.transactions.get()
        self.assertEqual(txn.arn, self.nj)
        self.assertEqual(txn.sub_broker_code, "SB123")

    def test_same_file_skipped_and_same_rows_deduped(self):
        rta_feed.import_feed_container("a.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        again = rta_feed.import_feed_container("a.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(again.status, RTAFeedImport.STATUS_SKIPPED)

        # Same rows arriving in a *different* file (e.g. weekly vs daily report)
        renamed = CAMS_CSV + b"\n"
        third = rta_feed.import_feed_container("b.csv", renamed, source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(third.rows_duplicate, 3)
        self.assertEqual(third.rows_imported, 0)
        self.assertEqual(MutualFundTransaction.objects.count(), 3)

    def test_kfin_file_and_cross_rta_folio(self):
        rta_feed.import_feed_container("MFSD201.csv", KFIN_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        folio = MutualFundFolio.objects.get(folio_number="777888")
        self.assertEqual(folio.rta, RTA_KFIN)
        self.assertEqual(folio.client, self.rahul)
        self.assertEqual(folio.transactions.get().scheme_name, "Axis Small Cap")

    def test_unmatched_broker_code_is_reported(self):
        csv_data = (
            "FOLIO_NO,INV_NAME,TRXNTYPE,TRXNNO,TRADDATE,AMOUNT,BROKCODE\n"
            "111,Someone,Purchase,X1,01-Jul-2026,100,ARN-999999\n"
        ).encode()
        feed_import = rta_feed.import_feed_container("WBR2_x.csv", csv_data,
                                                     source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertIn("ARN-999999", feed_import.notes)
        self.assertIsNone(MutualFundFolio.objects.get(folio_number="111").arn)

    def test_folio_master_rows_upsert_without_transactions(self):
        csv_data = (
            "FOLIO_NO,INV_NAME,PAN,AMC_CODE\n"
            "222333,Priya Patel,FGHIJ5678K,I\n"
        ).encode()
        feed_import = rta_feed.import_feed_container("WBR9_folio_master.csv", csv_data,
                                                     source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(feed_import.folios_created, 1)
        self.assertEqual(MutualFundTransaction.objects.count(), 0)

    def test_unrecognised_headers_fail_with_headers_in_notes(self):
        feed_import = rta_feed.import_feed_container(
            "weird.csv", b"COL_A,COL_B\n1,2\n", source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_FAILED)
        self.assertIn("COL_A", feed_import.notes)

    def test_relink_after_client_gets_pan(self):
        rta_feed.import_feed_container("WBR2_140726.csv", CAMS_CSV,
                                       source=RTAFeedImport.SOURCE_UPLOAD)
        folio = MutualFundFolio.objects.get(folio_number="555111")
        self.assertIsNone(folio.client)
        Client.objects.create(name="Priya Patel", pan="fghij 5678k")  # messy PAN still matches
        self.assertEqual(rta_feed.relink_folios(), 1)
        folio.refresh_from_db()
        self.assertEqual(folio.client.name, "Priya Patel")

    def test_ambiguous_pan_is_not_auto_linked(self):
        Client.objects.create(name="Rahul Duplicate", pan="ABCDE1234F")
        rta_feed.import_feed_container("WBR2_140726.csv", CAMS_CSV,
                                       source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertIsNone(MutualFundFolio.objects.get(folio_number="1234567/89").client)


class CommandTests(TestCase):
    def test_command_noops_without_imap_config(self):
        out = io.StringIO()
        call_command("import_rta_feeds", stdout=out)
        self.assertIn("No RTA feed emails processed", out.getvalue())
        self.assertEqual(RTAFeedImport.objects.count(), 0)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for role in ("admin", "employee"):
            user = User.objects.create_user(username=f"mf_{role}", password="x")
            Employee.objects.create(user=user, role=role, salary=0, active=True)
        cls.arn = ArnAccount.objects.create(label="Direct (NSE)", arn_code="ARN-777")

    def setUp(self):
        self.admin = TestClient()
        self.admin.force_login(User.objects.get(username="mf_admin"))
        self.employee = TestClient()
        self.employee.force_login(User.objects.get(username="mf_employee"))

    def test_admin_only_access(self):
        for name in ("mf_dashboard", "mf_folios", "mf_transactions"):
            self.assertEqual(self.admin.get(reverse(f"clients:{name}")).status_code, 200)
            self.assertEqual(self.employee.get(reverse(f"clients:{name}")).status_code, 403)

    def test_upload_imports_file(self):
        upload = SimpleUploadedFile("WBR2_140726.csv", CAMS_CSV, content_type="text/csv")
        resp = self.admin.post(reverse("clients:mf_upload"), {"feed_files": upload})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MutualFundTransaction.objects.count(), 3)
        self.assertEqual(RTAFeedImport.objects.get().source, RTAFeedImport.SOURCE_UPLOAD)

    def test_folio_link_and_unlink(self):
        folio = MutualFundFolio.objects.create(folio_number="42", amc_name="HDFC")
        client = Client.objects.create(name="Linkable Person")
        url = reverse("clients:mf_folio_link", args=[folio.id])
        self.assertEqual(self.admin.get(url).status_code, 200)

        self.admin.post(url, {"client_id": str(client.id)})
        folio.refresh_from_db()
        self.assertEqual(folio.client, client)

        self.admin.post(url, {"action": "unlink"})
        folio.refresh_from_db()
        self.assertIsNone(folio.client)

    def test_arn_save_and_delete(self):
        self.admin.post(reverse("clients:mf_arn_save"),
                        {"label": "NJ", "arn_code": "ARN-0155", "sub_broker_code": "SB123"})
        nj = ArnAccount.objects.get(label="NJ")
        self.assertEqual(nj.sub_broker_code, "SB123")

        self.admin.post(reverse("clients:mf_arn_delete", args=[nj.id]))
        self.assertFalse(ArnAccount.objects.filter(label="NJ").exists())

    def test_arn_delete_with_data_deactivates_instead(self):
        MutualFundFolio.objects.create(folio_number="7", amc_name="X", arn=self.arn)
        self.admin.post(reverse("clients:mf_arn_delete", args=[self.arn.id]))
        self.arn.refresh_from_db()
        self.assertFalse(self.arn.is_active)

    def test_client_profile_shows_folios(self):
        client = Client.objects.create(name="Profile Person")
        MutualFundFolio.objects.create(folio_number="F-1", amc_name="HDFC", client=client, arn=self.arn)
        resp = self.admin.get(reverse("clients:client_profile", args=[client.id]))
        self.assertContains(resp, "Mutual Fund Folios")
        self.assertContains(resp, "F-1")
