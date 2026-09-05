"""Sales CRUD/approval, incentive rules, performance, financial planner."""
from django.urls import path

from ..views import planner, sales

urlpatterns = [
    path("sales/add/", sales.add_sale, name="add_sale"),
    path("sales/admin_add/", sales.admin_add_sale, name="admin_add_sale"),
    path("sales/approve/", sales.approve_sales, name="approve_sales"),
    path("sales/<int:sale_id>/edit/", sales.edit_sale, name="edit_sale"),
    path("sales/<int:sale_id>/delete/", sales.delete_sale, name="delete_sale"),
    path("sales/<int:sale_id>/policy-uploaded/", sales.mark_policy_uploaded, name="mark_policy_uploaded"),
    path("sales/recalc/", sales.recalc_points, name="recalc_points"),
    path("sales/all/", sales.all_sales, name="all_sales"),
    path("incentives/calculator/", sales.incentive_calculator, name="incentive_calculator"),
    path("incentives/payout/", sales.incentive_payout, name="incentive_payout"),
    path("incentives/future-points/", sales.future_points, name="future_points"),
    path("incentives/life-bonus/", sales.life_bonus_tracker, name="life_bonus_tracker"),
    path("incentives/bonus-payout/record/", sales.record_bonus_payout, name="record_bonus_payout"),
    path("incentives/", sales.incentive_structure, name="incentive_structure"),
    path("incentives/manage/", sales.manage_incentive_rules, name="manage_incentive_rules"),
    path("incentives/rule/add/", sales.add_incentive_rule, name="add_incentive_rule"),
    path("incentives/rule/<int:rule_id>/update/", sales.update_incentive_rule, name="update_incentive_rule"),
    path("incentives/rule/<int:rule_id>/delete/", sales.delete_incentive_rule, name="delete_incentive_rule"),
    path("incentives/rule/<int:rule_id>/slab/add/", sales.add_incentive_slab, name="add_incentive_slab"),
    path("incentives/slab/<int:slab_id>/update/", sales.update_incentive_slab, name="update_incentive_slab"),
    path("incentives/slab/<int:slab_id>/delete/", sales.delete_incentive_slab, name="delete_incentive_slab"),
    path("sales/financial-planner/", planner.financial_planner, name="financial_planner"),
    path("sales/financial-planner/download-report/", planner.financial_planner_download_report, name="financial_planner_download_report"),
]
