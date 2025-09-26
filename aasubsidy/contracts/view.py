from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.views.generic import TemplateView
from django.views import View
from django.http import JsonResponse, Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.contrib import messages
from decimal import Decimal, InvalidOperation
from django.shortcuts import get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from ..contracts.summaries import doctrine_stock_summary
from ..contracts.reviews import reviewer_table
from ..models import CorporateContractSubsidy, UserTablePreference
from ..models import FittingClaim
from fittings.models import Fitting
from allianceauth.eveonline.models import EveCharacter
from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CorporateContract
from .payments import aggregate_payments_to_main, mark_all_unpaid_for_main_as_paid

def get_main_for_character(character: EveCharacter):
    try:
        return character.character_ownership.user.profile.main_character
    except (
        AttributeError,
        EveCharacter.character_ownership.RelatedObjectDoesNotExist,
        CharacterOwnership.user.RelatedObjectDoesNotExist,
    ):
        return None

class MainView(PermissionRequiredMixin, TemplateView): 


    template_name = "contracts/summary.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        end = timezone.now()
        start = end - timedelta(days=3000)
        from ..contracts import summaries as summaries_mod
        summaries_mod.doctrine_stock_summary.request_user_id = self.request.user.id if self.request.user.is_authenticated else None
        rows = doctrine_stock_summary(start, end)
        total_requested = sum(r.get("stock_requested", 0) for r in rows)
        total_available = sum(r.get("stock_available", 0) for r in rows)
        total_needed = sum(r.get("stock_needed", 0) for r in rows)
        ctx["rows"] = rows
        ctx["totals"] = {
            "requested": total_requested,
            "available": total_available,
            "needed": total_needed,
        }

        if self.request.user.is_authenticated:
            pref = UserTablePreference.objects.filter(user=self.request.user, table_key="contracts").first()
            if pref:
                ctx["table_pref"] = {
                    "sort_idx": pref.sort_idx,
                    "sort_dir": pref.sort_dir,
                    "filters": pref.filters_json,
                }
        return ctx

@method_decorator(csrf_exempt, name="dispatch")
class SaveClaimView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.basic_access"

    def post(self, request):
        try:
            import json
            data = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

        fit_id = int(data.get("fit_id") or 0)
        qty = int(data.get("quantity") or 0)
        if fit_id <= 0 or qty <= 0:
            return JsonResponse({"ok": False, "error": "invalid_params"}, status=400)

        fit = get_object_or_404(Fitting, pk=fit_id)

        main_char = None
        try:
            first_char = EveCharacter.objects.filter(character_ownership__user=request.user).first()
            if first_char:
                main_char = get_main_for_character(first_char)
        except Exception:
            main_char = None

        claim, created = FittingClaim.objects.get_or_create(fitting=fit, user=request.user, defaults={"quantity": qty})
        if not created:
            claim.quantity = qty
            claim.save(update_fields=["quantity"])
            messages.success(request, "Claim updated.")
        else:
            messages.success(request, "Claim made.")

        return JsonResponse({"ok": True, "fit_id": fit_id, "quantity": claim.quantity})

class ReviewerView(PermissionRequiredMixin, TemplateView):
    template_name = "contracts/review.html"
    permission_required = "aasubsidy.review_subsidy"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        end = timezone.now()
        start = end - timedelta(days=30)
        ctx["contracts"] = reviewer_table(start, end, corporation_id=1)
        if self.request.user.is_authenticated:
            pref = UserTablePreference.objects.filter(user=self.request.user, table_key="contracts").first()
            if pref:
                ctx["table_pref"] = {
                    "sort_idx": pref.sort_idx,
                    "sort_dir": pref.sort_dir,
                    "filters": pref.filters_json,
                }
        return ctx

@method_decorator(csrf_exempt, name="dispatch")
class ApproveView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        from corptools.models import CorporateContract
        try:
            cc = CorporateContract.objects.select_for_update().get(contract_id=contract_id, corporation_id=1)
        except CorporateContract.DoesNotExist:
            messages.error(request, "Contract not found.")
            raise Http404

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(contract_id=cc.pk)

        reason = request.POST.get("comment") or request.POST.get("reason") or meta.reason
        subsidy_raw = request.POST.get("subsidy_amount")
        if subsidy_raw is not None:
            try:
                meta.subsidy_amount = Decimal(subsidy_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid subsidy amount.")
                return JsonResponse({"ok": False, "error": "invalid subsidy_amount"}, status=400)

        meta.review_status = 1
        meta.reason = reason
        meta.save(update_fields=["review_status", "reason", "subsidy_amount"])
        messages.success(request, "Contract approved.")
        return JsonResponse({"ok": True, "review_status": "Approved", "subsidy_amount": str(meta.subsidy_amount)})

@method_decorator(csrf_exempt, name="dispatch")
class DenyView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        from corptools.models import CorporateContract
        try:
            cc = CorporateContract.objects.select_for_update().get(contract_id=contract_id, corporation_id=1)
        except CorporateContract.DoesNotExist:
            messages.error(request, "Contract not found.")
            raise Http404

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(contract_id=cc.pk)

        reason = request.POST.get("comment") or request.POST.get("reason") or ""
        if not reason:
            return JsonResponse({"ok": False, "error": "reason_required"}, status=400)

        subsidy_raw = request.POST.get("subsidy_amount")
        if subsidy_raw is not None:
            try:
                meta.subsidy_amount = Decimal(subsidy_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid subsidy amount.")
                return JsonResponse({"ok": False, "error": "invalid subsidy_amount"}, status=400)

        meta.review_status = -1
        meta.reason = reason
        meta.save(update_fields=["review_status", "reason", "subsidy_amount"])
        messages.success(request, "Contract denied.")
        return JsonResponse({"ok": True, "review_status": "Rejected", "subsidy_amount": str(meta.subsidy_amount)})

@method_decorator(csrf_exempt, name="dispatch")
class SaveTablePreferenceView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.basic_access"

    def post(self, request):
        try:
            import json
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        sort_idx = int(payload.get("sort_idx") or 0)
        sort_dir = str(payload.get("sort_dir") or "desc")[:4]
        filters_json = payload.get("filters") or {}
        try:
            import json as _json
            filters_str = _json.dumps(filters_json)
        except Exception:
            filters_str = "{}"
        pref, _ = UserTablePreference.objects.get_or_create(user=request.user, table_key="contracts")
        pref.sort_idx = sort_idx
        pref.sort_dir = sort_dir
        pref.filters_json = filters_str
        pref.save(update_fields=["sort_idx", "sort_dir", "filters_json"])
        return JsonResponse({"ok": True})

class PaymentsView(PermissionRequiredMixin, TemplateView):
    template_name = "contracts/payments.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rows, totals = aggregate_payments_to_main()
        ctx["rows"] = rows
        ctx["totals"] = totals
        return ctx

@method_decorator(csrf_exempt, name="dispatch")
class MarkPaidView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.subsidy_admin"

    def post(self, request):
        name = (request.POST.get("character") or "").strip()
        if not name:
            messages.error(request, "Missing character name.")
            return JsonResponse({"ok": False, "error": "missing_character"}, status=400)

        count = mark_all_unpaid_for_main_as_paid(name)

        if count > 0:
            messages.success(request, f"Marked {count} approved subsidies as paid for {name}.")
        else:
            messages.info(request, f"No unpaid approved subsidies found for {name}.")

        return JsonResponse({"ok": True, "updated": count})
