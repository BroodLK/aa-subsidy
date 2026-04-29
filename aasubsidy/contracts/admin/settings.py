from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.contrib import messages
from decimal import Decimal, InvalidOperation

from aasubsidy.models import SubsidyConfig


class SubsidySettingsAdminView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.subsidy_admin"
    template_name = "admin/settings.html"

    def get(self, request):
        cfg = SubsidyConfig.active()
        return render(request, self.template_name, {"cfg": cfg})

    def post(self, request):
        cfg = SubsidyConfig.active()
        basis = request.POST.get("price_basis") or "sell"
        pct_raw = request.POST.get("pct_over_basis") or "0.10"
        m3_raw = request.POST.get("cost_per_m3") or "250"
        incr_raw = request.POST.get("rounding_increment") or "250000"
        corp_id_raw = request.POST.get("corporation_id") or "1"
        ignore_zero_isk_contracts = request.POST.get("ignore_zero_isk_contracts") == "on"
        ignored_contract_title_patterns = (request.POST.get("ignored_contract_title_patterns") or "").strip()
        close_match_threshold_raw = request.POST.get("close_match_threshold") or "70.00"
        show_close_matches = request.POST.get("show_close_matches") == "on"
        try:
            cfg.price_basis = "buy" if basis == "buy" else "sell"
            cfg.pct_over_basis = Decimal(pct_raw)
            cfg.cost_per_m3 = Decimal(m3_raw)
            cfg.rounding_increment = int(incr_raw)
            cfg.corporation_id = int(corp_id_raw)
            cfg.ignore_zero_isk_contracts = ignore_zero_isk_contracts
            cfg.ignored_contract_title_patterns = ignored_contract_title_patterns
            cfg.close_match_threshold = Decimal(close_match_threshold_raw)
            cfg.show_close_matches = show_close_matches
            cfg.save(
                update_fields=[
                    "price_basis",
                    "pct_over_basis",
                    "cost_per_m3",
                    "rounding_increment",
                    "corporation_id",
                    "ignore_zero_isk_contracts",
                    "ignored_contract_title_patterns",
                    "close_match_threshold",
                    "show_close_matches",
                ]
            )
            messages.success(request, "Subsidy settings saved.")
        except (InvalidOperation, ValueError):
            messages.error(request, "Invalid values provided. Please review and try again.")
        return redirect(reverse("aasubsidy:subsidy_settings"))
