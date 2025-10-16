from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import List

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from corptools.models import CorporateContract
from fittings.models import Fitting

from ..contracts.reviews import reviewer_table
from ..contracts.summaries import doctrine_stock_summary
from ..models import CorporateContractSubsidy, FittingClaim, UserTablePreference
from .payments import aggregate_payments_to_main, mark_all_unpaid_for_main_as_paid


def _all_character_ids_for_user(user) -> List[int]:
    try:
        return list(
            EveCharacter.objects.filter(
                character_ownership__user=user
            ).values_list("character_id", flat=True)
        )
    except Exception:
        return []


def get_main_for_character(character: EveCharacter):
    try:
        return character.character_ownership.user.profile.main_character
    except (
        AttributeError,
        EveCharacter.character_ownership.RelatedObjectDoesNotExist,
        CharacterOwnership.user.RelatedObjectDoesNotExist,
    ):
        return None

@method_decorator(csrf_exempt, name="dispatch")
class DeleteClaimView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.basic_access"

    def post(self, request):
        try:
            import json
            data = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

        try:
            fit_id = int(data.get("fit_id") or 0)
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid_params"}, status=400)

        if fit_id <= 0:
            return JsonResponse({"ok": False, "error": "invalid_params"}, status=400)

        deleted, _ = FittingClaim.objects.filter(fitting_id=fit_id, user=request.user).delete()

        if deleted:
            messages.success(request, "Your claim was cleared.")
            return JsonResponse({"ok": True, "fit_id": fit_id, "deleted": True})
        else:
            return JsonResponse({"ok": True, "fit_id": fit_id, "deleted": False})

class MainView(PermissionRequiredMixin, TemplateView):
    template_name = "contracts/summary.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        end = timezone.now()
        start = end - timedelta(days=3000)

        try:
            from ..contracts import summaries as summaries_mod
            summaries_mod.doctrine_stock_summary.request_user_id = (
                self.request.user.id if self.request.user.is_authenticated else None
            )
        except Exception:
            pass

        rows = doctrine_stock_summary(start, end)
        total_requested = sum(int(r.get("stock_requested", 0) or 0) for r in rows)
        total_available = sum(int(r.get("stock_available", 0) or 0) for r in rows)
        total_needed = sum(int(r.get("stock_needed", 0) or 0) for r in rows)

        ctx["rows"] = rows
        ctx["totals"] = {
            "requested": total_requested,
            "available": total_available,
            "needed": total_needed,
        }

        if self.request.user.is_authenticated:
            pref = UserTablePreference.objects.filter(
                user=self.request.user, table_key="contracts"
            ).first()
            if pref:
                ctx["table_pref"] = {
                    "sort_idx": pref.sort_idx,
                    "sort_dir": pref.sort_dir,
                    "filters": pref.filters_json,
                }

        return ctx


class UserStatsView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "contracts/user_stats.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        char_eve_ids = _all_character_ids_for_user(self.request.user)
        contracts_qs = (
            CorporateContract.objects.filter(
                issuer_name__eve_id__in=char_eve_ids,
                corporation_id=1)
            .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
            .order_by("-date_issued")
        )

        rows = []
        totals_by_char: dict[str, int] = {}
        contract_count_by_char: dict[str, int] = {}

        for c in contracts_qs:
            meta = getattr(c, "aasubsidy_meta", None)
            subsidy_amount = float(getattr(meta, "subsidy_amount", 0) or 0)
            review_status = getattr(meta, "review_status", 0)
            status_label = {1: "Approved", -1: "Rejected"}.get(review_status, "Pending")
            paid = bool(getattr(meta, "paid", False))
            exempt = bool(getattr(meta, "exempt", False))
            issuer = getattr(c.issuer_name, "name", "Unknown")

            rows.append(
                {
                    "id": c.contract_id,
                    "issuer": issuer,
                    "date_issued": c.date_issued,
                    "price_listed": int(c.price or 0),
                    "status": c.status,
                    "title": c.title or "",
                    "station": getattr(c.start_location_name, "location_name", "") or "",
                    "review_status": status_label,
                    "subsidy_amount": subsidy_amount,
                    "paid": paid,
                    "exempt": exempt,
                    "reason": getattr(meta, "reason", "") if meta else "",
                }
            )

            if issuer not in totals_by_char:
                totals_by_char[issuer] = 0
                contract_count_by_char[issuer] = 0

            if review_status == 1:
                totals_by_char[issuer] += int(subsidy_amount or 0)

            contract_count_by_char[issuer] += 1

        per_character = []
        for name in sorted(totals_by_char.keys(), key=str.lower):
            per_character.append(
                {
                    "character": name,
                    "approved_total": totals_by_char[name],
                    "contract_count": contract_count_by_char.get(name, 0),
                }
            )

        ctx["per_character"] = per_character
        ctx["contracts"] = rows
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

        try:
            fit_id = int(data.get("fit_id") or 0)
            qty = int(data.get("quantity") or 0)
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid_params"}, status=400)

        if fit_id <= 0 or qty <= 0:
            return JsonResponse({"ok": False, "error": "invalid_params"}, status=400)

        fit = get_object_or_404(Fitting, pk=fit_id)

        try:
            first_char = EveCharacter.objects.filter(
                character_ownership__user=request.user
            ).first()
            if first_char:
                _ = get_main_for_character(first_char)
        except Exception:
            pass

        claim, created = FittingClaim.objects.get_or_create(
            fitting=fit, user=request.user, defaults={"quantity": qty}
        )
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
        ctx["all_fits"] = Fitting.objects.only("id", "name").order_by("name")
        if self.request.user.is_authenticated:
            pref = UserTablePreference.objects.filter(
                user=self.request.user, table_key="contracts"
            ).first()
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
        try:
            cc = (
                CorporateContract.objects.select_for_update()
                .only("id", "contract_id")
                .get(contract_id=contract_id, corporation_id=1)
            )
        except CorporateContract.DoesNotExist:
            messages.error(request, "Contract not found.")
            raise Http404

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(
            contract_id=cc.pk
        )

        reason = (
            request.POST.get("comment")
            or request.POST.get("reason")
            or meta.reason
        )

        subsidy_raw = request.POST.get("subsidy_amount")
        if subsidy_raw is not None:
            try:
                meta.subsidy_amount = Decimal(subsidy_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid subsidy amount.")
                return JsonResponse(
                    {"ok": False, "error": "invalid_subsidy_amount"}, status=400
                )

        meta.review_status = 1
        meta.reason = reason
        meta.save(update_fields=["review_status", "reason", "subsidy_amount"])

        messages.success(request, "Contract approved.")
        return JsonResponse(
            {
                "ok": True,
                "review_status": "Approved",
                "subsidy_amount": str(meta.subsidy_amount or "0"),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class DenyView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        try:
            cc = (
                CorporateContract.objects.select_for_update()
                .only("id", "contract_id")
                .get(contract_id=contract_id, corporation_id=1)
            )
        except CorporateContract.DoesNotExist:
            messages.error(request, "Contract not found.")
            raise Http404

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(
            contract_id=cc.pk
        )

        reason = (request.POST.get("comment") or request.POST.get("reason") or "").strip()
        if not reason:
            return JsonResponse(
                {"ok": False, "error": "reason_required"}, status=400
            )

        subsidy_raw = request.POST.get("subsidy_amount")
        if subsidy_raw is not None:
            try:
                meta.subsidy_amount = Decimal(subsidy_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Invalid subsidy amount.")
                return JsonResponse(
                    {"ok": False, "error": "invalid_subsidy_amount"}, status=400
                )

        meta.review_status = -1
        meta.reason = reason
        meta.save(update_fields=["review_status", "reason", "subsidy_amount"])

        messages.success(request, "Contract denied.")
        return JsonResponse(
            {
                "ok": True,
                "review_status": "Rejected",
                "subsidy_amount": str(meta.subsidy_amount or "0"),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class SaveTablePreferenceView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.basic_access"

    def post(self, request):
        try:
            import json

            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            payload = {}

        try:
            sort_idx = int(payload.get("sort_idx") or 0)
        except Exception:
            sort_idx = 0

        sort_dir = str(payload.get("sort_dir") or "desc")[:4]
        filters_json_obj = payload.get("filters") or {}

        try:
            import json as _json

            filters_str = _json.dumps(filters_json_obj)
        except Exception:
            filters_str = "{}"

        pref, _ = UserTablePreference.objects.get_or_create(
            user=request.user, table_key="contracts"
        )
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
            return JsonResponse(
                {"ok": False, "error": "missing_character"}, status=400
            )

        count = mark_all_unpaid_for_main_as_paid(name)

        if count > 0:
            messages.success(
                request, f"Marked {count} approved subsidies as paid for {name}."
            )
        else:
            messages.info(
                request, f"No unpaid approved subsidies found for {name}."
            )

        return JsonResponse({"ok": True, "updated": count})


@method_decorator(csrf_exempt, name="dispatch")
class ForceFitView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        fit_id_raw = request.POST.get("fit_id", "").strip()
        try:
            cc = CorporateContract.objects.select_for_update().only("id", "contract_id").get(
                contract_id=contract_id, corporation_id=1
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(contract_id=cc.pk)

        if not fit_id_raw:
            meta.forced_fitting = None
        else:
            try:
                fit_id = int(fit_id_raw)
                meta.forced_fitting = get_object_or_404(Fitting, pk=fit_id)
            except Exception:
                return JsonResponse({"ok": False, "error": "invalid_fit"}, status=400)

        meta.save(update_fields=["forced_fitting"])
        return JsonResponse({"ok": True})