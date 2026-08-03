"""Financial planner — the workbook as a screen, plus its PDF.

Both views hand the raw form values to `services.financial_plan`; neither
trusts a number computed in the browser. The report is therefore always the
same plan the page just showed.
"""
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..services import financial_plan as fp


@login_required
def financial_planner(request):
    data = request.POST if request.method == "POST" else {}
    values = fp.parse(data) if data else dict(fp.defaults(), goals=list(fp.DEFAULT_GOALS))
    plan = fp.compute(values)
    input_groups, calc_groups = fp.form_groups(values)
    return render(request, "sales/financial_planner.html", {
        "plan": plan,
        "values": values,
        "input_groups": input_groups,
        # Calculator inputs and their answers come off the same list, in order.
        "calc_groups": [dict(group, rows=rows) for group, (_title, _key, rows)
                        in zip(calc_groups, plan["calculators"])],
        "priorities": fp.PRIORITIES,
        "active_tab": (data.get("active_tab") or "inputs"),
        "kpis": plan["kpis"],
        "crumbs": [{"label": "Reports", "url": ""},
                   {"label": "Financial Planner", "url": ""}],
    })


def _safe_name(raw):
    return (re.sub(r"[^A-Za-z0-9_-]+", "_", (raw or "Client").strip())[:40] or "Client")


@login_required
@require_POST
def financial_planner_download_report(request):
    """The plan as a client-ready PDF, recomputed from the posted inputs."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    from ..models import FirmSettings

    values = fp.parse(request.POST)
    plan = fp.compute(values)
    firm = FirmSettings.get_settings()
    brand = colors.HexColor(firm.primary_color or "#E5B740")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, textColor=brand,
                        spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11,
                        textColor=colors.HexColor("#1e293b"), spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8.5, leading=11)
    note = ParagraphStyle("note", parent=body, fontSize=7.5,
                          textColor=colors.HexColor("#64748b"))
    centre = ParagraphStyle("centre", parent=body, alignment=TA_CENTER,
                            textColor=colors.HexColor("#64748b"))

    story = [
        Paragraph(firm.firm_name or "Financial Plan", h1),
        Paragraph(f"<b>Financial Plan — {values['client_name'] or 'Client'}</b>", styles["Heading2"]),
        Paragraph(f"{values['plan_year']} &nbsp;·&nbsp; prepared "
                  f"{timezone.localdate().strftime('%d %B %Y')}", note),
        Spacer(1, 0.4 * cm),
    ]

    # The base-14 PDF fonts have no ₹ glyph — it prints as a black box. Rather
    # than bundle a TTF just for one character, the report says Rs.
    def rs(text):
        return str(text).replace("₹", "Rs ")

    def block(title, rows):
        table = Table(
            [[Paragraph(f"<b>{r['label']}</b>" if r["big"] else r["label"], body),
              Paragraph(f"<b>{rs(r['value'])}</b>" if r["big"] else rs(r["value"]), body)]
             for r in rows],
            colWidths=[11.5 * cm, 5 * cm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return KeepTogether([Paragraph(title, h2), table])

    for section in plan["summary"]:
        story.append(block(section["title"], section["rows"]))

    story.append(PageBreak())
    story.append(Paragraph("Goals", h1))
    goal_rows = [["Goal", "Priority", "Yrs", "Cost Today", "Future Cost",
                  "Shortfall", "Monthly SIP"]]
    for row in plan["goals"]["rows"]:
        goal_rows.append([
            Paragraph(row["name"], body), row["priority"], f"{row['years']:.0f}",
            rs(fp.fmt(row["cost"], "₹")), rs(row["cells"][0]),
            rs(row["cells"][2]), rs(row["cells"][3]),
        ])
    table = Table(goal_rows, colWidths=[5.2 * cm, 1.9 * cm, 1.1 * cm, 2.2 * cm,
                                        2.4 * cm, 2.3 * cm, 2.2 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3 * cm))
    story.append(block("Affordability", plan["goals"]["affordability"]))

    story.append(PageBreak())
    story.append(Paragraph("Action Sequence", h1))
    for index, (action, why) in enumerate(plan["actions"], 1):
        story.append(Paragraph(f"<b>{index}. {action}</b>", body))
        story.append(Paragraph(why, note))
        story.append(Spacer(1, 0.15 * cm))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(plan["disclaimer"], note))
    if firm.phone or firm.email:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(" · ".join(x for x in [firm.phone, firm.email,
                                                      firm.website] if x), centre))

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = (
        'attachment; filename="financial_plan_'
        f'{_safe_name(values["client_name"])}_'
        f'{timezone.localdate().strftime("%Y%m%d")}.pdf"')
    SimpleDocTemplate(response, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      leftMargin=1.7 * cm, rightMargin=1.7 * cm,
                      title=f"Financial Plan — {values['client_name'] or 'Client'}",
                      ).build(story)
    return response
