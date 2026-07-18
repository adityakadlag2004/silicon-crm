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

    def test_same_file_skipped_for_email_but_uploads_always_process(self):
        rta_feed.import_feed_container("a.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        # identical file via email → skipped (protects the hourly cron)
        again = rta_feed.import_feed_container("a.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_EMAIL)
        self.assertEqual(again.status, RTAFeedImport.STATUS_SKIPPED)
        # identical file re-UPLOADED → processed (deliberate; rows dedupe)
        reup = rta_feed.import_feed_container("a.csv", CAMS_CSV, source=RTAFeedImport.SOURCE_UPLOAD)
        self.assertEqual(reup.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(reup.rows_duplicate, 3)
        self.assertEqual(MutualFundTransaction.objects.count(), 3)  # no doubles

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


    def test_tilde_delimited_kfin_master_parses(self):
        data = (
            "Product Code~Fund~Folio~Investor Name~PAN No~Dividend Option\n"
            "1011AID~101~99887766~Suresh Patil~ABCDE1234F~G\n"
        ).encode()
        headers, rows = rta_feed.read_data_file("MFSD211_WBMST1_1.txt", data)
        self.assertIn("Folio", headers)
        self.assertEqual(rows[0]["Folio"], "99887766")


    def test_master_file_never_creates_transactions(self):
        # MFSD211-style investor master: no txn-type column, but unit-balance
        # columns that match the amount/units aliases
        data = (
            "Product Code~Fund~Folio~Investor Name~PAN No~Units~Amount\n"
            "1011AID~101~99887766~Suresh Patil~ABCDE1234F~120.5~50000\n"
        ).encode()
        feed_import = rta_feed.import_feed_container(
            "MFSD211_WBMST2_2.txt", data, source="upload")
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(feed_import.rows_imported, 1)
        self.assertEqual(MutualFundTransaction.objects.count(), 0)
        self.assertEqual(MutualFundFolio.objects.count(), 1)


    def test_mfsd201_dbf_variant_headers_map_as_transactions(self):
        # Real header set from the MFSD201 DBF variant (W0T592.dbf, 2026-07-16)
        headers = ["FMCODE", "TD_FUND", "TD_ACNO", "FUNDDESC", "TD_TRNO", "INVNAME",
                   "TD_TRDT", "TD_UNITS", "TD_AMT", "TD_BROKER", "TRDESC", "TD_TRTYPE",
                   "TD_NAV", "PAN1", "SUBARNCODE"]
        hm = rta_feed._build_header_map(headers)
        self.assertEqual(hm["folio"], "TD_ACNO")
        self.assertEqual(hm["txn_type"], "TRDESC")
        self.assertEqual(hm["broker"], "TD_BROKER")
        self.assertEqual(hm["sub_broker"], "SUBARNCODE")
        self.assertEqual(hm["trade_date"], "TD_TRDT")


class SipRegisterTests(TestCase):
    """SIP register: registration reports route to SipRegistration (never the
    transaction ledger), upserts are idempotent, cease transitions notify
    admins, and the screen is admin-only."""

    CAMS_REG_CSV = (
        "FUNDNAME,FOLIO_NO,PAN,SCHEME_NAME,ARN_CODE,SUB_BROKER_ARN,INVESTOR_NAME,"
        "AMOUNT,TRANSACTION_TYPE,FROM_DATE,TO_DATE,NO_OF_INSTALMENTS,REGISTRATIONDATE\n"
        "ICICI Prudential,45375976,ELPPK1234F,Agressive Hybrid,ARN-777,,Paresh K,"
        "2000,SIP,2026-07-10,2065-09-10,471,2026-06-15\n"
    ).encode()

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.admin_user = User.objects.create_user(username="sip_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="sip_emp", password="x")
        Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        ArnAccount.objects.create(label="Direct", arn_code="ARN-777")

    def _import(self, name="Systematic_Registration_Status_P_11-Jul-2026.csv", data=None):
        return rta_feed.import_feed_container(name, data or self.CAMS_REG_CSV, source="upload")

    def test_registration_file_creates_register_rows_not_transactions(self):
        from clients.models import SipRegistration
        feed_import = self._import()
        self.assertEqual(feed_import.status, RTAFeedImport.STATUS_PROCESSED)
        self.assertEqual(feed_import.rows_imported, 1)
        self.assertEqual(MutualFundTransaction.objects.count(), 0)
        reg = SipRegistration.objects.get()
        self.assertEqual(reg.folio_number, "45375976")
        self.assertEqual(str(reg.amount), "2000.00")
        self.assertEqual(reg.txn_type, "SIP")
        self.assertEqual(str(reg.start_date), "2026-07-10")
        self.assertEqual(reg.installments, 471)
        self.assertEqual(reg.broker_code, "ARN-777")
        self.assertIsNotNone(reg.arn)
        self.assertEqual(reg.status, SipRegistration.STATUS_ACTIVE)

    def test_reimport_is_idempotent(self):
        from clients.models import SipRegistration
        self._import()
        # next day's report repeats the same registration (different file bytes)
        feed_import = self._import(
            name="Systematic_Registration_Status_P_12-Jul-2026.csv",
            data=self.CAMS_REG_CSV + b"\n",
        )
        self.assertEqual(SipRegistration.objects.count(), 1)
        self.assertEqual(feed_import.rows_duplicate, 1)

    def test_cease_status_notifies_admins(self):
        from clients.models import Notification, SipRegistration
        self._import()
        ceased_csv = self.CAMS_REG_CSV.replace(
            b"REGISTRATIONDATE\n", b"REGISTRATIONDATE,STATUS\n"
        ).replace(b",2026-06-15\n", b",2026-06-15,Ceased\n")
        self._import(name="Systematic_Registration_Status_P_13-Jul-2026.csv", data=ceased_csv)
        reg = SipRegistration.objects.get()
        self.assertEqual(reg.status, SipRegistration.STATUS_CEASED)
        self.assertIsNotNone(reg.ceased_on)
        note = Notification.objects.get(recipient=self.admin_user)
        self.assertIn("SIP ceased", note.title)

    def test_mfsd243_routes_by_filename(self):
        from clients.models import SipRegistration
        data = (
            "FOLIO,SCHEME,AMOUNT,FROMDATE,STATUS\n"
            "777999,Axis Small Cap,1500,01/07/2026,Active\n"
        ).encode()
        feed_import = rta_feed.import_feed_container(
            "MFSD243_WSREG1_1.csv", data, source="upload")
        self.assertEqual(SipRegistration.objects.count(), 1)
        self.assertIn("SIP register", feed_import.notes)

    def test_screen_admin_only(self):
        c = TestClient()
        c.force_login(self.admin_user)
        self.assertEqual(c.get(reverse("clients:mf_sips")).status_code, 200)
        c2 = TestClient()
        c2.force_login(self.emp_user)
        self.assertEqual(c2.get(reverse("clients:mf_sips")).status_code, 403)

    def test_pan_links_client(self):
        from clients.models import SipRegistration
        client = Client.objects.create(name="Paresh K", pan="ELPPK1234F")
        self._import()
        reg = SipRegistration.objects.get()
        self.assertEqual(reg.client_id, client.id)


    def test_relink_links_sip_registrations_after_pan_added(self):
        from clients.models import SipRegistration
        self._import()
        reg = SipRegistration.objects.get()
        self.assertIsNone(reg.client_id)
        client = Client.objects.create(name="Paresh K", pan="ELPPK1234F")
        linked = rta_feed.relink_folios()
        reg.refresh_from_db()
        self.assertEqual(reg.client_id, client.id)
        self.assertGreaterEqual(linked, 1)


class FolioMatchTests(TestCase):
    """Match Folios screen: suffix-aware name matching + the two row actions."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="fm_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="fm_emp", password="x")
        Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        # CRM names carry routing tags; RTA reports the legal name
        cls.aditya = Client.objects.create(name="Aditya Kadlag NSE")
        cls.rahul = Client.objects.create(name="Rahul Sharma NJ")
        MutualFundFolio.objects.create(folio_number="111", amc_name="HDFC",
                                       investor_name="ADITYA SUNIL KADLAG", pan="IWSPK1111A")
        MutualFundFolio.objects.create(folio_number="222", amc_name="Axis",
                                       investor_name="ADITYA SUNIL KADLAG", pan="IWSPK1111A")
        MutualFundFolio.objects.create(folio_number="333", amc_name="SBI",
                                       investor_name="RAHUL SHARMA", pan="ABCDE9999Z")

    def _http(self):
        c = TestClient()
        c.force_login(self.admin_user)
        return c

    def test_suggestions_match_despite_suffix_and_middle_name(self):
        suggestions = rta_feed.suggest_folio_matches()
        by_client = {s["client"].id: s for s in suggestions}
        self.assertIn(self.aditya.id, by_client)      # first-two-words + middle name
        self.assertIn(self.rahul.id, by_client)       # exact after NJ suffix strip
        self.assertEqual(len(by_client[self.aditya.id]["folios"]), 2)  # grouped by PAN
        self.assertEqual(by_client[self.rahul.id]["level"], 3)

    def test_adopt_sets_pan_and_links_everything(self):
        folio_ids = list(MutualFundFolio.objects.filter(pan="IWSPK1111A").values_list("id", flat=True))
        resp = self._http().post(reverse("clients:mf_folio_match"), {
            "action": "adopt", "client_id": self.aditya.id, "pan": "IWSPK1111A",
            "folio_ids": folio_ids,
        })
        self.assertEqual(resp.status_code, 302)
        self.aditya.refresh_from_db()
        self.assertEqual(self.aditya.pan, "IWSPK1111A")
        self.assertEqual(MutualFundFolio.objects.filter(client=self.aditya).count(), 2)

    def test_adopt_rejects_invalid_pan_and_conflicting_pan(self):
        self._http().post(reverse("clients:mf_folio_match"), {
            "action": "adopt", "client_id": self.aditya.id, "pan": "NOT-A-PAN",
        })
        self.aditya.refresh_from_db()
        self.assertEqual(self.aditya.pan or "", "")
        self.rahul.pan = "ZZZZZ1234Z"
        self.rahul.save()
        self._http().post(reverse("clients:mf_folio_match"), {
            "action": "adopt", "client_id": self.rahul.id, "pan": "ABCDE9999Z",
        })
        self.rahul.refresh_from_db()
        self.assertEqual(self.rahul.pan, "ZZZZZ1234Z")  # not overwritten

    def test_link_only_links_without_pan_change(self):
        folio = MutualFundFolio.objects.get(folio_number="333")
        self._http().post(reverse("clients:mf_folio_match"), {
            "action": "link", "client_id": self.rahul.id, "folio_ids": [folio.id],
        })
        folio.refresh_from_db()
        self.assertEqual(folio.client, self.rahul)
        self.rahul.refresh_from_db()
        self.assertEqual(self.rahul.pan or "", "")

    def test_screen_admin_only(self):
        c = TestClient()
        c.force_login(self.emp_user)
        self.assertEqual(c.get(reverse("clients:mf_folio_match")).status_code, 403)
        self.assertEqual(self._http().get(reverse("clients:mf_folio_match")).status_code, 200)


class SipLeakTrackingTests(TestCase):
    """TerminateDate + raw status capture, and the register dashboard's
    terminated-vs-expired distinction."""

    MFSD243_CSV = (
        "Folio,Investor Name,RegistrationDate,Start Date,End Date,Amount,"
        "Scheme Name,PAN,SipType,Frequency,TerminateDate,Status,AgentCode\n"
        "9001,Asha Verma,01/02/2026,10/02/2026,10/02/2036,5000,"
        "Axis Small Cap,ASHAV1234K,SIP,Monthly,,Live SIP,ARN-777\n"
        "9002,Vikram Rao,01/01/2026,10/01/2026,10/01/2036,3000,"
        "HDFC Flexi Cap,VIKRR5678L,SIP,Monthly,05/06/2026,Terminated,ARN-777\n"
        "9003,Sita Iyer,01/01/2025,10/01/2025,10/06/2026,2000,"
        "SBI Bluechip,SITAI9012M,SIP,Monthly,,Expired,ARN-777\n"
    ).encode()

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="lk_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)

    def _import(self, name="MFSD243_WSREG9_1.csv", data=None):
        return rta_feed.import_feed_container(name, data or self.MFSD243_CSV, source="upload")

    def test_terminate_date_and_raw_status_captured(self):
        from clients.models import SipRegistration
        self._import()
        live = SipRegistration.objects.get(folio_number="9001")
        self.assertEqual(live.status, SipRegistration.STATUS_ACTIVE)
        self.assertEqual(live.rta_status, "Live SIP")

        terminated = SipRegistration.objects.get(folio_number="9002")
        self.assertEqual(terminated.status, SipRegistration.STATUS_CEASED)
        self.assertEqual(str(terminated.ceased_on), "2026-06-05")  # TerminateDate, not today

        expired = SipRegistration.objects.get(folio_number="9003")
        self.assertEqual(expired.status, SipRegistration.STATUS_CEASED)
        self.assertEqual(expired.rta_status, "Expired")

    def test_reimport_backfills_cease_date(self):
        from clients.models import SipRegistration
        # first file had no terminate date; a later one carries it
        first = self.MFSD243_CSV.replace(b"05/06/2026,Terminated", b",Terminated")
        self._import(data=first)
        reg = SipRegistration.objects.get(folio_number="9002")
        self.assertNotEqual(str(reg.ceased_on), "2026-06-05")
        self._import(name="MFSD243_WSREG9_2.csv")
        reg.refresh_from_db()
        self.assertEqual(str(reg.ceased_on), "2026-06-05")

    def test_dashboard_counts_leaks_not_expiries(self):
        self._import()
        c = TestClient()
        c.force_login(self.admin_user)
        resp = c.get(reverse("clients:mf_sips"))
        self.assertEqual(resp.status_code, 200)
        tiles = resp.context["tiles"]
        self.assertEqual(tiles["active"]["n"], 1)
        self.assertEqual(tiles["expired"]["n"], 1)
        # terminated shows in leak lists; expired doesn't
        stopped_folios = [r.folio_number for r in resp.context["recent_stopped"]]
        self.assertIn("9002", stopped_folios)
        self.assertNotIn("9003", stopped_folios)
        self.assertEqual(len(resp.context["flow"]), 6)


class SipMonthDrilldownTests(TestCase):
    """Month drill-down page + Indian number formatting."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import SipRegistration
        cls.admin_user = User.objects.create_user(username="md_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        SipRegistration.objects.create(
            dedupe_key="k1", folio_number="1", scheme_name="Axis Small Cap",
            amount=5000, registered_on="2026-06-05", start_date="2026-06-10",
            status="active", rta_status="Live SIP")
        SipRegistration.objects.create(
            dedupe_key="k2", folio_number="2", scheme_name="HDFC Flexi Cap",
            amount=3000, registered_on="2026-01-05", start_date="2026-01-10",
            status="ceased", rta_status="Terminated", ceased_on="2026-06-20")
        SipRegistration.objects.create(
            dedupe_key="k3", folio_number="3", scheme_name="SBI Bluechip",
            amount=2000, registered_on="2025-01-05", start_date="2025-01-10",
            status="ceased", rta_status="Expired", end_date="2026-06-15")

    def _http(self):
        c = TestClient()
        c.force_login(self.admin_user)
        return c

    def test_month_page_splits_new_stopped_expired(self):
        resp = self._http().get(reverse("clients:mf_sips_month", args=[2026, 6]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r.folio_number for r in resp.context["new_regs"]], ["1"])
        self.assertEqual([r.folio_number for r in resp.context["stopped_regs"]], ["2"])
        self.assertEqual([r.folio_number for r in resp.context["expired_regs"]], ["3"])
        self.assertEqual(resp.context["net"], 2000)  # 5000 new - 3000 stopped

    def test_invalid_month_404(self):
        self.assertEqual(self._http().get("/clients/mf/sips/month/2026/13/").status_code, 404)

    def test_inr_filter_groups_indian_style(self):
        from clients.templatetags.custom_filters import inr
        self.assertEqual(inr(2063297), "20,63,297")
        self.assertEqual(inr(182063297), "18,20,63,297")
        self.assertEqual(inr(999), "999")
        self.assertEqual(inr(-1234567), "-12,34,567")
        self.assertEqual(inr(None), "0")


class FolioCreateClientTests(TestCase):
    """Create-client-from-folio button + the KYC data-health console."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="cc_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.folio = MutualFundFolio.objects.create(
            folio_number="777001", amc_name="Axis",
            investor_name="MEENA RAVI JOSHI", pan="MEENJ1234R")
        MutualFundFolio.objects.create(
            folio_number="777002", amc_name="HDFC",
            investor_name="MEENA RAVI JOSHI", pan="MEENJ1234R")

    def _http(self):
        c = TestClient()
        c.force_login(self.admin_user)
        return c

    def test_create_client_from_folio_links_all_same_pan(self):
        resp = self._http().post(reverse("clients:mf_folio_create_client", args=[self.folio.id]))
        self.assertEqual(resp.status_code, 302)
        client = Client.objects.get(pan="MEENJ1234R")
        self.assertEqual(client.name, "Meena Ravi Joshi")
        self.assertEqual(MutualFundFolio.objects.filter(client=client).count(), 2)

    def test_existing_pan_links_instead_of_duplicating(self):
        existing = Client.objects.create(name="Meena J NSE", pan="MEENJ1234R")
        self._http().post(reverse("clients:mf_folio_create_client", args=[self.folio.id]))
        self.assertEqual(Client.objects.filter(pan="MEENJ1234R").count(), 1)
        self.folio.refresh_from_db()
        self.assertEqual(self.folio.client, existing)

    def test_kyc_health_console_context(self):
        Client.objects.create(name="Ramesh")           # single-word junk
        resp = self._http().get(reverse("clients:client_kyc_issues"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("single_word", resp.context)
        self.assertIn("no_business", resp.context)
        self.assertEqual(resp.context["unlinked_folios"], 2)
        names = [c.name for c in resp.context["single_word"]]
        self.assertIn("Ramesh", names)

    def test_merge_into_typed_bad_id_is_friendly(self):
        junk = Client.objects.create(name="Ramesh")
        resp = self._http().post(reverse("clients:client_merge"), {
            "keep_id": "99999", "remove_id": junk.id})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Client.objects.filter(id=junk.id).exists())  # nothing merged


class BrokerAttributionFallbackTests(TestCase):
    """Broker-less rows: TD_AGENT fallback + inheritance from the folio's ARN."""

    @classmethod
    def setUpTestData(cls):
        cls.arn = ArnAccount.objects.create(label="Direct", arn_code="ARN-295541")

    def test_agent_column_fallback_when_broker_blank(self):
        data = (
            "FMCODE,TD_ACNO,INVNAME,TRDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_BROKER,TD_AGENT\n"
            "102,881001,Asha Naik,Purchase,T1,01/07/2026,5000,10,,ARN-295541\n"
        ).encode()
        rta_feed.import_feed_container("MFSD201_x.csv", data, source="upload")
        txn = MutualFundTransaction.objects.get()
        self.assertEqual(txn.arn, self.arn)
        self.assertEqual(txn.broker_code, "ARN-295541")

    def test_blank_broker_inherits_folio_arn(self):
        first = (
            "FMCODE,TD_ACNO,INVNAME,TRDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_BROKER\n"
            "102,881002,Asha Naik,Purchase,T2,01/07/2026,5000,10,ARN-295541\n"
        ).encode()
        rta_feed.import_feed_container("MFSD201_a.csv", first, source="upload")
        second = (
            "FMCODE,TD_ACNO,INVNAME,TRDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_BROKER\n"
            "102,881002,Asha Naik,Dividend Reinvest,T3,05/07/2026,200,1,NOT PROVIDED\n"
        ).encode()
        rta_feed.import_feed_container("MFSD201_b.csv", second, source="upload")
        txn = MutualFundTransaction.objects.get(txn_number="T3")
        self.assertEqual(txn.arn, self.arn)  # inherited from the folio


    def test_reimport_heals_missing_attribution(self):
        # first import: broker column empty and no agent fallback available
        first = (
            "FMCODE,TD_ACNO,INVNAME,TRDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_BROKER\n"
            "102,881003,Asha Naik,Purchase,T4,01/07/2026,5000,10,\n"
        ).encode()
        rta_feed.import_feed_container("MFSD201_c.csv", first, source="upload")
        self.assertIsNone(MutualFundTransaction.objects.get(txn_number="T4").arn)
        # same row re-imported, now with the agent column present
        second = (
            "FMCODE,TD_ACNO,INVNAME,TRDESC,TD_TRNO,TD_TRDT,TD_AMT,TD_UNITS,TD_BROKER,TD_AGENT\n"
            "102,881003,Asha Naik,Purchase,T4,01/07/2026,5000,10,,ARN-295541\n"
        ).encode()
        rta_feed.import_feed_container("MFSD201_d.csv", second, source="upload")
        txn = MutualFundTransaction.objects.get(txn_number="T4")
        self.assertEqual(txn.arn, self.arn)


class CobAndProfileSipTests(TestCase):
    """COB opportunity detection + the client-profile live-SIP figure."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone
        cls.admin_user = User.objects.create_user(username="cob_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="cob_emp", password="x")
        Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.arn = ArnAccount.objects.create(label="Direct", arn_code="ARN-295541")
        cls.client_row = Client.objects.create(name="Asha Naik", pan="ASHAN1234K")
        cls.folio = MutualFundFolio.objects.create(
            folio_number="990001", amc_name="LIC MF", investor_name="ASHA NAIK",
            pan="ASHAN1234K", client=cls.client_row, arn=cls.arn)
        today = timezone.localdate()
        # outsider SIP stream: old broker code 63755, still running
        for i, days_ago in enumerate((70, 40, 10)):
            MutualFundTransaction.objects.create(
                dedupe_key=f"cob{i}", folio=cls.folio, txn_type="Systematic Investment",
                amount=2000, trade_date=today - timedelta(days=days_ago),
                broker_code="63755")
        # dead outsider stream in another folio
        cls.folio2 = MutualFundFolio.objects.create(
            folio_number="990002", amc_name="Axis", investor_name="ASHA NAIK",
            pan="ASHAN1234K", client=cls.client_row)
        MutualFundTransaction.objects.create(
            dedupe_key="cobdead", folio=cls.folio2, txn_type="SIN",
            amount=1500, trade_date=today - timedelta(days=200), broker_code="26848")
        # the user's own attributed SIP — must NOT appear in COB
        MutualFundTransaction.objects.create(
            dedupe_key="own1", folio=cls.folio, txn_type="SIN",
            amount=5000, trade_date=today - timedelta(days=5),
            broker_code="ARN-295541", arn=cls.arn)

    def test_cob_groups_live_and_stopped(self):
        groups = rta_feed.cob_opportunities()
        self.assertEqual(len(groups), 2)
        live = [g for g in groups if g["live"]]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["broker_code"], "63755")
        self.assertEqual(live[0]["monthly"], 2000)
        self.assertEqual(live[0]["n"], 3)
        self.assertEqual(live[0]["client"], self.client_row)

    def test_cob_page_admin_only(self):
        c = TestClient()
        c.force_login(self.admin_user)
        resp = c.get(reverse("clients:mf_cob"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["tiles"]["live_n"], 1)
        c2 = TestClient()
        c2.force_login(self.emp_user)
        self.assertEqual(c2.get(reverse("clients:mf_cob")).status_code, 403)

    def test_profile_sip_counts_cams_sin_rows(self):
        summary = rta_feed.mf_summary_for_client(self.client_row)
        # the own-ARN SIN installment (5 days ago) counts toward monthly SIP;
        # the live outsider installment does too (it's the client's money)
        self.assertGreaterEqual(summary["monthly_sip"], 5000)

    def test_profile_sip_prefers_register(self):
        from clients.models import SipRegistration
        SipRegistration.objects.create(
            dedupe_key="reg-pref", folio_number="990001", client=self.client_row,
            amount=12000, status="active", rta_status="Live SIP")
        summary = rta_feed.mf_summary_for_client(self.client_row)
        self.assertEqual(summary["monthly_sip"], 12000)


class SipFieldSyncAndIdentityTests(TestCase):
    """Client SIP columns fed from the register; folio identity conflicts."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import SipRegistration
        cls.admin_user = User.objects.create_user(username="ss_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.client_row = Client.objects.create(name="Deepak Rao NSE", pan="DEEPR1234K")
        cls.reg = SipRegistration.objects.create(
            dedupe_key="ss1", folio_number="550001", client=cls.client_row,
            amount=7000, status="active", rta_status="Live SIP")

    def test_refresh_sets_client_sip_columns_from_register(self):
        rta_feed.refresh_client_sip_fields()
        self.client_row.refresh_from_db()
        self.assertEqual(self.client_row.sip_amount, 7000)
        self.assertTrue(self.client_row.sip_status)

    def test_sale_signal_does_not_override_register_truth(self):
        rta_feed.refresh_client_sip_fields()
        product, _ = Product.objects.get_or_create(name="SIP", defaults={"code": "SIP"})
        emp_user = User.objects.create_user(username="ss_emp2", password="x")
        emp = Employee.objects.create(user=emp_user, role="employee", salary=0, active=True)
        Sale.objects.create(client=self.client_row, employee=emp, product="SIP",
                            product_ref=product, amount=999, status=Sale.STATUS_APPROVED)
        self.client_row.refresh_from_db()
        self.assertEqual(self.client_row.sip_amount, 7000)  # register wins over the 999 sale

    def test_ceased_register_zeroes_sip_columns(self):
        from clients.models import SipRegistration
        self.reg.status = SipRegistration.STATUS_CEASED
        self.reg.save()
        rta_feed.refresh_client_sip_fields()
        self.client_row.refresh_from_db()
        self.assertEqual(self.client_row.sip_amount, 0)
        self.assertFalse(self.client_row.sip_status)

    def test_identity_issue_detection(self):
        from clients.views.kyc import _folio_identity_issues
        # PAN conflict + name mismatch on one client
        MutualFundFolio.objects.create(folio_number="551", amc_name="HDFC",
                                       investor_name="SOMEONE ELSE", pan="XXXXX9999X",
                                       client=self.client_row)
        # a healthy link elsewhere must not appear
        ok = Client.objects.create(name="Asha Naik", pan="ASHAN1234K")
        MutualFundFolio.objects.create(folio_number="552", amc_name="Axis",
                                       investor_name="ASHA NAIK", pan="ASHAN1234K", client=ok)
        issues = _folio_identity_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["client"], self.client_row)
        self.assertTrue(any("PAN" in p for p in issues[0]["problems"]))

    def test_broker_name_captured_and_shown_in_cob(self):
        data = (
            "Folio,Scheme Name,Amount,Start Date,Status,AgentCode,AgentName,RegistrationDate,No Of Installments\n"
            "660001,Quant Small Cap,2500,01/07/2026,Live SIP,63755,SHARMA INVESTMENTS,15/06/2026,60\n"
        ).encode()
        rta_feed.import_feed_container("MFSD243_name.csv", data, source="upload")
        from clients.models import SipRegistration
        reg = SipRegistration.objects.get(folio_number="660001")
        self.assertEqual(reg.broker_name, "SHARMA INVESTMENTS")


    def test_mf_summary_has_lumpsum_12m(self):
        from datetime import timedelta
        from django.utils import timezone
        folio = MutualFundFolio.objects.create(
            folio_number="550002", amc_name="HDFC",
            investor_name="DEEPAK RAO", pan="DEEPR1234K", client=self.client_row)
        MutualFundTransaction.objects.create(
            dedupe_key="lump1", folio=folio, txn_type="Purchase",
            amount=100000, trade_date=timezone.localdate() - timedelta(days=30))
        MutualFundTransaction.objects.create(
            dedupe_key="sip1", folio=folio, txn_type="SIN",
            amount=3000, trade_date=timezone.localdate() - timedelta(days=10))
        summary = rta_feed.mf_summary_for_client(self.client_row)
        self.assertEqual(summary["lumpsum_12m"], 100000)  # SIN row excluded

    def test_profile_portfolio_uses_feed_numbers(self):
        c = TestClient()
        c.force_login(self.admin_user)
        resp = c.get(reverse("clients:client_profile", args=[self.client_row.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "live from RTA")


class BulkKycTests(TestCase):
    """Bulk PAN apply + bulk duplicate merge from the KYC page."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="bk_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)

    def _http(self):
        c = TestClient()
        c.force_login(self.admin_user)
        return c

    def test_row_save_applies_pan_and_links_folios(self):
        a = Client.objects.create(name="Asha Naik NSE")
        MutualFundFolio.objects.create(folio_number="700001", amc_name="Axis",
                                       investor_name="ASHA NAIK", pan="ASHAN1234K")
        resp = self._http().post(
            reverse("clients:client_kyc_update_pan", args=[a.id]), {"pan": "ASHAN1234K"})
        self.assertEqual(resp.status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.pan, "ASHAN1234K")
        # folio auto-linked by the freshly-saved PAN
        self.assertEqual(MutualFundFolio.objects.get(folio_number="700001").client, a)

    def test_row_save_rejects_pan_owned_by_another_client(self):
        Client.objects.create(name="Existing", pan="DUPES1234K")
        target = Client.objects.create(name="New One NSE")
        self._http().post(
            reverse("clients:client_kyc_update_pan", args=[target.id]), {"pan": "DUPES1234K"})
        target.refresh_from_db()
        self.assertFalse(target.pan)  # not applied — belongs to another client

    def test_missing_pan_rows_carry_folio_suggestion(self):
        c = Client.objects.create(name="Meena Joshi NSE")
        MutualFundFolio.objects.create(folio_number="700002", amc_name="HDFC",
                                       investor_name="MEENA JOSHI", pan="MEENJ9999K")
        resp = self._http().get(reverse("clients:client_kyc_issues"))
        row = next(x for x in resp.context["missing"] if x.id == c.id)
        self.assertIsNotNone(row.pan_suggestion)
        self.assertEqual(row.pan_suggestion["pan"], "MEENJ9999K")

    def test_bulk_merge_keeps_selected_and_moves_records(self):
        keep = Client.objects.create(name="Ramesh Kumar", phone="9998887777")
        dupe = Client.objects.create(name="Ramesh Kumar", phone="9998887777")
        emp = Employee.objects.create(
            user=User.objects.create_user(username="bk_emp", password="x"),
            role="employee", salary=0, active=True)
        product, _ = Product.objects.get_or_create(name="SIP", defaults={"code": "SIP"})
        Sale.objects.create(client=dupe, employee=emp, product="SIP",
                            product_ref=product, amount=5000, status=Sale.STATUS_APPROVED)
        # tick the dupe to merge, mark keep as the survivor
        resp = self._http().post(reverse("clients:client_bulk_merge"), {
            "group_count": "1",
            "keep_g0": str(keep.id),
            "merge_g0": [str(dupe.id)],
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Client.objects.filter(id=dupe.id).exists())
        self.assertEqual(Sale.objects.filter(client=keep).count(), 1)

    def test_bulk_merge_skips_group_with_nothing_ticked(self):
        # two DIFFERENT people sharing a phone — keeper picked but nothing
        # ticked to merge → group must be left untouched
        a = Client.objects.create(name="Person A", phone="9111122222")
        b = Client.objects.create(name="Person B", phone="9111122222")
        resp = self._http().post(reverse("clients:client_bulk_merge"), {
            "group_count": "1", "keep_g0": str(a.id),  # no merge_g0 ticked
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Client.objects.filter(id=a.id).exists())
        self.assertTrue(Client.objects.filter(id=b.id).exists())  # both survive

    def test_bulk_merge_page_admin_only(self):
        c = TestClient()
        emp_user = User.objects.create_user(username="bk_emp2", password="x")
        Employee.objects.create(user=emp_user, role="employee", salary=0, active=True)
        c.force_login(emp_user)
        self.assertEqual(c.post(reverse("clients:client_bulk_merge"), {"group_count": "0"}).status_code, 403)


class MsoffcryptoPatchTests(TestCase):
    """The guarded monkeypatch for msoffcrypto's legacy-XLS decrypt bugs
    applies cleanly and is idempotent (CAMS Systematic-Registration files)."""

    def test_patch_applies_and_is_idempotent(self):
        rta_feed._patch_msoffcrypto_biff()
        from msoffcrypto.format import xls97
        import olefile
        self.assertTrue(getattr(xls97._BIFFStream.iter_record, "_ki_patched", False))
        self.assertTrue(getattr(olefile.OleFileIO.write_stream, "_ki_patched", False))
        # second call must not re-wrap or raise
        rta_feed._patch_msoffcrypto_biff()
        self.assertTrue(getattr(xls97._BIFFStream.iter_record, "_ki_patched", False))
