"""Financial plan engine — a port of docs/planner_fin/Financial_Plan_Template.xlsx.

One implementation. The web page, the PDF and any future app endpoint all call
`compute()`; nothing recalculates in the browser, so a number on screen is the
number in the report.

Layout mirrors the workbook: Inputs feed Insurance, Insurance's premium total
feeds the investible surplus, Retirement sizes the corpus, Goals check what is
affordable, Allocation splits the surplus and Projection walks it year by year.
The `note` on each row is the workbook's own "Formula / Logic Used" column —
that column is half the value of the sheet, so it ships with the numbers.
"""
import math

from ..templatetags.custom_filters import inr

# ─────────────────────────────── Excel maths ───────────────────────────────
# Same sign convention as Excel: money leaving you is negative.


def fv(rate, nper, pmt=0.0, pv_=0.0, due=0):
    if nper <= 0:
        return -pv_
    if rate == 0:
        return -(pv_ + pmt * nper)
    f = (1 + rate) ** nper
    return -(pv_ * f + pmt * (1 + rate * due) * (f - 1) / rate)


def pv(rate, nper, pmt=0.0, fv_=0.0, due=0):
    if rate == 0:
        return -(fv_ + pmt * nper)
    f = (1 + rate) ** nper
    return -(fv_ + pmt * (1 + rate * due) * (f - 1) / rate) / f


def pmt(rate, nper, pv_, fv_=0.0, due=0):
    if nper <= 0:
        return 0.0
    if rate == 0:
        return -(pv_ + fv_) / nper
    f = (1 + rate) ** nper
    return -(pv_ * f + fv_) * rate / ((1 + rate * due) * (f - 1))


def nper(rate, pmt_, pv_, fv_=0.0):
    """Periods until the balance hits zero. None when it never does."""
    if pmt_ == 0:
        return None
    if rate == 0:
        n = -(pv_ + fv_) / pmt_
        return n if n > 0 else None
    num = pmt_ - fv_ * rate
    den = pv_ * rate + pmt_
    if den == 0 or num / den <= 0:
        return None
    return math.log(num / den) / math.log(1 + rate)


def rate(nper_, pmt_, pv_, fv_=0.0):
    """Excel RATE by bisection — no derivative, no failure to converge."""
    def g(r):
        if r == 0:
            return pv_ + pmt_ * nper_ + fv_
        f = (1 + r) ** nper_
        return pv_ * f + pmt_ * (f - 1) / r + fv_

    lo, hi = -0.9999, 10.0
    glo, ghi = g(lo), g(hi)
    if glo * ghi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if g(lo) * g(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def ceiling(value, significance):
    """Excel CEILING — round up to the next multiple. Insurers sell in slabs."""
    if significance <= 0:
        return value
    return math.ceil(value / significance) * significance


def _band(table, age):
    """Excel INDEX/MATCH with match-type 1: last band whose floor <= age."""
    picked = table[0][1]
    for floor, value in table:
        if age >= floor:
            picked = value
    return picked


# ───────────────────────────── Lookup tables ──────────────────────────────
# Workbook "Tables" sheet. Indicative retail benchmarks for a healthy
# non-smoker — replace with actual quotes before anything is sold.

LIFE_MULTIPLE = [(0, 20), (30, 18), (40, 15), (45, 12), (50, 10), (55, 8)]
TERM_RATE_PER_LAKH = [(0, 105), (30, 135), (35, 190), (40, 285), (45, 450),
                      (50, 720), (55, 1150)]
HEALTH_BENCHMARK = [(0, 500000), (30, 750000), (40, 1000000), (50, 1500000),
                    (60, 2500000)]
HEALTH_RATE_PER_LAKH = [(0, 1400), (30, 1600), (40, 2200), (50, 3600), (60, 6500)]
GLIDE_PATH = [(0, (0.75, 0.15, 0.10)), (35, (0.70, 0.20, 0.10)),
              (40, (0.65, 0.25, 0.10)), (45, (0.60, 0.30, 0.10)),
              (50, (0.50, 0.40, 0.10)), (55, (0.40, 0.50, 0.10)),
              (60, (0.30, 0.60, 0.10)), (65, (0.25, 0.65, 0.10))]
TOPUP_PREMIUM_FACTOR = 0.30     # x the base rate per lakh
CI_RATE_PER_LAKH = 220
PA_RATE_PER_LAKH = 90
SURVIVOR_EXPENSE_RATIO = 0.75   # household costs if the earner dies
FUTURE_COVER_YEARS = [0, 5, 10, 15, 20, 25, 30, 36]
DELAY_OFFSETS = [0, 3, 5, 10, 15]


# ──────────────────────────────── Inputs ──────────────────────────────────
# (key, label, kind, default, unit, note). One list drives the form, the
# parser and the defaults, so a new input cannot be added to only two of them.
# kind: text | money | pct | num

INPUT_GROUPS = [
    ("A. Client Profile", [
        ("client_name", "Client Name", "text", "", "",
         "Free text label used on the summary and the report."),
        ("plan_year", "Plan Start Date (financial year)", "text", "FY 2026-27", "",
         "Reference year for all 'today's cost' figures."),
        ("age", "Current Age", "num", 30, "years",
         "Drives insurance multiples, premium rates and the allocation glide path."),
        ("retirement_age", "Planned Retirement Age", "num", 60, "years",
         "End of the accumulation phase."),
        ("life_expectancy", "Life Expectancy", "num", 85, "years",
         "Corpus must last until this age."),
        ("dependents", "Number of Financial Dependents", "num", 2, "count",
         "Used in the needs-based life cover method."),
        ("family_members", "Family Members to Insure (Health)", "num", 3, "count",
         "Self + spouse + children on a floater policy."),
        ("city_factor", "City Tier Factor", "num", 1.0, "x",
         "Metro 1.00, Tier-2 0.80, Tier-3 0.65. Scales the health benchmark to "
         "local hospitalisation costs."),
    ]),
    ("B. Income & Cash Flow", [
        ("gross_income", "Annual Gross Income", "money", 3500000, "₹ p.a.", ""),
        ("tax_rate", "Effective Tax Rate", "pct", 0.25, "%",
         "All-in effective rate including cess, not the marginal slab."),
        ("monthly_expenses", "Monthly Household Expenses", "money", 75000, "₹ p.m.",
         "All living costs incl. rent, EMIs, lifestyle. Excludes investments."),
        ("income_growth", "Expected Annual Income Growth", "pct", 0.08, "%",
         "Supports the step-up SIP: if income grows 8%, a 10% step-up is affordable."),
    ]),
    ("C. Existing Assets & Liabilities", [
        ("existing_equity", "Existing Equity Investments (MF / stocks / NPS-E)",
         "money", 500000, "₹", "Market value today."),
        ("existing_debt", "Existing Debt Investments (EPF / PPF / debt MF)",
         "money", 300000, "₹", "Balance today."),
        ("existing_cash", "Existing Cash, FD & Liquid Funds", "money", 200000, "₹",
         "Includes anything already set aside as emergency money."),
        ("home_loan", "Outstanding Home Loan", "money", 0, "₹", "Principal outstanding."),
        ("other_loans", "Other Loans (car / personal / education)", "money", 0, "₹",
         "Principal outstanding."),
        ("existing_life_cover", "Existing Life Cover (term + employer + others)",
         "money", 0, "₹",
         "Employer group cover is not portable — count it, do not rely on it."),
        ("existing_health_cover", "Existing Health Cover (personal + employer)",
         "money", 500000, "₹", "Typically employer floater only, at this stage."),
    ]),
    ("D. Inflation & Return Assumptions", [
        ("inflation", "General Inflation (CPI)", "pct", 0.06, "%",
         "Applied to living expenses and generic goals."),
        ("medical_inflation", "Medical Inflation", "pct", 0.10, "%",
         "Runs far above CPI in India. Applied to the health cover requirement."),
        ("education_inflation", "Education Inflation", "pct", 0.08, "%",
         "Applied to child education goals."),
        ("equity_return", "Equity Return (long term, pre-retirement)", "pct", 0.12, "%",
         "Nominal CAGR assumption for diversified Indian equity over 30+ years."),
        ("debt_return", "Debt Return", "pct", 0.07, "%",
         "EPF / PPF / debt MF blended nominal return."),
        ("equity_allocation", "Pre-Retirement Equity Allocation", "pct", 0.75, "%",
         "Starting equity weight. See the glide path for the full path."),
        ("post_ret_return", "Post-Retirement Portfolio Return", "pct", 0.075, "%",
         "Lower-risk drawdown portfolio, roughly 30% equity / 70% debt."),
        ("step_up", "Annual SIP Step-Up Rate", "pct", 0.10, "%",
         "How much the SIP rises each year. Should be <= expected income growth."),
    ]),
    ("E. Planning Parameters", [
        ("retirement_expense_ratio", "Retirement Expense as % of Current Expense",
         "pct", 0.75, "%",
         "Work and child costs fall away, healthcare rises. 70-80% is the standard band."),
        ("emergency_months", "Emergency Fund Target", "num", 6, "months",
         "6 months for a salaried single earner; 9-12 if income is variable."),
    ]),
    ("F. Insurance Assumptions", [
        ("self_consumption", "Self-Consumption Share of Income", "pct", 0.30, "%",
         "The portion the earner spends on themselves, which the family no longer "
         "needs. 25-35% is standard."),
        ("education_corpus", "Children's Education Corpus (today's value)",
         "money", 3000000, "₹",
         "Present-day cost of the education the earner promised to fund."),
        ("marriage_corpus", "Children's Marriage Corpus (today's value)",
         "money", 2500000, "₹", "Present-day cost. Zero if not a planned goal."),
        ("estate_costs", "Funeral / Immediate Estate Costs", "money", 500000, "₹",
         "Immediate liquidity for the family in the first six months."),
    ]),
    ("G. Retirement Assumptions", [
        ("medical_buffer", "Medical Buffer (today's value)", "money", 1000000, "₹",
         "Ring-fenced for treatment insurance does not cover. Grows at MEDICAL "
         "inflation, not CPI."),
        ("legacy", "Legacy / Estate to Leave Behind (today's value)", "money", 0, "₹",
         "Above zero only if the client wants to bequeath a specific amount."),
        ("epf_at_retirement", "Expected EPF Accumulation at Retirement", "money", 0, "₹",
         "Only if it is not already inside the existing corpus above — otherwise it "
         "is counted twice."),
    ]),
]

INPUT_FIELDS = {f[0]: f for group in INPUT_GROUPS for f in group[1]}

# Standalone calculators — independent of the plan, same field shape.
CALC_GROUPS = [
    ("SIP Future Value", "calc1", [
        ("c1_sip", "Monthly SIP Amount", "money", 25000, "₹ p.m.", ""),
        ("c1_return", "Expected Annual Return", "pct", 0.12, "%", ""),
        ("c1_years", "Investment Period", "num", 20, "years", ""),
    ]),
    ("Step-Up SIP Future Value", "calc2", [
        ("c2_sip", "Year-1 Monthly SIP", "money", 20000, "₹ p.m.", ""),
        ("c2_stepup", "Annual Step-Up Rate", "pct", 0.10, "%", ""),
        ("c2_return", "Expected Annual Return", "pct", 0.12, "%", ""),
        ("c2_years", "Investment Period", "num", 20, "years", ""),
    ]),
    ("Lumpsum Future Value", "calc3", [
        ("c3_amount", "Lumpsum Invested Today", "money", 1000000, "₹", ""),
        ("c3_return", "Expected Annual Return", "pct", 0.12, "%", ""),
        ("c3_years", "Investment Period", "num", 20, "years", ""),
    ]),
    ("SIP Needed for a Target", "calc4", [
        ("c4_target", "Target Amount (future value needed)", "money", 10000000, "₹", ""),
        ("c4_return", "Expected Annual Return", "pct", 0.12, "%", ""),
        ("c4_years", "Time Available", "num", 15, "years", ""),
    ]),
    ("Inflation Impact", "calc5", [
        ("c5_cost", "Cost Today", "money", 100000, "₹", ""),
        ("c5_inflation", "Inflation Rate", "pct", 0.06, "%",
         "10% for medical, 8% for education."),
        ("c5_years", "Number of Years", "num", 25, "years", ""),
    ]),
    ("SWP — How Long Will a Corpus Last?", "calc6", [
        ("c6_corpus", "Corpus Available", "money", 50000000, "₹", ""),
        ("c6_withdrawal", "Monthly Withdrawal", "money", 200000, "₹ p.m.", ""),
        ("c6_return", "Expected Annual Return on Corpus", "pct", 0.075, "%", ""),
    ]),
    ("Loan EMI", "calc7", [
        ("c7_loan", "Loan Amount", "money", 5000000, "₹", ""),
        ("c7_rate", "Annual Interest Rate", "pct", 0.085, "%", ""),
        ("c7_years", "Tenure", "num", 20, "years", ""),
    ]),
    ("Time & Rate Solvers", "calc8", [
        ("c8_target", "Target Amount", "money", 10000000, "₹", ""),
        ("c8_sip", "Monthly SIP You Can Afford", "money", 25000, "₹ p.m.", ""),
        ("c8_return", "Expected Annual Return", "pct", 0.12, "%", ""),
        ("c8_years", "Years You Actually Have", "num", 10, "years", ""),
    ]),
]

CALC_FIELDS = {f[0]: f for _, _, fields in CALC_GROUPS for f in fields}
ALL_FIELDS = {**INPUT_FIELDS, **CALC_FIELDS}

PRIORITIES = ["Critical", "High", "Medium", "Low"]

# name, priority, years, cost today, inflation, earmarked, return, link
DEFAULT_GOALS = [
    ("Emergency Fund", "Critical", 1, 0, 0.06, 0, 0.065, "emergency"),
    ("International Holiday", "Low", 2, 300000, 0.06, 0, 0.08, ""),
    ("Upskilling / Certification / PG", "High", 5, 1000000, 0.08, 0, 0.10, ""),
    ("Wedding", "High", 5, 1500000, 0.06, 0, 0.10, ""),
    ("Car Purchase (down payment + on-road)", "Medium", 7, 1000000, 0.06, 0, 0.11, ""),
    ("Home Down Payment", "High", 10, 3000000, 0.07, 0, 0.11, ""),
    ("Child — Higher Education (undergrad)", "High", 22, 3000000, 0.08, 0, 0.12, ""),
    ("Child — Post Graduation / Abroad", "Medium", 26, 4000000, 0.08, 0, 0.12, ""),
    ("Child — Marriage", "Medium", 30, 2500000, 0.06, 0, 0.12, ""),
]

ACTIONS = [
    ("Buy the term plan TODAY",
     "The premium is locked for the whole term at today's age. Every year of "
     "delay raises the lifetime cost by roughly 8-12%."),
    ("Buy a personal health floater independent of the employer",
     "Employer cover vanishes with the job, and conditions acquired meanwhile "
     "become permanently excluded."),
    ("Add the super top-up",
     "Large-claim protection at roughly a quarter of base-policy cost."),
    ("Add critical illness and personal accident cover",
     "These replace INCOME during illness or disability, which a hospitalisation "
     "policy does not."),
    ("Build the emergency fund in liquid funds and sweep-in FDs",
     "Until this exists, every other investment is really a forced-sale risk."),
    ("Start the retirement step-up SIP",
     "Compounding time is the single largest financial asset in the plan."),
    ("Layer in goal SIPs by horizon",
     "Under 3 years: debt only. 3-7 years: hybrid. Over 7 years: equity."),
    ("Register nominations, write a will, keep a document register",
     "The cheapest and most neglected step in the whole plan."),
    ("Review annually and after every life event",
     "Marriage, a child, a job change or a house purchase each invalidate at "
     "least one assumption above."),
]

DISCLAIMER = (
    "This is a planning model, not investment, insurance or tax advice. Return "
    "and inflation figures are long-run assumptions that no single decade is "
    "obliged to deliver. Premium rates are indicative and must be replaced with "
    "actual quotes. Tax treatment depends on the regime chosen and on rules that "
    "change. Have the plan reviewed by a SEBI-registered investment adviser "
    "before acting on it."
)


# ─────────────────────────────── Parsing ──────────────────────────────────


def _num(raw, default):
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return float(default)


def defaults():
    return {key: spec[3] for key, spec in ALL_FIELDS.items()}


def parse(data):
    """Read a POST/dict into the value dict compute() expects.

    Percent fields arrive as human percents (6 for 6%) and are stored as
    fractions, the way the workbook holds them.
    """
    values = {}
    for key, spec in ALL_FIELDS.items():
        kind, default = spec[2], spec[3]
        raw = data.get(key)
        if kind == "text":
            values[key] = (raw or default or "").strip()[:120]
        elif kind == "pct":
            values[key] = _num(raw, default * 100) / 100 if raw not in (None, "") else default
        else:
            values[key] = _num(raw, default)
    values["goals"] = _parse_goals(data)
    return values


def _parse_goals(data):
    getlist = getattr(data, "getlist", None)
    if getlist is None:
        return list(data.get("goals") or DEFAULT_GOALS)
    names = getlist("goal_name")
    if not names:
        return list(DEFAULT_GOALS)
    goals = []
    for i, name in enumerate(names):
        def col(field, default=""):
            vals = getlist(field)
            return vals[i] if i < len(vals) else default
        link = col("goal_link")
        if not (name or "").strip() and link != "emergency":
            continue
        priority = col("goal_priority")
        goals.append((
            (name or "").strip()[:80],
            priority if priority in PRIORITIES else "Medium",
            _num(col("goal_years"), 1),
            _num(col("goal_cost"), 0),
            _num(col("goal_inflation"), 6) / 100,
            _num(col("goal_earmarked"), 0),
            _num(col("goal_return"), 10) / 100,
            link,
        ))
    return goals or list(DEFAULT_GOALS)


# ─────────────────────────────── Formatting ───────────────────────────────


def _display(value, kind):
    """A field's value as it should sit in the input box."""
    if kind == "text":
        return value
    if kind == "pct":
        value = value * 100
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def form_groups(values):
    """The input form, ready to render: label, current value, unit, note."""
    def field(spec):
        key, label, kind, _default, unit, note = spec
        return {"key": key, "label": label, "kind": kind, "unit": unit,
                "note": note, "value": _display(values.get(key, spec[3]), kind)}

    plan_groups = [{"title": title, "fields": [field(f) for f in fields]}
                   for title, fields in INPUT_GROUPS]
    calc_groups = [{"title": title, "key": key,
                    "fields": [field(f) for f in fields]}
                   for title, key, fields in CALC_GROUPS]
    return plan_groups, calc_groups


def fmt(value, unit):
    if unit in ("", "text") or isinstance(value, str):
        return value
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit in ("years", "months", "count"):
        return f"{value:,.1f}".rstrip("0").rstrip(".") + f" {unit}"
    return f"₹{inr(value)}"


def short(value):
    """Crore/lakh notation — how an adviser says the number out loud, and the
    only way a ₹14 crore corpus fits in a KPI tile."""
    size = abs(value)
    if size >= 10000000:
        return f"₹{value / 10000000:.2f} Cr"
    if size >= 100000:
        return f"₹{value / 100000:.2f} L"
    return f"₹{inr(value)}"


def _row(label, value, unit, note="", big=False):
    return {"label": label, "value": fmt(value, unit), "raw": value,
            "unit": unit, "note": note, "big": big}


def _table(title, columns, rows, note=""):
    return {"title": title, "columns": columns, "rows": rows, "note": note}


# ──────────────────────────────── Engine ──────────────────────────────────


def compute(v):
    """The whole plan. `v` is the dict `parse()` returns."""
    r = {}

    # ── Cash flow, assets, derived rates (workbook: Inputs) ──
    age = v["age"]
    r["post_tax_income"] = v["gross_income"] * (1 - v["tax_rate"])
    r["monthly_post_tax"] = r["post_tax_income"] / 12
    r["annual_expenses"] = v["monthly_expenses"] * 12
    r["total_corpus"] = v["existing_equity"] + v["existing_debt"] + v["existing_cash"]
    r["liabilities"] = v["home_loan"] + v["other_loans"]
    r["net_worth"] = r["total_corpus"] - r["liabilities"]
    r["blended_return"] = (v["equity_return"] * v["equity_allocation"]
                           + v["debt_return"] * (1 - v["equity_allocation"]))
    r["real_pre"] = (1 + r["blended_return"]) / (1 + v["inflation"]) - 1
    r["real_post"] = (1 + v["post_ret_return"]) / (1 + v["inflation"]) - 1
    r["real_safe"] = (1 + v["debt_return"]) / (1 + v["inflation"]) - 1
    r["years_to_retirement"] = max(0.0, v["retirement_age"] - age)
    r["months_to_retirement"] = r["years_to_retirement"] * 12
    r["years_in_retirement"] = max(0.0, v["life_expectancy"] - v["retirement_age"])

    insurance = _insurance(v, r)
    # Premiums are spent before a single rupee is invested — that is why the
    # surplus is computed only after the protection bill is known.
    r["investible"] = r["post_tax_income"] - r["annual_expenses"] - r["total_premium"]
    r["monthly_investible"] = r["investible"] / 12
    r["savings_rate"] = r["investible"] / r["post_tax_income"] if r["post_tax_income"] else 0

    retirement = _retirement(v, r)
    goals = _goals(v, r)
    allocation = _allocation(v, r)
    projection = _projection(v, r)
    calculators = _calculators(v)

    return {
        "v": v,
        "r": r,
        "cashflow": _cashflow_sections(v, r),
        "insurance": insurance,
        "goals": goals,
        "retirement": retirement,
        "allocation": allocation,
        "projection": projection,
        "calculators": calculators,
        "summary": _summary(v, r),
        "actions": ACTIONS,
        "disclaimer": DISCLAIMER,
        "kpis": _kpis(r),
    }


def _cashflow_sections(v, r):
    return [
        {"title": "Income & Cash Flow", "rows": [
            _row("Annual Gross Income", v["gross_income"], "₹ p.a."),
            _row("Annual Post-Tax Income", r["post_tax_income"], "₹ p.a.",
                 "Gross Income x (1 - Effective Tax Rate)"),
            _row("Monthly Post-Tax Income", r["monthly_post_tax"], "₹ p.m."),
            _row("Annual Household Expenses", r["annual_expenses"], "₹ p.a.",
                 "Monthly Expenses x 12"),
            _row("Annual Insurance Premium Outgo", r["total_premium"], "₹ p.a.",
                 "Total of term + health + super top-up + critical illness + accident."),
            _row("Annual Investible Surplus", r["investible"], "₹ p.a.",
                 "Post-Tax Income - Living Expenses - Insurance Premiums", big=True),
            _row("Monthly Investible Surplus", r["monthly_investible"], "₹ p.m.",
                 "The budget every SIP in this plan must fit inside.", big=True),
            _row("Savings Rate", r["savings_rate"], "%",
                 "Investible Surplus / Post-Tax Income. Target 40%+ when young."),
        ]},
        {"title": "Assets & Liabilities", "rows": [
            _row("Total Existing Corpus", r["total_corpus"], "₹",
                 "Equity + Debt + Cash"),
            _row("Total Liabilities", r["liabilities"], "₹",
                 "Must be fully covered by life insurance."),
            _row("Net Worth", r["net_worth"], "₹", "Total Assets - Total Liabilities"),
        ]},
        {"title": "Derived Rates & Horizons", "rows": [
            _row("Blended Pre-Retirement Return", r["blended_return"], "%",
                 "(Equity Return x Equity Weight) + (Debt Return x Debt Weight). "
                 "The single growth rate used for all accumulation maths."),
            _row("Real Return — Pre-Retirement", r["real_pre"], "%",
                 "(1 + Nominal) / (1 + Inflation) - 1. Fisher equation, not the "
                 "crude 'return minus inflation'."),
            _row("Real Return — Post-Retirement", r["real_post"], "%",
                 "Used to size the corpus so withdrawals can rise with inflation "
                 "every year."),
            _row("Real Discount Rate for Insurance", r["real_safe"], "%",
                 "Debt-based, deliberately conservative: the family should not have "
                 "to take market risk."),
            _row("Years to Retirement", r["years_to_retirement"], "years"),
            _row("Months to Retirement", r["months_to_retirement"], "months"),
            _row("Years in Retirement", r["years_in_retirement"], "years",
                 "Life Expectancy - Retirement Age. The corpus must fund these."),
        ]},
    ]


def _insurance(v, r):
    age = v["age"]
    gross, post_tax = v["gross_income"], r["post_tax_income"]

    # Method 1 — income multiple.
    r["income_multiple"] = _band(LIFE_MULTIPLE, age)
    r["cover_income_multiple"] = gross * r["income_multiple"]

    # Method 2 — human life value.
    r["income_to_family"] = post_tax * (1 - v["self_consumption"])
    r["pv_lost_income"] = pv(r["real_safe"], r["years_to_retirement"],
                             -r["income_to_family"])
    r["cover_hlv"] = max(0.0, r["pv_lost_income"] + r["liabilities"] - r["total_corpus"])

    # Method 3 — needs based.
    r["survivor_expenses"] = r["annual_expenses"] * SURVIVOR_EXPENSE_RATIO
    r["dependency_years"] = max(0.0, v["life_expectancy"] - age)
    r["pv_family_expenses"] = pv(r["real_safe"], r["dependency_years"],
                                 -r["survivor_expenses"])
    r["cover_needs"] = max(0.0, r["pv_family_expenses"] + r["liabilities"]
                           + v["education_corpus"] + v["marriage_corpus"]
                           + v["estate_costs"] - r["total_corpus"])

    r["cover_highest"] = max(r["cover_income_multiple"], r["cover_hlv"], r["cover_needs"])
    r["cover_additional"] = max(0.0, r["cover_highest"] - v["existing_life_cover"])
    r["term_cover"] = ceiling(r["cover_additional"], 2500000)
    r["term_years"] = max(0.0, 70 - age)
    r["term_rate"] = _band(TERM_RATE_PER_LAKH, age)
    r["term_premium"] = r["term_cover"] / 100000 * r["term_rate"]
    r["term_premium_pct"] = r["term_premium"] / gross if gross else 0

    # Health.
    r["health_benchmark"] = _band(HEALTH_BENCHMARK, age)
    r["health_income_min"] = gross * 0.5
    r["family_loading"] = 1 + (v["family_members"] - 1) * 0.25
    r["health_base"] = ceiling(max(r["health_benchmark"], r["health_income_min"])
                               * v["city_factor"] * r["family_loading"], 500000)
    r["health_topup"] = ceiling(r["health_base"] * 3, 500000)
    r["health_total"] = r["health_base"] + r["health_topup"]
    r["health_additional"] = max(0.0, r["health_base"] - v["existing_health_cover"])
    r["health_rate"] = _band(HEALTH_RATE_PER_LAKH, age)
    r["health_base_premium"] = r["health_base"] / 100000 * r["health_rate"]
    r["health_topup_premium"] = (r["health_topup"] / 100000 * r["health_rate"]
                                 * TOPUP_PREMIUM_FACTOR)

    # Critical illness & personal accident.
    r["ci_cover"] = ceiling(min(gross * 3, 5000000), 500000)
    r["pa_cover"] = ceiling(min(gross * 10, 20000000), 500000)
    r["ci_premium"] = r["ci_cover"] / 100000 * CI_RATE_PER_LAKH
    r["pa_premium"] = r["pa_cover"] / 100000 * PA_RATE_PER_LAKH

    r["total_premium"] = (r["term_premium"] + r["health_base_premium"]
                          + r["health_topup_premium"] + r["ci_premium"] + r["pa_premium"])
    r["monthly_premium"] = r["total_premium"] / 12
    r["premium_pct"] = r["total_premium"] / gross if gross else 0
    r["deduction_80d"] = min(r["health_base_premium"] + r["health_topup_premium"], 25000)

    future_cover = [
        [f"{int(y)}", f"{age + y:.0f}", fmt(fv(v["medical_inflation"], y, 0,
                                               -r["health_total"]), "₹")]
        for y in FUTURE_COVER_YEARS
    ]

    return {
        "sections": [
            {"title": "Life Cover — Method 1: Income Multiple", "rows": [
                _row("Applicable Income Multiple", r["income_multiple"], "x",
                     "Age-banded lookup — the multiple falls as fewer earning "
                     "years remain."),
                _row("Cover by Income Multiple", r["cover_income_multiple"], "₹",
                     "Gross Income x Multiple. A sanity check only: it ignores "
                     "liabilities, assets and actual family need."),
            ]},
            {"title": "Life Cover — Method 2: Human Life Value", "rows": [
                _row("Income Available to Family", r["income_to_family"], "₹ p.a.",
                     "Post-Tax Income x (1 - Self-Consumption Share)"),
                _row("Present Value of Lost Future Income", r["pv_lost_income"], "₹",
                     "PV(real safe rate, years to retirement, income to family). The "
                     "REAL rate means the payout can rise with inflation every year."),
                _row("Add: Outstanding Liabilities", r["liabilities"], "₹",
                     "Loans must be cleared, not serviced, on death."),
                _row("Less: Existing Corpus", -r["total_corpus"], "₹"),
                _row("Human Life Value Cover Required", r["cover_hlv"], "₹", "", big=True),
            ]},
            {"title": "Life Cover — Method 3: Needs-Based", "rows": [
                _row("Family Expenses if Earner Dies", r["survivor_expenses"], "₹ p.a.",
                     "Current expenses x 75%. Household costs fall, but not "
                     "proportionally."),
                _row("Dependency Period", r["dependency_years"], "years",
                     "Life Expectancy - Current Age. The family needs support for "
                     "the survivor's whole life, not until the earner would have retired."),
                _row("Present Value of Family Expenses", r["pv_family_expenses"], "₹"),
                _row("Add: Outstanding Liabilities", r["liabilities"], "₹"),
                _row("Add: Children's Education Corpus", v["education_corpus"], "₹"),
                _row("Add: Children's Marriage Corpus", v["marriage_corpus"], "₹"),
                _row("Add: Funeral / Estate Costs", v["estate_costs"], "₹"),
                _row("Less: Existing Corpus", -r["total_corpus"], "₹"),
                _row("Needs-Based Cover Required", r["cover_needs"], "₹", "", big=True),
            ]},
            {"title": "Life Cover — Recommendation", "rows": [
                _row("Highest of the Three Methods", r["cover_highest"], "₹",
                     "Always plan to the highest — under-insurance cannot be fixed "
                     "after the event."),
                _row("Less: Existing Life Cover", -v["existing_life_cover"], "₹"),
                _row("Additional Cover Needed", r["cover_additional"], "₹"),
                _row("RECOMMENDED TERM COVER", r["term_cover"], "₹",
                     "Rounded up to the next ₹25L — insurers sell in slabs and "
                     "rounding up is free protection.", big=True),
                _row("Recommended Policy Term (to age 70)", r["term_years"], "years",
                     "Cover must run past retirement: liabilities and dependants "
                     "can outlive the salary."),
                _row("Premium Rate per ₹1 Lakh", r["term_rate"], "₹ p.a."),
                _row("Estimated Annual Term Premium", r["term_premium"], "₹ p.a.",
                     "(Cover / 1,00,000) x Rate per Lakh", big=True),
                _row("Term Premium as % of Gross Income", r["term_premium_pct"], "%"),
            ]},
            {"title": "Health Insurance", "rows": [
                _row("Age-Based Cover Benchmark", r["health_benchmark"], "₹"),
                _row("Income-Linked Minimum (50% of gross)", r["health_income_min"], "₹"),
                _row("City Tier Factor", v["city_factor"], "x",
                     "Metro hospitalisation costs 25-50% more than Tier-3."),
                _row("Family Size Loading", r["family_loading"], "x",
                     "1 + (Members - 1) x 25%. A floater needs more than a "
                     "single-life sum insured, but not N times it — claims rarely "
                     "coincide."),
                _row("RECOMMENDED BASE COVER (floater)", r["health_base"], "₹",
                     "CEILING( MAX(benchmark, income minimum) x City x Family, ₹5L )",
                     big=True),
                _row("RECOMMENDED SUPER TOP-UP", r["health_topup"], "₹",
                     "Base x 3, with a deductible equal to the base. The single most "
                     "cost-efficient move in health planning.", big=True),
                _row("Total Effective Health Cover", r["health_total"], "₹"),
                _row("Less: Existing Health Cover", -v["existing_health_cover"], "₹",
                     "Employer cover ends with employment — a bonus, not the plan."),
                _row("Additional Base Cover to Buy", r["health_additional"], "₹"),
                _row("Premium Rate per ₹1 Lakh", r["health_rate"], "₹ p.a."),
                _row("Estimated Base Policy Premium", r["health_base_premium"], "₹ p.a."),
                _row("Estimated Super Top-Up Premium", r["health_topup_premium"],
                     "₹ p.a.", "Roughly a third of the base rate per lakh."),
            ]},
            {"title": "Critical Illness & Personal Accident", "rows": [
                _row("Critical Illness Cover", r["ci_cover"], "₹",
                     "MIN(3x gross income, ₹50L). A lump sum on diagnosis — it "
                     "replaces INCOME during treatment, which hospitalisation cover "
                     "does not.", big=True),
                _row("Personal Accident Cover", r["pa_cover"], "₹",
                     "MIN(10x gross income, ₹2 Cr). Covers disability — likelier "
                     "than death when young, and financially worse.", big=True),
                _row("Estimated Critical Illness Premium", r["ci_premium"], "₹ p.a."),
                _row("Estimated Personal Accident Premium", r["pa_premium"], "₹ p.a."),
            ]},
            {"title": "Total Protection Cost", "rows": [
                _row("TOTAL ANNUAL PREMIUM OUTGO", r["total_premium"], "₹ p.a.",
                     "Term + Health Base + Super Top-Up + Critical Illness + "
                     "Accident. Deducted before any SIP is sized.", big=True),
                _row("Monthly Equivalent", r["monthly_premium"], "₹ p.m."),
                _row("Total Premium as % of Gross Income", r["premium_pct"], "%",
                     "Green under 5%, review above 7%."),
                _row("Section 80D Deduction (old regime)", r["deduction_80d"], "₹",
                     "MIN(health premiums, ₹25,000) for self+family under 60. Nil "
                     "benefit under the new regime."),
            ]},
        ],
        "future_cover": _table(
            "Future Health Cover Requirement (medical inflation)",
            ["Years From Now", "Age Then", "Cover Needed Then"], future_cover,
            "Today's cover buys progressively less treatment. Review and step up "
            "the sum insured every five years."),
    }


def _retirement(v, r):
    years, in_ret = r["years_to_retirement"], r["years_in_retirement"]
    blended, infl = r["blended_return"], v["inflation"]

    r["retirement_expenses_today"] = r["annual_expenses"] * v["retirement_expense_ratio"]
    r["first_year_expense"] = fv(infl, years, 0, -r["retirement_expenses_today"])
    r["first_year_monthly"] = r["first_year_expense"] / 12
    r["corpus_living"] = pv(r["real_post"], in_ret, -r["first_year_expense"], 0, 1)
    r["medical_buffer_at_ret"] = fv(v["medical_inflation"], years, 0, -v["medical_buffer"])
    r["legacy_at_ret"] = fv(infl, years, 0, -v["legacy"])
    r["corpus_required"] = (r["corpus_living"] + r["medical_buffer_at_ret"]
                            + r["legacy_at_ret"])
    r["corpus_today"] = r["corpus_required"] / (1 + infl) ** years
    r["corpus_30x"] = r["first_year_expense"] * 30
    r["withdrawal_rate"] = (r["first_year_expense"] / r["corpus_required"]
                            if r["corpus_required"] else 0)

    # Cash and FDs are deliberately excluded — that money is the emergency fund
    # on the Goals tab, and counting it twice overstates the retirement position.
    r["retirement_existing"] = v["existing_equity"] + v["existing_debt"]
    r["existing_at_retirement"] = fv(blended, years, 0, -r["retirement_existing"])
    r["expected_without_new"] = r["existing_at_retirement"] + v["epf_at_retirement"]
    r["corpus_gap"] = max(0.0, r["corpus_required"] - r["expected_without_new"])

    r["level_sip"] = (0.0 if r["corpus_gap"] == 0
                      else -pmt(blended / 12, years * 12, 0, r["corpus_gap"]))
    r["stepup_sip"] = _stepup_first_payment(r["corpus_gap"], blended, v["step_up"], years)
    r["lumpsum_today"] = (r["corpus_gap"] / (1 + blended) ** years if years else r["corpus_gap"])
    surplus = r["monthly_investible"]
    r["level_sip_pct"] = r["level_sip"] / surplus if surplus else 0
    r["stepup_sip_pct"] = r["stepup_sip"] / surplus if surplus else 0
    g = v["step_up"]
    r["total_invested"] = (r["stepup_sip"] * 12 * years if abs(g) < 1e-9
                           else r["stepup_sip"] * 12 * ((1 + g) ** years - 1) / g)
    r["wealth_from_compounding"] = max(0.0, r["corpus_gap"] - r["total_invested"])

    delay = []
    for offset in DELAY_OFFSETS:
        start_age = v["age"] + offset
        left = v["retirement_age"] - start_age
        sip = _stepup_first_payment(r["corpus_gap"], blended, g, left) if left > 0 else 0.0
        delay.append([f"{start_age:.0f}", f"{max(0, left):.0f} years", fmt(sip, "₹ p.m.")])

    return {
        "sections": [
            {"title": "Retirement Expense Projection", "rows": [
                _row("Retirement Expenses in TODAY'S money",
                     r["retirement_expenses_today"], "₹ p.a.",
                     "Current Annual Expenses x Retirement Expense Ratio"),
                _row("Annual Expenses in the FIRST year of retirement",
                     r["first_year_expense"], "₹ p.a.",
                     "Inflated to the retirement date — this is the number that "
                     "shocks clients.", big=True),
                _row("Monthly Expenses in the first year", r["first_year_monthly"],
                     "₹ p.m."),
            ]},
            {"title": "Corpus Required at Retirement", "rows": [
                _row("Corpus to Fund Inflation-Linked Living Expenses",
                     r["corpus_living"], "₹",
                     "Annuity-DUE present value at the REAL post-retirement rate: "
                     "expenses are withdrawn at the START of each year and every "
                     "withdrawal rises with inflation."),
                _row("Medical Buffer at Retirement", r["medical_buffer_at_ret"], "₹",
                     "Grown at MEDICAL inflation, not CPI."),
                _row("Legacy at Retirement", r["legacy_at_ret"], "₹"),
                _row("TOTAL RETIREMENT CORPUS REQUIRED", r["corpus_required"], "₹",
                     "Living Expense Corpus + Medical Buffer + Legacy", big=True),
                _row("Corpus in TODAY'S purchasing power", r["corpus_today"], "₹",
                     "The headline number is large mostly because money shrinks."),
                _row("Cross-check: 30x First-Year Expense", r["corpus_30x"], "₹",
                     "The 3.33% safe-withdrawal thumb rule. Should land within ~15% "
                     "of the PV answer; a big divergence means an assumption needs "
                     "revisiting."),
                _row("Implied Initial Withdrawal Rate", r["withdrawal_rate"], "%",
                     "At or under 4% is historically survivable over 25 years; "
                     "under 3.5% for 30+."),
            ]},
            {"title": "What the Client Already Has", "rows": [
                _row("Existing Corpus Available for Retirement",
                     r["retirement_existing"], "₹",
                     "Equity + Debt. Excludes cash and FDs — that is the emergency "
                     "fund and cannot be spent twice."),
                _row("Future Value at Retirement", r["existing_at_retirement"], "₹",
                     "Money already invested does the heavy lifting: it compounds "
                     "untouched for the whole runway."),
                _row("Expected EPF Accumulation", v["epf_at_retirement"], "₹"),
                _row("Total Expected Without New Investment",
                     r["expected_without_new"], "₹"),
                _row("RETIREMENT CORPUS GAP", r["corpus_gap"], "₹",
                     "What new monthly investing has to create.", big=True),
            ]},
            {"title": "How to Fund the Gap", "rows": [
                _row("Option 1 — LEVEL Monthly SIP", r["level_sip"], "₹ p.m.",
                     "The fixed instalment whose future value equals the gap."),
                _row("Option 2 — STEP-UP SIP, year 1", r["stepup_sip"], "₹ p.m.",
                     "Growing-annuity future value solved for the first payment. "
                     "RECOMMENDED: start lower and raise it each year as income "
                     "grows.", big=True),
                _row("Option 3 — ONE-TIME Lumpsum Today", r["lumpsum_today"], "₹",
                     "Present value of the gap."),
                _row("Level SIP as % of Monthly Surplus", r["level_sip_pct"], "%"),
                _row("Step-Up SIP as % of Monthly Surplus", r["stepup_sip_pct"], "%"),
                _row("Total SIP over the accumulation period", r["total_invested"], "₹",
                     "Sum of a geometric series — the money actually invested."),
                _row("Wealth Created by Compounding", r["wealth_from_compounding"], "₹",
                     "Gap - Total Invested. The portion returns produced rather than "
                     "the client's savings.", big=True),
            ]},
        ],
        "delay": _table(
            "The Cost of Delay",
            ["Start Investing At Age", "Years of Compounding", "Step-Up SIP Needed"],
            delay,
            "The same target corpus, started later, costs dramatically more "
            "per month."),
    }


def _stepup_first_payment(gap, ret, growth, years):
    """First monthly instalment of a growing annuity that reaches `gap`."""
    if gap <= 0 or years <= 0:
        return 0.0
    if abs(ret - growth) < 0.0001:
        return gap / (years * (1 + ret) ** (years - 1)) / 12
    return gap * (ret - growth) / ((1 + ret) ** years - (1 + growth) ** years) / 12


def _goals(v, r):
    rows, totals = [], {"cost": 0.0, "future": 0.0, "earmarked": 0.0,
                        "fv_earmarked": 0.0, "shortfall": 0.0, "sip": 0.0, "lumpsum": 0.0}
    by_priority = {p: 0.0 for p in PRIORITIES}
    inputs = []

    for name, priority, years, cost, infl, earmarked, ret, link in v["goals"]:
        if link == "emergency":
            # Stays wired to the inputs, exactly as the workbook links it.
            cost = v["monthly_expenses"] * v["emergency_months"]
            earmarked = v["existing_cash"]
        future = fv(infl, years, 0, -cost)
        fv_earmarked = fv(ret, years, 0, -earmarked)
        shortfall = max(0.0, future - fv_earmarked)
        sip = 0.0 if shortfall == 0 else -pmt(ret / 12, years * 12, 0, shortfall)
        lumpsum = shortfall / (1 + ret) ** years if years else shortfall
        rows.append({
            "name": name, "priority": priority, "years": years, "cost": cost,
            "inflation": infl, "earmarked": earmarked, "ret": ret,
            "link": link, "future": future, "fv_earmarked": fv_earmarked,
            "shortfall": shortfall, "sip": sip, "lumpsum": lumpsum,
            "cells": [fmt(future, "₹"), fmt(fv_earmarked, "₹"), fmt(shortfall, "₹"),
                      fmt(sip, "₹ p.m."), fmt(lumpsum, "₹")],
            # What the editable cells show — percents as percents, the way
            # they were typed, so a round-trip through the form is lossless.
            "in_years": _display(years, "num"),
            "in_cost": _display(cost, "num"),
            "in_inflation": _display(infl, "pct"),
            "in_earmarked": _display(earmarked, "num"),
            "in_return": _display(ret, "pct"),
        })
        inputs.append((name, priority, years, cost, infl, earmarked, ret, link))
        totals["cost"] += cost
        totals["future"] += future
        totals["earmarked"] += earmarked
        totals["fv_earmarked"] += fv_earmarked
        totals["shortfall"] += shortfall
        totals["sip"] += sip
        totals["lumpsum"] += lumpsum
        by_priority[priority] = by_priority.get(priority, 0.0) + sip

    r["goals_future_cost"] = totals["future"]
    r["goals_shortfall"] = totals["shortfall"]
    r["goals_sip_all"] = totals["sip"]
    r["goals_sip_critical_high"] = by_priority["Critical"] + by_priority["High"]
    r["goals_sip_deferrable"] = totals["sip"] - r["goals_sip_critical_high"]
    r["committed_now"] = r["goals_sip_critical_high"] + r["stepup_sip"]
    r["surplus_after_committed"] = r["monthly_investible"] - r["committed_now"]
    r["committed_pct"] = (r["committed_now"] / r["monthly_investible"]
                          if r["monthly_investible"] else 0)
    r["surplus_if_concurrent"] = (r["monthly_investible"] - totals["sip"] - r["stepup_sip"])
    r["emergency_target"] = v["monthly_expenses"] * v["emergency_months"]

    return {
        "rows": rows,
        "totals": totals,
        "totals_fmt": [fmt(totals[k], u) for k, u in
                       [("cost", "₹"), ("future", "₹"), ("earmarked", "₹"),
                        ("fv_earmarked", "₹"), ("shortfall", "₹"),
                        ("sip", "₹ p.m."), ("lumpsum", "₹")]],
        "affordability": [
            _row("Monthly Investible Surplus available", r["monthly_investible"], "₹ p.m."),
            _row("Monthly SIP if ALL goals ran concurrently", totals["sip"], "₹ p.m.",
                 "Goals rarely all start on day one — treat this as the ceiling, "
                 "not the plan."),
            _row("Monthly SIP for CRITICAL + HIGH priority goals",
                 r["goals_sip_critical_high"], "₹ p.m.", "This is what starts now."),
            _row("Monthly SIP for MEDIUM + LOW priority goals (deferrable)",
                 r["goals_sip_deferrable"], "₹ p.m.",
                 "Started as income rises or as earlier goals complete and free "
                 "up their instalment."),
            _row("Monthly SIP for Retirement (step-up, year 1)", r["stepup_sip"],
                 "₹ p.m.", "Non-negotiable — the longest runway, the least ability "
                 "to be made up later."),
            _row("COMMITTED NOW (Critical + High goals + Retirement)",
                 r["committed_now"], "₹ p.m.", "", big=True),
            _row("Surplus / (Deficit) after what is committed now",
                 r["surplus_after_committed"], "₹ p.m.",
                 "Negative means stretch a horizon, trim a goal, or raise income "
                 "before adding anything.", big=True),
            _row("Committed as % of Investible Surplus", r["committed_pct"], "%",
                 "Anything under 100% is fundable today."),
            _row("Surplus / (Deficit) if every goal ran concurrently",
                 r["surplus_if_concurrent"], "₹ p.m.",
                 "Usually negative, and that is the point: it is the arithmetic "
                 "proof that goals must be SEQUENCED, not stacked."),
        ],
        "note": "Under 3 years fund at 6.5-8% (liquid / arbitrage), 3-7 years at "
                "9-11% (hybrid), over 7 years at 12% (equity). Never fund a goal "
                "under three years away with equity.",
    }


def _allocation(v, r):
    equity, debt, gold = _band(GLIDE_PATH, v["age"])
    surplus = r["monthly_investible"]
    r["alloc_equity"], r["alloc_debt"], r["alloc_gold"] = equity, debt, gold
    r["alloc_equity_amt"] = surplus * equity
    r["alloc_debt_amt"] = surplus * debt
    r["alloc_gold_amt"] = surplus * gold

    glide = []
    for age in range(25, 90, 5):
        e, d, g = _band(GLIDE_PATH, age)
        glide.append([str(age), fmt(e, "%"), fmt(d, "%"), fmt(g, "%")])

    return {
        "rows": [
            _row("Equity Allocation", equity, "%"),
            _row("Debt Allocation", debt, "%"),
            _row("Gold / Commodity Allocation", gold, "%"),
        ],
        "split": [
            _row("Into Equity (index + flexicap + midcap SIPs)",
                 r["alloc_equity_amt"], "₹ p.m.", "", big=True),
            _row("Into Debt (EPF / PPF / short-duration & corporate bond funds)",
                 r["alloc_debt_amt"], "₹ p.m.", "", big=True),
            _row("Into Gold (sovereign gold / gold ETF)", r["alloc_gold_amt"],
                 "₹ p.m.", "", big=True),
        ],
        "glide": _table("Glide Path — How the Mix Shifts with Age",
                        ["Age", "Equity %", "Debt %", "Gold %"], glide,
                        "Check the actual portfolio against these weights once a "
                        "year and after any move above 10 percentage points. Drift "
                        "is what quietly turns a 75/25 portfolio into a 90/10 one "
                        "right before it matters."),
        "note": "Until the emergency fund is fully built, route 100% of the "
                "surplus into liquid funds and sweep-in FDs regardless of the "
                "split above. Allocation matters only once the safety net exists.",
    }


def _projection(v, r):
    """Year-by-year accumulation then drawdown.

    Withdrawals come out at the START of the year so they earn nothing, and the
    year's investment lands at the END so it earns nothing either — the same
    timing the corpus maths solves under, which is why the two agree.
    """
    rows = []
    opening = r["retirement_existing"]
    total_years = int(round(v["life_expectancy"] - v["age"])) + 1
    for index in range(total_years):
        age = v["age"] + index
        accumulating = age < v["retirement_age"]
        invest = (r["stepup_sip"] * 12 * (1 + v["step_up"]) ** index
                  if accumulating else 0.0)
        withdraw = (0.0 if accumulating
                    else r["first_year_expense"] * (1 + v["inflation"]) ** (age - v["retirement_age"]))
        growth_rate = r["blended_return"] if accumulating else v["post_ret_return"]
        growth = (opening - withdraw) * growth_rate
        closing = opening - withdraw + growth + invest
        today = closing / (1 + v["inflation"]) ** (index + 1)
        phase = "Accumulation" if accumulating else "Retirement"
        rows.append({
            "year": index + 1, "age": age, "phase": phase,
            "opening": opening, "invest": invest, "withdraw": withdraw,
            "rate": growth_rate, "growth": growth, "closing": closing,
            "today": today,
            "cells": [str(index + 1), f"{age:.0f}", phase, fmt(opening, "₹"),
                      fmt(invest, "₹"), fmt(withdraw, "₹"), fmt(growth_rate, "%"),
                      fmt(growth, "₹"), fmt(closing, "₹"), fmt(today, "₹")],
        })
        opening = closing
    r["corpus_at_life_expectancy"] = rows[-1]["closing"] if rows else 0.0
    return rows


def _calculators(v):
    out = []

    fv1 = fv(v["c1_return"] / 12, v["c1_years"] * 12, -v["c1_sip"])
    invested1 = v["c1_sip"] * v["c1_years"] * 12
    out.append(("SIP Future Value", "calc1", [
        _row("Future Value", fv1, "₹", "", big=True),
        _row("Total Amount Invested", invested1, "₹"),
        _row("Wealth Gained", fv1 - invested1, "₹"),
        _row("Gain Multiple", fv1 / invested1 if invested1 else 0, "x"),
    ]))

    r2, g2, n2, a2 = v["c2_return"], v["c2_stepup"], v["c2_years"], v["c2_sip"] * 12
    fv2 = (a2 * n2 * (1 + r2) ** (n2 - 1) if abs(r2 - g2) < 0.0001
           else a2 * ((1 + r2) ** n2 - (1 + g2) ** n2) / (r2 - g2))
    invested2 = a2 * n2 if abs(g2) < 0.0001 else a2 * ((1 + g2) ** n2 - 1) / g2
    out.append(("Step-Up SIP Future Value", "calc2", [
        _row("Future Value", fv2, "₹", "Growing-annuity future value.", big=True),
        _row("Total Amount Invested", invested2, "₹"),
        _row("Wealth Gained", fv2 - invested2, "₹"),
    ]))

    fv3 = fv(v["c3_return"], v["c3_years"], 0, -v["c3_amount"])
    out.append(("Lumpsum Future Value", "calc3", [
        _row("Future Value", fv3, "₹", "", big=True),
        _row("Wealth Gained", fv3 - v["c3_amount"], "₹"),
    ]))

    sip4 = -pmt(v["c4_return"] / 12, v["c4_years"] * 12, 0, v["c4_target"])
    out.append(("SIP Needed for a Target", "calc4", [
        _row("Monthly SIP Required", sip4, "₹ p.m.", "", big=True),
        _row("Lumpsum Required Instead",
             v["c4_target"] / (1 + v["c4_return"]) ** v["c4_years"], "₹"),
    ]))

    cost5 = fv(v["c5_inflation"], v["c5_years"], 0, -v["c5_cost"])
    out.append(("Inflation Impact", "calc5", [
        _row("Cost After N Years", cost5, "₹", "", big=True),
        _row("Today's Value of that Future Amount",
             cost5 / (1 + v["c5_inflation"]) ** v["c5_years"], "₹",
             "Should return the cost today — the check that both directions agree."),
        _row("Purchasing Power Lost",
             1 - 1 / (1 + v["c5_inflation"]) ** v["c5_years"], "%"),
    ]))

    months6 = nper(v["c6_return"] / 12, v["c6_withdrawal"], -v["c6_corpus"])
    lasts_forever = months6 is None
    out.append(("SWP — How Long Will a Corpus Last?", "calc6", [
        _row("Months the Corpus Lasts",
             "Never depletes" if lasts_forever else months6, "" if lasts_forever else "months",
             "The withdrawal is smaller than the return earned." if lasts_forever else "",
             big=True),
        _row("Years the Corpus Lasts",
             "Never depletes" if lasts_forever else months6 / 12,
             "" if lasts_forever else "years"),
        _row("Withdrawal Rate",
             v["c6_withdrawal"] * 12 / v["c6_corpus"] if v["c6_corpus"] else 0, "%",
             "Keep at or under 4% for a 25-year retirement; 3-3.5% if it could run 30+."),
    ]))

    emi = -pmt(v["c7_rate"] / 12, v["c7_years"] * 12, v["c7_loan"])
    repaid = emi * v["c7_years"] * 12
    out.append(("Loan EMI", "calc7", [
        _row("Monthly EMI", emi, "₹ p.m.", "", big=True),
        _row("Total Amount Repaid", repaid, "₹"),
        _row("Total Interest Paid", repaid - v["c7_loan"], "₹"),
        _row("Interest as % of Loan",
             (repaid - v["c7_loan"]) / v["c7_loan"] if v["c7_loan"] else 0, "%"),
    ]))

    months8 = nper(v["c8_return"] / 12, -v["c8_sip"], 0, v["c8_target"])
    needed = rate(v["c8_years"] * 12, -v["c8_sip"], 0, v["c8_target"])
    out.append(("Time & Rate Solvers", "calc8", [
        _row("Years Needed to Reach the Target",
             months8 / 12 if months8 else "Never at this SIP",
             "years" if months8 else "", "", big=True),
        _row("Return Required to Hit the Target in Time",
             needed * 12 if needed is not None else None, "%",
             "Above ~13-14% the goal is not realistic — change the amount, the SIP "
             "or the timeline, not the assumption."),
        _row("Real (Inflation-Adjusted) Return",
             (1 + v["c8_return"]) / (1 + v["inflation"]) - 1, "%",
             "Fisher equation against the plan's own inflation assumption."),
        _row("Years for Money to Double",
             math.log(2) / math.log(1 + v["c8_return"]) if v["c8_return"] > 0 else None,
             "years", "The exact version of the Rule of 72."),
    ]))
    return out


def _summary(v, r):
    return [
        {"title": "Client & Cash Flow", "rows": [
            _row("Client", v["client_name"] or "—", ""),
            _row("Age / Retirement / Life Expectancy",
                 f"{v['age']:.0f} / {v['retirement_age']:.0f} / {v['life_expectancy']:.0f}", ""),
            _row("Annual Post-Tax Income", r["post_tax_income"], "₹ p.a."),
            _row("Annual Living Expenses", r["annual_expenses"], "₹ p.a."),
            _row("Annual Insurance Premiums", r["total_premium"], "₹ p.a."),
            _row("Monthly Investible Surplus", r["monthly_investible"], "₹ p.m.", "", big=True),
            _row("Savings Rate", r["savings_rate"], "%"),
            _row("Net Worth Today", r["net_worth"], "₹"),
        ]},
        {"title": "Step 1 — Protection (buy this before investing a single rupee)",
         "rows": [
            _row("Term Life Cover to Buy", r["term_cover"], "₹", "", big=True),
            _row("Term Policy Tenure", r["term_years"], "years"),
            _row("Estimated Term Premium", r["term_premium"], "₹ p.a."),
            _row("Health Base Cover to Hold", r["health_base"], "₹"),
            _row("Super Top-Up to Add", r["health_topup"], "₹"),
            _row("Total Effective Health Cover", r["health_total"], "₹", "", big=True),
            _row("Critical Illness Cover", r["ci_cover"], "₹"),
            _row("Personal Accident Cover", r["pa_cover"], "₹"),
            _row("TOTAL ANNUAL PREMIUM", r["total_premium"], "₹ p.a.", "", big=True),
            _row("Premium as % of Gross Income", r["premium_pct"], "%",
                 "Should stay under 5%."),
        ]},
        {"title": "Step 2 — Emergency Fund & Goals", "rows": [
            _row("Emergency Fund Target", r["emergency_target"], "₹", "", big=True),
            _row("Total Future Cost of All Goals", r["goals_future_cost"], "₹"),
            _row("Monthly SIP if All Goals Ran Concurrently", r["goals_sip_all"],
                 "₹ p.m.", "The ceiling, not the plan — goals are sequenced."),
        ]},
        {"title": "Step 3 — Retirement", "rows": [
            _row("Years to Retirement", r["years_to_retirement"], "years"),
            _row("First-Year Retirement Expense", r["first_year_expense"], "₹ p.a."),
            _row("RETIREMENT CORPUS REQUIRED", r["corpus_required"], "₹", "", big=True),
            _row("Same Corpus in Today's Money", r["corpus_today"], "₹"),
            _row("Value of Existing Corpus at Retirement",
                 r["existing_at_retirement"], "₹"),
            _row("CORPUS GAP TO FUND", r["corpus_gap"], "₹", "", big=True),
            _row("Level Monthly SIP (never increased)", r["level_sip"], "₹ p.m."),
            _row("RECOMMENDED: Step-Up SIP, year 1", r["stepup_sip"], "₹ p.m.",
                 "Raise it each year as income grows.", big=True),
            _row("Or One-Time Lumpsum Today", r["lumpsum_today"], "₹"),
            _row("Wealth Created by Compounding", r["wealth_from_compounding"], "₹"),
        ]},
        {"title": "Step 4 — Asset Allocation of the Monthly Surplus", "rows": [
            _row("Equity", r["alloc_equity_amt"], "₹ p.m."),
            _row("Debt (incl. EPF / PPF)", r["alloc_debt_amt"], "₹ p.m."),
            _row("Gold", r["alloc_gold_amt"], "₹ p.m."),
        ]},
        {"title": "Feasibility Check", "rows": [
            _row("Monthly Investible Surplus", r["monthly_investible"], "₹ p.m."),
            _row("Committed Now (Critical + High goals + Retirement)",
                 r["committed_now"], "₹ p.m."),
            _row("Surplus / (Deficit)", r["surplus_after_committed"], "₹ p.m.",
                 "Negative means the plan as written is not fundable.", big=True),
            _row("Committed as % of Surplus", r["committed_pct"], "%"),
            _row("Deferred Medium / Low priority goal SIPs",
                 r["goals_sip_deferrable"], "₹ p.m."),
        ]},
    ]


def _kpis(r):
    fundable = r["surplus_after_committed"] >= 0
    return [
        {"label": "Monthly Surplus", "value": fmt(r["monthly_investible"], "₹ p.m."),
         "color": "#2563eb", "sub": f"Savings rate {fmt(r['savings_rate'], '%')}"},
        {"label": "Term Cover to Buy", "value": short(r["term_cover"]),
         "color": "#7c3aed", "sub": f"Premium {fmt(r['term_premium'], '₹ p.a.')}"},
        {"label": "Health Cover to Hold", "value": short(r["health_total"]),
         "color": "#0d9488", "sub": f"Base {short(r['health_base'])} + top-up"},
        {"label": "Retirement Corpus", "value": short(r["corpus_required"]),
         "color": "#d97706", "sub": f"Gap {short(r['corpus_gap'])}"},
        {"label": "Retirement SIP", "value": fmt(r["stepup_sip"], "₹ p.m."),
         "color": "#16a34a", "sub": "Step-up, year 1"},
        {"label": "Plan Feasibility",
         "value": "Fundable" if fundable else "Short",
         "color": "#16a34a" if fundable else "#dc2626",
         "sub": f"{fmt(r['surplus_after_committed'], '₹ p.m.')} left"},
    ]
