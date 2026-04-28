from django.contrib import admin
from .models import (
    DoctrineContractDecision,
    DoctrineItemRule,
    DoctrineLocation,
    DoctrineMatchProfile,
    DoctrineMatchResult,
    DoctrineQuantityTolerance,
    DoctrineSubstitutionRule,
    DoctrineSystem,
    SubsidyConfig,
)


class SubsidyAdminMixin:
    def has_module_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

@admin.register(SubsidyConfig)
class SubsidyConfigAdmin(SubsidyAdminMixin, admin.ModelAdmin):
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

@admin.register(DoctrineLocation)
class DoctrineLocationAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("system", "location")
    list_filter = ("system",)
    raw_id_fields = ("location",)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


class DoctrineItemRuleInline(admin.TabularInline):
    model = DoctrineItemRule
    extra = 0
    autocomplete_fields = ("eve_type",)


class DoctrineSubstitutionRuleInline(admin.TabularInline):
    model = DoctrineSubstitutionRule
    extra = 0
    autocomplete_fields = ("expected_type", "allowed_type")


class DoctrineQuantityToleranceInline(admin.TabularInline):
    model = DoctrineQuantityTolerance
    extra = 0
    autocomplete_fields = ("eve_type",)


@admin.register(DoctrineMatchProfile)
class DoctrineMatchProfileAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = (
        "fitting",
        "enabled",
        "auto_match_threshold",
        "review_threshold",
        "allow_extra_items",
        "allow_meta_variants",
        "allow_faction_variants",
    )
    list_filter = ("enabled", "allow_extra_items", "allow_meta_variants", "allow_faction_variants")
    search_fields = ("fitting__name",)
    autocomplete_fields = ("fitting",)
    inlines = (DoctrineItemRuleInline, DoctrineSubstitutionRuleInline, DoctrineQuantityToleranceInline)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


@admin.register(DoctrineItemRule)
class DoctrineItemRuleAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("profile", "eve_type", "rule_kind", "quantity_mode", "expected_quantity", "category", "sort_order")
    list_filter = ("rule_kind", "quantity_mode", "category")
    search_fields = ("profile__fitting__name", "eve_type__name")
    autocomplete_fields = ("profile", "eve_type")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


@admin.register(DoctrineSubstitutionRule)
class DoctrineSubstitutionRuleAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("profile", "expected_type", "allowed_type", "rule_type", "penalty_points", "same_slot_only", "same_group_only")
    list_filter = ("rule_type", "same_slot_only", "same_group_only")
    search_fields = ("profile__fitting__name", "expected_type__name", "allowed_type__name")
    autocomplete_fields = ("profile", "expected_type", "allowed_type")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


@admin.register(DoctrineQuantityTolerance)
class DoctrineQuantityToleranceAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("profile", "eve_type", "mode", "lower_bound", "upper_bound", "penalty_points")
    list_filter = ("mode",)
    search_fields = ("profile__fitting__name", "eve_type__name")
    autocomplete_fields = ("profile", "eve_type")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


@admin.register(DoctrineMatchResult)
class DoctrineMatchResultAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("contract", "matched_fitting", "match_source", "match_status", "score", "updated_at")
    list_filter = ("match_source", "match_status", "updated_at")
    search_fields = ("contract__contract_id", "matched_fitting__name")
    autocomplete_fields = ("contract", "matched_fitting")
    readonly_fields = ("updated_at",)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")


@admin.register(DoctrineContractDecision)
class DoctrineContractDecisionAdmin(SubsidyAdminMixin, admin.ModelAdmin):
    list_display = ("contract", "fitting", "decision", "summary", "created_by", "created_at")
    list_filter = ("decision", "created_at")
    search_fields = ("contract__contract_id", "fitting__name", "summary")
    autocomplete_fields = ("contract", "fitting", "created_by")
    readonly_fields = ("created_at",)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_add_permission(self, request):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("aasubsidy.subsidy_admin")
