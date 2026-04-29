from django.urls import path
from .contracts import view as v
from .contracts.doctrines import DoctrineRequestsAdminView, DoctrineRequestsDetailView, location_search
from .contracts.admin.settings import SubsidySettingsAdminView
from .contracts.admin import rule_exceptions as admin_views

app_name = "aasubsidy"

urlpatterns = [
    path("", v.MainView.as_view(), name="index"),
    path("insights/", v.DoctrineInsightsView.as_view(), name="insights"),
    path("contract/review/", v.ReviewerView.as_view(), name="review"),
    path("contract/review/summaries/", v.ReviewSummariesView.as_view(), name="review_summaries"),
    path("contract/<int:contract_id>/approve/", v.ApproveView.as_view(), name="approve"),
    path("contract/<int:contract_id>/deny/", v.DenyView.as_view(), name="deny"),
    path("contract/review/table-pref/save/", v.SaveTablePreferenceView.as_view(), name="save_table_pref"),
    path("contract/summary/claim/", v.SaveClaimView.as_view(), name="save_claim"),
    path("contract/summary/claim/delete/", v.DeleteClaimView.as_view(), name="delete_claim"),
    path("contract/payments/", v.PaymentsView.as_view(), name="payments"),
    path("contract/payments/mark-paid/", v.MarkPaidView.as_view(), name="payments_mark_paid"),
    path("contract/user/stats/", v.UserStatsView.as_view(), name="user_stats"),
    path("contract/stats/", v.GlobalStatsView.as_view(), name="all_stats"),
    path("contract/<int:contract_id>/force-fit/", v.ForceFitView.as_view(), name="force_fit"),
    path("contract/<int:contract_id>/match-preview/", v.MatchPreviewView.as_view(), name="match_preview"),
    path("contract/<int:contract_id>/accept-once/", v.AcceptOnceView.as_view(), name="accept_once"),
    path("contract/<int:contract_id>/undo-accept-once/", v.UndoAcceptOnceView.as_view(), name="undo_accept_once"),
    path("contract/<int:contract_id>/create-rule/", v.CreateRuleView.as_view(), name="create_rule"),
    path("contract/<int:contract_id>/items/", v.ContractItemsView.as_view(), name="contract_items"),
    path("admin/doctrines/", DoctrineRequestsAdminView.as_view(), name="doctrine_admin"),
    path("admin/doctrines/<str:doctrine_name>/", DoctrineRequestsDetailView.as_view(), name="doctrine_detail"),
    path("admin/subsidy-settings/", SubsidySettingsAdminView.as_view(), name="subsidy_settings"),
    path("admin/rule-exceptions/", admin_views.RuleExceptionsView.as_view(), name="rule_exceptions"),
    path("admin/rule-exceptions/delete/", admin_views.DeleteRuleView.as_view(), name="delete_rule"),
    path("api/location-search/", location_search, name="location_search"),
]
