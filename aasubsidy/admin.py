from django.contrib import admin
from .models import SubsidyConfig

@admin.register(SubsidyConfig)
class SubsidyConfigAdmin(admin.ModelAdmin):
    list_display = ("price_basis", "pct_over_basis", "cost_per_m3", "rounding_increment")
    list_editable = ("pct_over_basis", "cost_per_m3", "rounding_increment")
    radio_fields = {"price_basis": admin.HORIZONTAL}
