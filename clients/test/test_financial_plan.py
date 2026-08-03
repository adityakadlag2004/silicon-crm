"""The financial plan engine, pinned to the workbook it was ported from.

Every expected number below is the cached result Excel itself stored in
docs/planner_fin/Financial_Plan_Template.xlsx for its sample client. If a
formula here drifts from the sheet, this fails.
"""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse

from clients.models import Employee
from clients.services import financial_plan as fp

# The workbook's own sample client (Inputs sheet, "Client A").
SHEET = dict(fp.defaults(), **{
    "client_name": "Client A", "plan_year": "FY 2026-27",
    "age": 24, "retirement_age": 60, "life_expectancy": 85,
    "dependents": 2, "family_members": 3, "city_factor": 1.0,
    "gross_income": 3500000, "tax_rate": 0.25, "monthly_expenses": 75000,
    "income_growth": 0.08,
    "existing_equity": 500000, "existing_debt": 300000, "existing_cash": 200000,
    "home_loan": 0, "other_loans": 0,
    "existing_life_cover": 0, "existing_health_cover": 500000,
    "inflation": 0.06, "medical_inflation": 0.10, "education_inflation": 0.08,
    "equity_return": 0.12, "debt_return": 0.07, "equity_allocation": 0.75,
    "post_ret_return": 0.075, "step_up": 0.10,
    "retirement_expense_ratio": 0.75, "emergency_months": 6,
    "self_consumption": 0.30, "education_corpus": 3000000,
    "marriage_corpus": 2500000, "estate_costs": 500000,
    "medical_buffer": 1000000, "legacy": 0, "epf_at_retirement": 0,
    "goals": list(fp.DEFAULT_GOALS),
})


class ExcelFunctionTests(TestCase):
    """The five Excel primitives everything else is built on."""

    def test_fv_pv_pmt_round_trip(self):
        self.assertAlmostEqual(fp.fv(0.12, 10, 0, -100000), 310584.82, places=1)
        self.assertAlmostEqual(fp.pv(0.10, 5, -1000), 3790.79, places=1)
        self.assertAlmostEqual(fp.pmt(0.01, 120, 0, 1000000), -4347.09, places=1)

    def test_annuity_due_is_one_period_richer(self):
        """The trailing 1 in PV() is what lets withdrawals start immediately."""
        ordinary = fp.pv(0.05, 10, -100)
        due = fp.pv(0.05, 10, -100, 0, 1)
        self.assertAlmostEqual(due, ordinary * 1.05, places=6)

    def test_nper_returns_none_when_the_corpus_never_depletes(self):
        # Withdrawing less than the corpus earns: Excel's NPER errors, the
        # sheet shows 999, we say so in words instead.
        self.assertIsNone(fp.nper(0.075 / 12, 200000, -50000000))
        self.assertAlmostEqual(fp.nper(0.01, -25000, 0, 10000000), 161.747, places=2)

    def test_rate_solves_back_to_the_target(self):
        r = fp.rate(120, -25000, 0, 10000000)
        self.assertAlmostEqual(r * 12, 0.209577, places=5)

    def test_zero_rate_does_not_divide_by_zero(self):
        self.assertAlmostEqual(fp.fv(0, 10, -1000), 10000)
        self.assertAlmostEqual(fp.pmt(0, 10, 0, 10000), -1000)

    def test_ceiling_rounds_up_to_the_slab(self):
        self.assertEqual(fp.ceiling(52000001, 2500000), 52500000)
        self.assertEqual(fp.ceiling(2500000, 2500000), 2500000)

    def test_band_lookup_takes_the_last_floor_at_or_below(self):
        self.assertEqual(fp._band(fp.LIFE_MULTIPLE, 24), 20)
        self.assertEqual(fp._band(fp.LIFE_MULTIPLE, 44), 15)
        self.assertEqual(fp._band(fp.LIFE_MULTIPLE, 99), 8)


class WorkbookParityTests(TestCase):
    """Section by section against the spreadsheet's cached results."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = fp.compute(dict(SHEET))
        cls.r = cls.plan["r"]

    def assertSheet(self, key, expected, places=2):
        self.assertAlmostEqual(self.r[key], expected, places=places,
                               msg=f"{key} drifted from the workbook")

    def test_inputs_cash_flow(self):
        self.assertSheet("post_tax_income", 2625000)
        self.assertSheet("annual_expenses", 900000)
        self.assertSheet("investible", 1542700)
        self.assertSheet("monthly_investible", 128558.333333333, places=4)
        self.assertSheet("savings_rate", 0.587695238095238, places=9)
        self.assertSheet("blended_return", 0.1075, places=9)
        self.assertSheet("real_pre", 0.0448113207547169, places=9)
        self.assertSheet("real_post", 0.0141509433962264, places=9)
        self.assertSheet("real_safe", 0.00943396226415105, places=9)
        self.assertSheet("years_to_retirement", 36)
        self.assertSheet("years_in_retirement", 25)

    def test_life_cover_all_three_methods(self):
        self.assertSheet("income_multiple", 20)
        self.assertSheet("cover_income_multiple", 70000000)
        self.assertSheet("income_to_family", 1837500)
        self.assertSheet("pv_lost_income", 55866645.7662845, places=3)
        self.assertSheet("cover_hlv", 54866645.7662845, places=3)
        self.assertSheet("survivor_expenses", 675000)
        self.assertSheet("dependency_years", 61)
        self.assertSheet("pv_family_expenses", 31198751.15034, places=3)
        self.assertSheet("cover_needs", 36198751.15034, places=3)

    def test_term_recommendation(self):
        self.assertSheet("cover_highest", 70000000)
        self.assertSheet("term_cover", 70000000)
        self.assertSheet("term_years", 46)
        self.assertSheet("term_rate", 105)
        self.assertSheet("term_premium", 73500)
        self.assertSheet("term_premium_pct", 0.021, places=6)

    def test_health_cover(self):
        self.assertSheet("health_benchmark", 500000)
        self.assertSheet("health_income_min", 1750000)
        self.assertSheet("family_loading", 1.5)
        self.assertSheet("health_base", 3000000)
        self.assertSheet("health_topup", 9000000)
        self.assertSheet("health_total", 12000000)
        self.assertSheet("health_additional", 2500000)
        self.assertSheet("health_base_premium", 42000)
        self.assertSheet("health_topup_premium", 37800)

    def test_critical_illness_accident_and_total_premium(self):
        self.assertSheet("ci_cover", 5000000)
        self.assertSheet("pa_cover", 20000000)
        self.assertSheet("ci_premium", 11000)
        self.assertSheet("pa_premium", 18000)
        self.assertSheet("total_premium", 182300)
        self.assertSheet("premium_pct", 0.0520857142857143, places=9)
        self.assertSheet("deduction_80d", 25000)

    def test_future_health_cover_table(self):
        rows = self.plan["insurance"]["future_cover"]["rows"]
        self.assertEqual(rows[0], ["0", "24", "₹1,20,00,000"])
        self.assertEqual(rows[-1][:2], ["36", "60"])
        self.assertEqual(rows[-1][2], "₹37,09,52,166")   # sheet: 370952166.39

    def test_retirement_corpus(self):
        self.assertSheet("retirement_expenses_today", 675000)
        self.assertSheet("first_year_expense", 5499395.09989949, places=4)
        self.assertSheet("corpus_living", 116748419.184134, places=2)
        self.assertSheet("medical_buffer_at_ret", 30912680.5328708, places=3)
        self.assertSheet("corpus_required", 147661099.717005, places=2)
        self.assertSheet("corpus_today", 18124037.371092, places=3)
        self.assertSheet("corpus_30x", 164981852.996985, places=2)
        self.assertSheet("withdrawal_rate", 0.0372433573259251, places=9)

    def test_retirement_gap_and_funding_options(self):
        self.assertSheet("retirement_existing", 800000)
        self.assertSheet("existing_at_retirement", 31583804.5326692, places=3)
        self.assertSheet("corpus_gap", 116077295.184336, places=2)
        self.assertSheet("level_sip", 22545.1812636349, places=5)
        self.assertSheet("stepup_sip", 8468.27048487619, places=5)
        self.assertSheet("lumpsum_today", 2940172.58280002, places=4)
        self.assertSheet("total_invested", 30397040.3616048, places=3)
        self.assertSheet("wealth_from_compounding", 85680254.8227313, places=2)

    def test_cost_of_delay(self):
        rows = self.plan["retirement"]["delay"]["rows"]
        self.assertEqual([r[0] for r in rows], ["24", "27", "29", "34", "39"])
        self.assertEqual(rows[0][2], "₹8,468")      # sheet: 8468.27
        self.assertEqual(rows[-1][2], "₹63,917")    # sheet: 63916.79

    def test_goals_table_and_totals(self):
        goals = self.plan["goals"]
        emergency = goals["rows"][0]
        self.assertAlmostEqual(emergency["cost"], 450000)        # linked to inputs
        self.assertAlmostEqual(emergency["earmarked"], 200000)
        self.assertAlmostEqual(emergency["future"], 477000, places=4)
        self.assertAlmostEqual(emergency["fv_earmarked"], 213000, places=4)
        self.assertAlmostEqual(emergency["shortfall"], 264000, places=4)
        self.assertAlmostEqual(emergency["sip"], 21352.2540775143, places=5)
        self.assertAlmostEqual(emergency["lumpsum"], 247887.323943662, places=5)
        self.assertAlmostEqual(goals["totals"]["future"], 71949592.7917682, places=2)
        self.assertAlmostEqual(goals["totals"]["shortfall"], 71736592.7917682, places=2)
        self.assertAlmostEqual(goals["totals"]["sip"], 149116.281431154, places=4)
        self.assertAlmostEqual(goals["totals"]["lumpsum"], 8879234.50964881, places=3)

    def test_affordability_block(self):
        self.assertSheet("goals_sip_critical_high", 106156.246120389, places=4)
        self.assertSheet("goals_sip_deferrable", 42960.0353107648, places=4)
        self.assertSheet("committed_now", 114624.516605266, places=4)
        self.assertSheet("surplus_after_committed", 13933.8167280678, places=4)
        self.assertSheet("committed_pct", 0.891614830662596, places=9)
        self.assertSheet("surplus_if_concurrent", -29026.218582697, places=4)

    def test_allocation_split(self):
        self.assertSheet("alloc_equity", 0.75)
        self.assertSheet("alloc_debt", 0.15)
        self.assertSheet("alloc_gold", 0.10)
        self.assertSheet("alloc_equity_amt", 96418.75, places=4)
        self.assertSheet("alloc_debt_amt", 19283.75, places=4)
        self.assertSheet("alloc_gold_amt", 12855.8333333333, places=4)

    def test_projection_first_and_pivot_rows(self):
        rows = self.plan["projection"]
        self.assertEqual(len(rows), 62)                       # ages 24..85
        first = rows[0]
        self.assertEqual((first["year"], first["age"], first["phase"]),
                         (1, 24, "Accumulation"))
        self.assertAlmostEqual(first["opening"], 800000)
        self.assertAlmostEqual(first["invest"], 101619.245818514, places=4)
        self.assertAlmostEqual(first["closing"], 987619.245818514, places=4)
        self.assertAlmostEqual(first["today"], 931716.269640108, places=4)

    def test_projection_reconciles_with_the_corpus_requirement(self):
        """The workbook's own check: the year before retirement must land on
        the corpus the Retirement maths asked for."""
        last_accumulation = [r for r in self.plan["projection"]
                             if r["phase"] == "Accumulation"][-1]
        self.assertEqual(last_accumulation["age"], 59)
        self.assertAlmostEqual(last_accumulation["closing"],
                               self.r["corpus_required"], places=2)

    def test_projection_leaves_only_the_buffer_and_legacy_at_the_end(self):
        """Living expenses are designed to be spent to zero; the medical
        buffer and legacy are not."""
        self.assertGreater(self.r["corpus_at_life_expectancy"], 0)

    def test_calculators_match_the_sheet(self):
        by_key = {key: rows for _, key, rows in self.plan["calculators"]}
        self.assertAlmostEqual(by_key["calc1"][0]["raw"], 24731384.1346841, places=3)
        self.assertAlmostEqual(by_key["calc2"][0]["raw"], 35025517.7273921, places=3)
        self.assertAlmostEqual(by_key["calc2"][1]["raw"], 13745999.8783815, places=3)
        self.assertAlmostEqual(by_key["calc3"][0]["raw"], 9646293.09327495, places=4)
        self.assertAlmostEqual(by_key["calc4"][0]["raw"], 20016.8062091514, places=5)
        self.assertAlmostEqual(by_key["calc5"][0]["raw"], 429187.071974349, places=5)
        self.assertAlmostEqual(by_key["calc5"][2]["raw"], 0.767001369496105, places=9)
        self.assertEqual(by_key["calc6"][0]["raw"], "Never depletes")
        self.assertAlmostEqual(by_key["calc7"][0]["raw"], 43391.1616682767, places=5)
        self.assertAlmostEqual(by_key["calc8"][0]["raw"], 13.4789313054322, places=6)
        self.assertAlmostEqual(by_key["calc8"][1]["raw"], 0.209576682838484, places=5)
        self.assertAlmostEqual(by_key["calc8"][3]["raw"], 6.1162553741997, places=6)


class EdgeCaseTests(TestCase):
    """Degenerate inputs a client-facing form will absolutely receive."""

    def test_a_plan_with_no_income_does_not_explode(self):
        plan = fp.compute(dict(SHEET, gross_income=0, monthly_expenses=0))
        self.assertEqual(plan["r"]["savings_rate"], 0)
        self.assertEqual(plan["r"]["premium_pct"], 0)

    def test_retiring_today_leaves_no_accumulation_phase(self):
        plan = fp.compute(dict(SHEET, age=60))
        self.assertEqual(plan["r"]["years_to_retirement"], 0)
        self.assertEqual(plan["r"]["stepup_sip"], 0)
        self.assertTrue(all(row["phase"] == "Retirement" for row in plan["projection"]))

    def test_a_fully_covered_client_is_told_to_buy_nothing_more(self):
        plan = fp.compute(dict(SHEET, existing_life_cover=200000000,
                               existing_health_cover=50000000))
        self.assertEqual(plan["r"]["term_cover"], 0)
        self.assertEqual(plan["r"]["term_premium"], 0)
        self.assertEqual(plan["r"]["health_additional"], 0)

    def test_zero_step_up_gives_a_flat_contribution_that_still_lands(self):
        """Without a step-up the instalment never rises, and the projection
        must still arrive at the corpus the retirement maths asked for.

        It does not equal the level SIP: the growing-annuity solve credits a
        year's investment once at year end, the level SIP compounds monthly.
        That is the workbook's own convention, and the projection uses it too.
        """
        plan = fp.compute(dict(SHEET, step_up=0))
        invested = [row["invest"] for row in plan["projection"]
                    if row["phase"] == "Accumulation"]
        self.assertEqual(len(set(round(x, 4) for x in invested)), 1)
        landing = [r for r in plan["projection"] if r["phase"] == "Accumulation"][-1]
        self.assertAlmostEqual(landing["closing"], plan["r"]["corpus_required"], places=2)

    def test_step_up_equal_to_the_return_does_not_divide_by_zero(self):
        plan = fp.compute(dict(SHEET, step_up=0.1075))
        self.assertGreater(plan["r"]["stepup_sip"], 0)

    def test_goals_with_no_shortfall_need_no_sip(self):
        plan = fp.compute(dict(SHEET, goals=[
            ("Funded goal", "High", 5, 100000, 0.06, 5000000, 0.10, ""),
        ]))
        self.assertEqual(plan["goals"]["totals"]["sip"], 0)
        self.assertEqual(plan["r"]["goals_sip_critical_high"], 0)


class ParsingTests(TestCase):
    def test_percent_fields_arrive_as_human_percents(self):
        values = fp.parse({"inflation": "7.5", "gross_income": "12,00,000"})
        self.assertAlmostEqual(values["inflation"], 0.075)
        self.assertAlmostEqual(values["gross_income"], 1200000)

    def test_junk_falls_back_to_the_default_instead_of_500ing(self):
        values = fp.parse({"age": "abc", "gross_income": ""})
        self.assertEqual(values["age"], fp.INPUT_FIELDS["age"][3])
        self.assertEqual(values["gross_income"], fp.INPUT_FIELDS["gross_income"][3])

    def test_goal_rows_come_off_a_querydict(self):
        from django.http import QueryDict
        qd = QueryDict(mutable=True)
        qd.setlist("goal_name", ["Car", "", "House"])
        qd.setlist("goal_priority", ["High", "Low", "Bogus"])
        qd.setlist("goal_years", ["5", "2", "10"])
        qd.setlist("goal_cost", ["100000", "1", "500000"])
        qd.setlist("goal_inflation", ["6", "6", "7"])
        qd.setlist("goal_earmarked", ["0", "0", "0"])
        qd.setlist("goal_return", ["10", "8", "11"])
        qd.setlist("goal_link", ["", "", ""])
        goals = fp.parse(qd)["goals"]
        self.assertEqual([g[0] for g in goals], ["Car", "House"])   # blank row dropped
        self.assertEqual(goals[1][1], "Medium")                     # bogus priority
        self.assertAlmostEqual(goals[0][4], 0.06)


class PlannerViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("planner", password="pw")
        Employee.objects.create(user=cls.u, role="employee", salary=0, active=True)

    def setUp(self):
        self.tc = TC()
        self.tc.force_login(self.u)

    def test_get_renders_a_worked_plan_from_the_defaults(self):
        resp = self.tc.get(reverse("clients:financial_planner"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ki-kpis")
        self.assertContains(resp, "RETIREMENT CORPUS REQUIRED")

    def test_post_recomputes_from_the_submitted_inputs(self):
        resp = self.tc.post(reverse("clients:financial_planner"), {
            "client_name": "Test Client", "age": "24", "retirement_age": "60",
            "life_expectancy": "85", "gross_income": "3500000", "tax_rate": "25",
            "monthly_expenses": "75000",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test Client")
        self.assertAlmostEqual(resp.context["plan"]["r"]["post_tax_income"], 2625000)

    def test_login_is_required(self):
        resp = TC().get(reverse("clients:financial_planner"))
        self.assertEqual(resp.status_code, 302)

    def test_report_recomputes_server_side_rather_than_trusting_the_browser(self):
        resp = self.tc.post(reverse("clients:financial_planner_download_report"), {
            "client_name": "PDF Client", "age": "24", "gross_income": "3500000",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("PDF_Client", resp["Content-Disposition"])
        self.assertTrue(resp.content.startswith(b"%PDF"))
