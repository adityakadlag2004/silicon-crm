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
    Product,
    RTAFeedImport,
    Sale,
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

    def test_password_candidates_include_case_variants(self):
        ArnAccount.objects.create(label="Direct (NSE)", arn_code="ARN-152880")
        candidates = rta_feed._password_candidates()
        for expected in ("ARN-152880", "ARN152880", "152880", "arn-152880", "arn152880"):
            self.assertIn(expected, candidates)

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

    def test_unrecognised_headers_skip_with_headers_in_notes(self):
        feed_import = rta_feed.import_feed_container(
            "weird.csv", b"COL_A,COL_B\n1,2\n", source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_SKIPPED)
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


class MailboxFetchTests(TestCase):
    """fetch_from_mailbox against a mocked IMAP server: only RTA senders are
    processed and marked read; other unread mail is left untouched."""

    def _email_bytes(self, from_addr, attach_name=None, attach_bytes=None):
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = from_addr
        msg["Subject"] = "test"
        msg.set_content("body")
        if attach_name:
            msg.add_attachment(attach_bytes, maintype="text", subtype="csv",
                               filename=attach_name)
        return bytes(msg)

    def test_fetch_processes_rta_mail_only_and_peeks(self):
        from unittest.mock import MagicMock, patch

        cams_msg = self._email_bytes("CAMS <donotreply@camsonline.com>",
                                     "WBR2_160726.csv", CAMS_CSV)
        personal_msg = self._email_bytes("Friend <friend@example.com>")

        imap = MagicMock()
        imap.search.return_value = ("OK", [b"1 2"])
        imap.fetch.side_effect = lambda num, spec: ("OK", [(num, cams_msg if num == b"1" else personal_msg)])

        env = {"RTA_FEED_IMAP_HOST": "imap.test", "RTA_FEED_IMAP_USER": "u",
               "RTA_FEED_IMAP_PASSWORD": "p"}
        with patch.dict("os.environ", env), \
             patch("clients.services.rta_feed.imaplib.IMAP4_SSL", return_value=imap):
            imports = rta_feed.fetch_from_mailbox()

        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].rta, RTA_CAMS)
        self.assertEqual(imports[0].source, RTAFeedImport.SOURCE_EMAIL)
        self.assertEqual(MutualFundTransaction.objects.count(), 3)
        # Search must be scoped server-side (per sender, recent window).
        first_search = imap.search.call_args_list[0].args[1]
        self.assertIn("FROM", first_search)
        self.assertIn("SINCE", first_search)
        # Scanning must PEEK (no implicit read-marking)…
        for call in imap.fetch.call_args_list:
            self.assertIn("PEEK", call.args[1])
        # …and only the CAMS message gets marked Seen.
        imap.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")

    def test_multiple_mailboxes_are_all_fetched(self):
        from unittest.mock import MagicMock, patch

        imap = MagicMock()
        imap.search.return_value = ("OK", [b""])

        env = {"RTA_FEED_IMAP_HOST": "imap.test", "RTA_FEED_IMAP_USER": "primary@x",
               "RTA_FEED_IMAP_PASSWORD": "p",
               "RTA_FEED_IMAP_HOST_2": "imap.other", "RTA_FEED_IMAP_USER_2": "nj@y",
               "RTA_FEED_IMAP_PASSWORD_2": "q"}
        with patch.dict("os.environ", env), \
             patch("clients.services.rta_feed.imaplib.IMAP4_SSL", return_value=imap) as ssl:
            rta_feed.fetch_from_mailbox()

        hosts = [call.args[0] for call in ssl.call_args_list]
        self.assertEqual(hosts, ["imap.test", "imap.other"])
        logins = [call.args[0] for call in imap.login.call_args_list]
        self.assertEqual(logins, ["primary@x", "nj@y"])

    def test_broken_mailbox_does_not_block_the_next(self):
        from unittest.mock import MagicMock, patch

        good = MagicMock()
        good.search.return_value = ("OK", [b""])

        def _ssl(host, port):
            if host == "imap.broken":
                raise OSError("connection refused")
            return good

        env = {"RTA_FEED_IMAP_HOST": "imap.broken", "RTA_FEED_IMAP_USER": "a@x",
               "RTA_FEED_IMAP_PASSWORD": "p",
               "RTA_FEED_IMAP_HOST_2": "imap.ok", "RTA_FEED_IMAP_USER_2": "b@y",
               "RTA_FEED_IMAP_PASSWORD_2": "q"}
        with patch.dict("os.environ", env), \
             patch("clients.services.rta_feed.imaplib.IMAP4_SSL", side_effect=_ssl):
            imports = rta_feed.fetch_from_mailbox()

        self.assertEqual(imports, [])
        good.login.assert_called_once_with("b@y", "q")

    def test_xlsx_with_title_rows_imports(self):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Some Report Title"])          # decorative rows before the header
        ws.append([])
        ws.append(["FOLIO_NO", "INV_NAME", "PAN", "TRXNTYPE", "TRXNNO", "TRADDATE", "AMOUNT", "BROKCODE"])
        ws.append(["555777", "Excel Person", "KLMNO9876P", "Purchase", "X9", "01-Jul-2026", 2500, "ARN-777"])
        buf = io.BytesIO()
        wb.save(buf)

        feed_import = rta_feed.import_feed_container(
            "nj_report.xlsx", buf.getvalue(), source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        folio = MutualFundFolio.objects.get(folio_number="555777")
        self.assertEqual(folio.pan, "KLMNO9876P")
        self.assertEqual(folio.transactions.get().amount, 2500)

    def test_corrupt_excel_fails(self):
        feed_import = rta_feed.import_feed_container(
            "junk.xlsx", b"not-really-excel", source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_FAILED)


class SaleCrossCheckTests(TestCase):
    """rta_evidence_for_sale: pending MF sales verified against feed data."""

    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="crosscheck_emp", password="x")
        cls.emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        cls.sip_product = Product.objects.create(
            name="Mutual Fund SIP", code="MF_SIP", rta_match=Product.RTA_MATCH_SIP)
        cls.lump_product = Product.objects.create(
            name="MF Lumpsum", code="MF_LUMP", rta_match=Product.RTA_MATCH_LUMPSUM)
        cls.other_product = Product.objects.create(name="Health Plan", code="HP")
        cls.client_obj = Client.objects.create(name="Rahul Sharma", pan="ABCDE1234F")
        cls.folio = MutualFundFolio.objects.create(
            folio_number="1234567/89", amc_name="H", pan="ABCDE1234F", client=cls.client_obj)

    def _txn(self, **kw):
        from datetime import date as d
        defaults = dict(folio=self.folio, txn_type="Systematic Investment (SIP)",
                        trade_date=d(2026, 7, 10), amount=5000,
                        scheme_name="HDFC Flexi Cap", dedupe_key=str(MutualFundTransaction.objects.count()))
        defaults.update(kw)
        return MutualFundTransaction.objects.create(**defaults)

    def _sale(self, product, amount, sale_date=None):
        from datetime import date as d
        return Sale.objects.create(
            client=self.client_obj, employee=self.emp, product=product.name,
            product_ref=product, amount=amount, date=sale_date or d(2026, 7, 8))

    def test_unlinked_product_returns_none(self):
        sale = self._sale(self.other_product, 5000)
        self.assertIsNone(rta_feed.rta_evidence_for_sale(sale))

    def test_sip_sale_matches_sip_txn(self):
        self._txn()
        evidence = rta_feed.rta_evidence_for_sale(self._sale(self.sip_product, 5000))
        self.assertEqual(evidence["status"], "matched")
        self.assertEqual(evidence["txns"][0].amount, 5000)

    def test_sip_sale_does_not_match_lumpsum_txn(self):
        self._txn(txn_type="Purchase")
        evidence = rta_feed.rta_evidence_for_sale(self._sale(self.sip_product, 5000))
        self.assertEqual(evidence["status"], "unmatched")
        self.assertEqual(len(evidence["nearby"]), 1)  # still shown as context

    def test_lumpsum_sale_matches_purchase(self):
        self._txn(txn_type="Purchase", amount=100000)
        evidence = rta_feed.rta_evidence_for_sale(self._sale(self.lump_product, 100000))
        self.assertEqual(evidence["status"], "matched")

    def test_amount_outside_tolerance_is_unmatched(self):
        self._txn(amount=9000)
        evidence = rta_feed.rta_evidence_for_sale(self._sale(self.sip_product, 5000))
        self.assertEqual(evidence["status"], "unmatched")

    def test_txn_outside_window_is_ignored(self):
        from datetime import date as d
        self._txn(trade_date=d(2026, 3, 1))
        evidence = rta_feed.rta_evidence_for_sale(self._sale(self.sip_product, 5000))
        self.assertEqual(evidence["status"], "unmatched")
        self.assertEqual(evidence["nearby"], [])

    def test_client_without_pan_or_folio(self):
        stranger = Client.objects.create(name="No Pan Person")
        sale = Sale.objects.create(client=stranger, employee=self.emp,
                                   product="SIP", product_ref=self.sip_product, amount=5000)
        self.assertEqual(rta_feed.rta_evidence_for_sale(sale)["status"], "no_pan")

    def test_approve_screen_shows_evidence_block(self):
        admin_user = User.objects.create_user(username="crosscheck_admin", password="x")
        Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        self._txn()
        self._sale(self.sip_product, 5000)
        web = TestClient()
        web.force_login(admin_user)
        resp = web.get(reverse("clients:approve_sales"))
        self.assertContains(resp, "Verified in RTA feed")
        self.assertContains(resp, "HDFC Flexi Cap")

    def test_product_page_saves_rta_match(self):
        admin_user = User.objects.create_user(username="crosscheck_admin2", password="x")
        Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        web = TestClient()
        web.force_login(admin_user)
        web.post(reverse("clients:product_management"), {
            "action": "update", "product_id": self.other_product.id,
            "name": "Health Plan", "code": "HP", "rta_match": "any",
        })
        self.other_product.refresh_from_db()
        self.assertEqual(self.other_product.rta_match, Product.RTA_MATCH_ANY)


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


class KfinLinkAndFormatTests(TestCase):
    """Fixes discovered from the first real KFintech mail (2026-07-16):
    xlsx content named .xls, link-delivered subscription feeds, and AMC
    rejection notices that must never import as transactions."""

    def _tracker(self, real_url):
        import urllib.parse
        shifted = "".join(chr(ord(c) + 1) for c in real_url)
        return ("https://scdelivery.kfintech.com/c/?u="
                + urllib.parse.quote(shifted, safe="") + "&p=track&e=s1")

    def test_xlsx_content_named_xls_is_parsed(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["FMCODE", "TD_ACNO", "INVNAME", "TRNDESC", "TD_AMT"])
        ws.append(["102", "777888", "Rahul Sharma", "Systematic", "3000.00"])
        buf = io.BytesIO()
        wb.save(buf)
        headers, rows = rta_feed.read_data_file("ARN-295541 16072026.xls", buf.getvalue())
        self.assertIn("TD_ACNO", headers)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["TD_ACNO"]), "777888")

    def test_rejection_rows_are_skipped_with_note(self):
        csv_data = (
            "FMCODE,TD_ACNO,INVNAME,TRNDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,BRCODE\n"
            "102,111222,Meena Joshi,STP OUT Rejection,R1,14/07/2026,0,0,ARN-777\n"
            "102,333444,Meena Joshi,Purchase Rejection,R2,14/07/2026,0,0,ARN-777\n"
        ).encode()
        feed_import = rta_feed.import_feed_container(
            "ARN-295541 16072026.csv", csv_data, source="upload")
        self.assertEqual(feed_import.rows_imported, 0)
        self.assertEqual(feed_import.rows_skipped, 2)
        self.assertEqual(MutualFundTransaction.objects.count(), 0)
        self.assertEqual(MutualFundFolio.objects.count(), 0)
        self.assertIn("rejection", feed_import.notes.lower())

    def test_decode_kfin_tracking_link(self):
        real = "https://mfs.kfintech.com/mfs/Distributor/Requests/Req_allrptslink.aspx?qrytype=Uz=="
        self.assertEqual(rta_feed._decode_kfin_tracking_link(self._tracker(real)), real)
        self.assertIsNone(rta_feed._decode_kfin_tracking_link(
            "https://scdelivery.kfintech.com/c/?u=undefined&p=x"))
        self.assertIsNone(rta_feed._decode_kfin_tracking_link("https://example.com/?u=abc"))

    def test_kfin_report_links_ignores_funcodes_and_marketing(self):
        from email import message_from_bytes
        from email.message import EmailMessage
        report = "https://mfs.kfintech.com/mfs/Distributor/Requests/Req_allrptslink.aspx?qrytype=Uz=="
        funcodes = "https://mfs.kfintech.com/mfs/distributor/downloads/FUNCODES-PRODCODE.xls"
        html = (f'<a href="{self._tracker(report)}">Click Here</a>'
                f'<a href="{self._tracker(funcodes)}">codes</a>'
                f'<a href="https://marketing.kfintech.com/quiz">quiz</a>')
        msg = EmailMessage()
        msg["From"] = "distributorcare@kfintech.com"
        msg["Subject"] = "Subscribed Transaction Feeds Report"
        msg.set_content("plain")
        msg.add_alternative(html, subtype="html")
        links = rta_feed._kfin_report_links(message_from_bytes(bytes(msg)))
        self.assertEqual(links, [report])

    def test_import_kfin_link_expired_records_failure(self):
        from unittest.mock import MagicMock, patch
        response = MagicMock()
        response.read.return_value = b"Invalid Request"
        response.headers = {"Content-Disposition": ""}
        response.headers = MagicMock()
        response.headers.get.return_value = ""
        cm = MagicMock()
        cm.__enter__.return_value = response
        with patch.object(rta_feed.urllib.request, "urlopen", return_value=cm):
            feed_import = rta_feed._import_kfin_link("https://mfs.kfintech.com/x")
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_FAILED)
        self.assertIn("expired link", feed_import.notes)

    def test_import_kfin_link_zip_payload_imports(self):
        import zipfile
        from unittest.mock import MagicMock, patch
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("MFSD307_WBTRN1_1.csv", KFIN_CSV)
        response = MagicMock()
        response.read.return_value = buf.getvalue()
        response.headers = MagicMock()
        response.headers.get.return_value = ""
        cm = MagicMock()
        cm.__enter__.return_value = response
        with patch.object(rta_feed.urllib.request, "urlopen", return_value=cm):
            feed_import = rta_feed._import_kfin_link("https://mfs.kfintech.com/x")
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(feed_import.rows_imported, 1)
        self.assertEqual(feed_import.rta, RTA_KFIN)
