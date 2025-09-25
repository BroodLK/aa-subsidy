from django.contrib import admin
from django.contrib.auth.models import Permission
from .models import SubsidyConfig

@admin.register(SubsidyConfig)
class SubsidyConfigAdmin(admin.ModelAdmin):
    list_display = ("price_basis", "pct_over_basis", "cost_per_m3", "rounding_increment")
    list_editable = ("pct_over_basis", "cost_per_m3", "rounding_increment")
    radio_fields = {"price_basis": admin.HORIZONTAL}

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")
