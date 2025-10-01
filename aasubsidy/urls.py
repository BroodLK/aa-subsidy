from django.urls import path
from .contracts import view as v
from .contracts.doctrines import DoctrineRequestsAdminView, DoctrineRequestsDetailView
from .contracts.admin.settings import SubsidySettingsAdminView

app_name = "aasubsidy"

urlpatterns = [
    path("", v.MainView.as_view(), name="index"),
    path("contract/review/", v.ReviewerView.as_view(), name="review"),
    path("contract/<int:contract_id>/approve/", v.ApproveView.as_view(), name="approve"),
    path("contract/<int:contract_id>/deny/", v.DenyView.as_view(), name="deny"),
    path("contract/review/table-pref/save/", v.SaveTablePreferenceView.as_view(), name="save_table_pref"),
    path("contract/summary/claim/", v.SaveClaimView.as_view(), name="save_claim"),
    path("contract/summary/claim/delete/", v.DeleteClaimView.as_view(), name="delete_claim"),
    path("contract/payments/", v.PaymentsView.as_view(), name="payments"),
    path("contract/payments/mark-paid/", v.MarkPaidView.as_view(), name="payments_mark_paid"),
    path("contract/user/stats/", v.UserStatsView.as_view(), name="user_stats"),
    path("admin/doctrines/", DoctrineRequestsAdminView.as_view(), name="doctrine_admin"),
    path("admin/doctrines/<str:doctrine_name>/", DoctrineRequestsDetailView.as_view(), name="doctrine_detail"),
    path("admin/subsidy-settings/", SubsidySettingsAdminView.as_view(), name="subsidy_settings"),
]
