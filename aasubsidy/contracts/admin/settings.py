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
        try:
            cfg.price_basis = "buy" if basis == "buy" else "sell"
            cfg.pct_over_basis = Decimal(pct_raw)
            cfg.cost_per_m3 = Decimal(m3_raw)
            cfg.rounding_increment = int(incr_raw)
            cfg.save(update_fields=["price_basis", "pct_over_basis", "cost_per_m3", "rounding_increment"])
            messages.success(request, "Subsidy settings saved.")
        except (InvalidOperation, ValueError):
            messages.error(request, "Invalid values provided. Please review and try again.")
        return redirect(reverse("aasubsidy:subsidy_settings"))