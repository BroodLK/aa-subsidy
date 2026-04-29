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
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from django.db.models import Sum

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from corptools.models import CorporateContract, CorporateContractItem
from fittings.models import Fitting

from ..contracts.matching import get_or_match_contract, get_or_match_contracts, match_contract
from ..contracts.pricing import get_fitting_pricing_map
from ..contracts.reviews import reviewer_table
from ..contracts.summaries import doctrine_stock_summary, doctrine_insights
from ..models import (
    CorporateContractSubsidy,
    DoctrineContractDecision,
    DoctrineItemRule,
    DoctrineMatchProfile,
    DoctrineQuantityTolerance,
    DoctrineSubstitutionRule,
    FittingClaim,
    SubsidyConfig,
    UserTablePreference,
)
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


def _main_name_for_issuer(issuer_eve_id: int | None, fallback_name: str, cache: dict[int, str]) -> str:
    if not issuer_eve_id:
        return fallback_name
    if issuer_eve_id in cache:
        return cache[issuer_eve_id]
    try:
        char = (
            EveCharacter.objects.filter(character_id=issuer_eve_id)
            .select_related("character_ownership__user__profile__main_character")
            .only("id")
            .first()
        )
        if not char or not getattr(char, "character_ownership", None):
            cache[issuer_eve_id] = fallback_name
            return fallback_name
        profile = getattr(char.character_ownership.user, "profile", None)
        main = getattr(profile, "main_character", None) if profile else None
        resolved = getattr(main, "character_name", None) or fallback_name
        cache[issuer_eve_id] = resolved
        return resolved
    except Exception:
        cache[issuer_eve_id] = fallback_name
        return fallback_name


def _build_stats_payload(contracts_qs, aggregate_to_main: bool = False):
    rows = []
    totals_by_char: dict[str, int] = {}
    contract_count_by_char: dict[str, int] = {}
    approved_count_by_char: dict[str, int] = {}
    main_name_cache: dict[int, str] = {}

    for c in contracts_qs:
        meta = getattr(c, "aasubsidy_meta", None)
        subsidy_amount = float(getattr(meta, "subsidy_amount", 0) or 0)
        review_status = getattr(meta, "review_status", 0)
        status_label = {1: "Approved", -1: "Rejected"}.get(review_status, "Pending")
        paid = bool(getattr(meta, "paid", False))
        exempt = bool(getattr(meta, "exempt", False))
        issuer = getattr(c.issuer_name, "name", "Unknown")
        issuer_eve_id = getattr(c.issuer_name, "eve_id", None)
        stats_name = (
            _main_name_for_issuer(issuer_eve_id, issuer, main_name_cache)
            if aggregate_to_main
            else issuer
        )
        issuer_main = (
            _main_name_for_issuer(issuer_eve_id, issuer, main_name_cache)
            if aggregate_to_main
            else ""
        )

        rows.append(
            {
                "id": c.contract_id,
                "issuer": issuer,
                "issuer_main": issuer_main,
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

        if stats_name not in totals_by_char:
            totals_by_char[stats_name] = 0
            contract_count_by_char[stats_name] = 0
            approved_count_by_char[stats_name] = 0

        if review_status == 1:
            totals_by_char[stats_name] += int(subsidy_amount or 0)
            approved_count_by_char[stats_name] += 1

        contract_count_by_char[stats_name] += 1

    per_character = []
    for name in sorted(totals_by_char.keys(), key=str.lower):
        approved_total = totals_by_char[name]
        approved_contract_count = approved_count_by_char.get(name, 0)
        if approved_total <= 0:
            continue
        per_character.append(
            {
                "character": name,
                "approved_total": approved_total,
                "contract_count": contract_count_by_char.get(name, 0),
                "approved_contract_count": approved_contract_count,
                "avg_per_approved": (
                    approved_total / approved_contract_count
                    if approved_contract_count > 0
                    else 0
                ),
            }
        )

    per_character_totals = {
        "contract_count": sum(r["contract_count"] for r in per_character),
        "approved_contract_count": sum(r["approved_contract_count"] for r in per_character),
        "approved_total": sum(r["approved_total"] for r in per_character),
    }
    per_character_totals["avg_per_approved"] = (
        per_character_totals["approved_total"] / per_character_totals["approved_contract_count"]
        if per_character_totals["approved_contract_count"] > 0
        else 0
    )

    return per_character, per_character_totals, rows


def get_main_for_character(character: EveCharacter):
    try:
        return character.character_ownership.user.profile.main_character
    except (
        AttributeError,
        EveCharacter.character_ownership.RelatedObjectDoesNotExist,
        CharacterOwnership.user.RelatedObjectDoesNotExist,
    ):
        return None


def _serialize_match_result(result, *, include_items: bool = False) -> dict:
    evidence = result.evidence or {}
    payload = {
        "selected_fit_id": result.matched_fitting_id or evidence.get("selected_fit_id"),
        "selected_fit_name": result.matched_fitting_name or evidence.get("selected_fit_name"),
        "match_source": result.match_source,
        "match_status": result.match_status,
        "score": float(result.score or 0),
        "warning_count": len(result.warnings or []),
        "hard_failure_count": len(result.hard_failures or []),
        "warnings": result.warnings or [],
        "hard_failures": result.hard_failures or [],
        "candidates": evidence.get("candidates", []),
        "pricing": evidence.get("pricing") or {},
    }
    if include_items:
        payload["items"] = evidence.get("item_rows", [])
    return payload


def _serialize_review_row(contract: CorporateContract, result, pricing_fallback: dict[int, dict[str, object]] | None = None) -> dict:
    meta = getattr(contract, "aasubsidy_meta", None)
    evidence = (result.evidence or {}) if result else {}
    pricing = dict(evidence.get("pricing") or {})
    selected_fit_id = result.matched_fitting_id if result else None
    pricing_fit_id = selected_fit_id or evidence.get("selected_fit_id")
    if pricing_fit_id and pricing_fallback and (not pricing.get("basis_isk") and not pricing.get("suggested_subsidy")):
        fit_info = pricing_fallback.get(int(pricing_fit_id))
        if fit_info:
            pricing = {
                "fit_id": int(pricing_fit_id),
                "basis_isk": float(fit_info["basis_total"] or 0),
                "total_volume_m3": float(fit_info["total_vol"] or 0),
                "suggested_subsidy": float(fit_info["suggested"] or 0),
            }

    basis_isk = float(pricing.get("basis_isk") or 0)
    suggested_subsidy = float(pricing.get("suggested_subsidy") or 0)
    stored_subsidy_amount = float(getattr(meta, "subsidy_amount", 0) or 0)
    subsidy_amount = stored_subsidy_amount or suggested_subsidy
    price_listed = float(getattr(contract, "price", 0) or 0)
    pct_jita = round((price_listed / basis_isk) * 100, 2) if basis_isk > 0 and price_listed > 0 else 0.0
    review_status = {1: "Approved", -1: "Rejected"}.get(getattr(meta, "review_status", 0), "Pending")

    payload = _serialize_match_result(result) if result else {
        "selected_fit_id": None,
        "selected_fit_name": "No Match",
        "match_source": "auto",
        "match_status": "rejected",
        "score": 0.0,
        "warning_count": 0,
        "hard_failure_count": 0,
        "warnings": [],
        "hard_failures": [],
        "candidates": [],
        "pricing": pricing,
    }
    payload.update(
        {
            "id": int(contract.contract_id),
            "basis_isk": round(basis_isk, 2),
            "suggested_subsidy": round(suggested_subsidy, 2),
            "stored_subsidy_amount": round(stored_subsidy_amount, 2),
            "subsidy_amount": round(subsidy_amount, 2),
            "price_listed": int(price_listed),
            "pct_jita": pct_jita,
            "review_status": review_status,
            "reason": getattr(meta, "reason", "") if meta else "",
            "paid": bool(getattr(meta, "paid", False)),
        }
    )
    return payload

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

        target_user = request.user
        request_user_id = data.get("user_id")
        if request_user_id and (request.user.is_superuser or request.user.has_perm("aasubsidy.subsidy_admin")):
            from django.contrib.auth.models import User
            target_user = get_object_or_404(User, pk=request_user_id)

        deleted, _ = FittingClaim.objects.filter(fitting_id=fit_id, user=target_user).delete()

        if deleted:
            if target_user == request.user:
                messages.success(request, "Your claim was cleared.")
            else:
                messages.success(request, f"Claim for {target_user} was cleared.")
            return JsonResponse({"ok": True, "fit_id": fit_id, "deleted": True})
        else:
            return JsonResponse({"ok": True, "fit_id": fit_id, "deleted": False})

@method_decorator(never_cache, name="dispatch")
class MainView(PermissionRequiredMixin, TemplateView):
    template_name = "contracts/summary.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        end = timezone.now()
        start = end - timedelta(days=3000)

        systems = doctrine_stock_summary(
            start, end, request_user_id=self.request.user.id if self.request.user.is_authenticated else None
        )
        
        ctx["systems"] = systems
        ctx["is_admin"] = self.request.user.is_superuser or self.request.user.has_perm("aasubsidy.subsidy_admin")
        ctx["overall_totals"] = {
            "requested": sum(s["totals"]["requested"] for s in systems),
            "available": sum(s["totals"]["available"] for s in systems),
            "needed": sum(s["totals"]["needed"] for s in systems),
        }

        if self.request.user.is_authenticated:
            pref = UserTablePreference.objects.filter(
                user=self.request.user, table_key="summary"
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
        cfg = SubsidyConfig.active()
        contracts_qs = (
            CorporateContract.objects.filter(
                issuer_name__eve_id__in=char_eve_ids,
                corporation_id=cfg.corporation_id)
            .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
            .order_by("-date_issued")
        )

        per_character, per_character_totals, rows = _build_stats_payload(contracts_qs)
        ctx["per_character"] = per_character
        ctx["per_character_totals"] = per_character_totals
        ctx["contracts"] = rows
        ctx["is_global_stats"] = False
        return ctx


class GlobalStatsView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "contracts/user_stats.html"
    permission_required = "aasubsidy.subsidy_admin"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = SubsidyConfig.active()
        contracts_qs = (
            CorporateContract.objects.filter(corporation_id=cfg.corporation_id)
            .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
            .order_by("-date_issued")
        )

        per_character, per_character_totals, rows = _build_stats_payload(
            contracts_qs, aggregate_to_main=True
        )
        ctx["per_character"] = per_character
        ctx["per_character_totals"] = per_character_totals
        ctx["contracts"] = rows
        ctx["is_global_stats"] = True
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
        cfg = SubsidyConfig.active()
        ctx["contracts"] = reviewer_table(start, end, corporation_id=cfg.corporation_id)
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


class ReviewSummariesView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    def get(self, request):
        raw_ids = (request.GET.get("contract_ids") or "").strip()
        try:
            public_contract_ids = [int(value) for value in raw_ids.split(",") if value.strip()]
        except ValueError:
            return JsonResponse({"ok": False, "error": "invalid_contract_ids"}, status=400)

        if not public_contract_ids:
            return JsonResponse({"ok": True, "rows": []})

        cfg = SubsidyConfig.active()
        contracts = list(
            CorporateContract.objects.filter(
                contract_id__in=public_contract_ids,
                corporation_id=cfg.corporation_id,
            )
            .select_related("aasubsidy_meta")
            .order_by("-date_issued")
        )

        # Calculate and persist doctrine matches for all contracts
        contract_pks = [contract.pk for contract in contracts]
        results = get_or_match_contracts(contract_pks, persist=True)

        # Ensure all contracts have match results
        missing_pks = [pk for pk in contract_pks if pk not in results]
        if missing_pks:
            from .matching import match_contracts
            additional_results = match_contracts(missing_pks, persist=True)
            results.update(additional_results)

        pricing_fit_ids = {
            int(result.matched_fitting_id or (result.evidence or {}).get("selected_fit_id") or 0)
            for result in results.values()
            if result.matched_fitting_id or (result.evidence or {}).get("selected_fit_id")
        }
        pricing_fallback = get_fitting_pricing_map(pricing_fit_ids)

        rows = [
            _serialize_review_row(contract, results.get(contract.pk), pricing_fallback)
            for contract in contracts
        ]

        return JsonResponse({"ok": True, "rows": rows})


@method_decorator(csrf_exempt, name="dispatch")
class ApproveView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = (
                CorporateContract.objects.select_for_update()
                .only("id", "contract_id")
                .get(contract_id=contract_id, corporation_id=cfg.corporation_id)
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
        cfg = SubsidyConfig.active()
        try:
            cc = (
                CorporateContract.objects.select_for_update()
                .only("id", "contract_id")
                .get(contract_id=contract_id, corporation_id=cfg.corporation_id)
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

        table_key = str(payload.get("table_key") or "contracts")[:100]

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
            user=request.user, table_key=table_key
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


class DoctrineInsightsView(PermissionRequiredMixin, TemplateView):
    template_name = "contracts/insights.html"
    permission_required = "aasubsidy.basic_access"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = SubsidyConfig.active()
        ctx["insights"] = doctrine_insights(corporation_id=cfg.corporation_id)
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
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.select_for_update().only("id", "contract_id").get(
                contract_id=contract_id, corporation_id=cfg.corporation_id
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(contract_id=cc.pk)

        if not fit_id_raw or fit_id_raw == "__clear__":
            meta.forced_fitting = None
        else:
            try:
                fit_id = int(fit_id_raw)
                meta.forced_fitting = get_object_or_404(Fitting, pk=fit_id)
            except Exception:
                return JsonResponse({"ok": False, "error": "invalid_fit"}, status=400)

        meta.save(update_fields=["forced_fitting"])
        result = match_contract(cc.pk, persist=True)
        return JsonResponse({"ok": True, "match": _serialize_match_result(result)})


@method_decorator(csrf_exempt, name="dispatch")
class MatchPreviewView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    def get(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.only("id", "contract_id").get(
                contract_id=contract_id,
                corporation_id=cfg.corporation_id,
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        # IMPORTANT: Use refresh=False to respect forced matches
        result = get_or_match_contract(cc.pk, persist=True, refresh=False)
        return JsonResponse({"ok": True, "match": _serialize_match_result(result, include_items=True)})


@method_decorator(csrf_exempt, name="dispatch")
class AcceptOnceView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.select_for_update().only("id", "contract_id").get(
                contract_id=contract_id,
                corporation_id=cfg.corporation_id,
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        meta, _ = CorporateContractSubsidy.objects.select_for_update().get_or_create(contract_id=cc.pk)
        fit_id_raw = (request.POST.get("fit_id") or "").strip()
        fit_id = int(fit_id_raw) if fit_id_raw.isdigit() else getattr(meta, "forced_fitting_id", None)
        if not fit_id:
            preview = get_or_match_contract(cc.pk, persist=True)
            fit_id = preview.matched_fitting_id or preview.evidence.get("selected_fit_id")
        if not fit_id:
            return JsonResponse({"ok": False, "error": "fit_required"}, status=400)

        fit = get_object_or_404(Fitting, pk=fit_id)
        summary = (request.POST.get("summary") or request.POST.get("reason") or "").strip()

        DoctrineContractDecision.objects.create(
            contract_id=cc.pk,
            fitting=fit,
            decision=DoctrineContractDecision.DECISION_ACCEPT_ONCE,
            summary=summary or "Accepted once during review.",
            details_json="{}",
            created_by=request.user,
        )
        result = match_contract(cc.pk, persist=True)
        messages.success(request, f"Accepted {fit.name} for this contract once.")
        return JsonResponse({"ok": True, "match": _serialize_match_result(result)})


@method_decorator(csrf_exempt, name="dispatch")
class UndoAcceptOnceView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.select_for_update().only("id", "contract_id").get(
                contract_id=contract_id,
                corporation_id=cfg.corporation_id,
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        # Delete the most recent accept_once decision
        deleted_count, _ = DoctrineContractDecision.objects.filter(
            contract_id=cc.pk,
            decision=DoctrineContractDecision.DECISION_ACCEPT_ONCE,
        ).order_by("-created_at", "-id")[:1].delete()

        if deleted_count == 0:
            return JsonResponse({"ok": False, "error": "no_decision_found"}, status=404)

        result = match_contract(cc.pk, persist=True)
        messages.success(request, "Undone accept once decision.")
        return JsonResponse({"ok": True, "match": _serialize_match_result(result)})


@method_decorator(csrf_exempt, name="dispatch")
class CreateRuleView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    @transaction.atomic
    def post(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.select_for_update().only("id", "contract_id").get(
                contract_id=contract_id,
                corporation_id=cfg.corporation_id,
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        fit_id_raw = (request.POST.get("fit_id") or "").strip()
        if not fit_id_raw.isdigit():
            return JsonResponse({"ok": False, "error": "fit_required"}, status=400)
        fit = get_object_or_404(Fitting, pk=int(fit_id_raw))
        profile, _ = DoctrineMatchProfile.objects.get_or_create(fitting=fit)

        action = (request.POST.get("action_name") or "").strip()
        expected_type_raw = (request.POST.get("expected_type_id") or "").strip()
        actual_type_raw = (request.POST.get("actual_type_id") or "").strip()
        expected_qty_raw = (request.POST.get("expected_qty") or "").strip()
        actual_qty_raw = (request.POST.get("actual_qty") or "").strip()
        category = (request.POST.get("category") or "module").strip()[:32]

        try:
            expected_type_id = int(expected_type_raw) if expected_type_raw else None
        except ValueError:
            expected_type_id = None
        try:
            actual_type_id = int(actual_type_raw) if actual_type_raw else None
        except ValueError:
            actual_type_id = None
        try:
            expected_qty = int(expected_qty_raw) if expected_qty_raw else 0
        except ValueError:
            expected_qty = 0
        try:
            actual_qty = int(actual_qty_raw) if actual_qty_raw else 0
        except ValueError:
            actual_qty = 0

        if action == "optional_item":
            if not expected_type_id:
                return JsonResponse({"ok": False, "error": "expected_type_required"}, status=400)
            rule, _ = DoctrineItemRule.objects.get_or_create(
                profile=profile,
                eve_type_id=expected_type_id,
                defaults={
                    "rule_kind": DoctrineItemRule.RULE_OPTIONAL,
                    "quantity_mode": DoctrineItemRule.QTY_EXACT,
                    "expected_quantity": max(expected_qty, 1),
                    "category": category,
                },
            )
            rule.rule_kind = DoctrineItemRule.RULE_OPTIONAL
            if expected_qty > 0:
                rule.expected_quantity = expected_qty
            if category:
                rule.category = category
            rule.save(update_fields=["rule_kind", "expected_quantity", "category"])
        elif action == "specific_substitute":
            if not expected_type_id or not actual_type_id:
                return JsonResponse({"ok": False, "error": "substitute_types_required"}, status=400)
            DoctrineSubstitutionRule.objects.get_or_create(
                profile=profile,
                expected_type_id=expected_type_id,
                allowed_type_id=actual_type_id,
                defaults={
                    "rule_type": DoctrineSubstitutionRule.RULE_SPECIFIC,
                    "penalty_points": Decimal("2.00"),
                },
            )
        elif action == "quantity_tolerance":
            if not expected_type_id or expected_qty <= 0:
                return JsonResponse({"ok": False, "error": "quantity_context_required"}, status=400)
            diff = actual_qty - expected_qty
            mode = DoctrineQuantityTolerance.MODE_ABSOLUTE
            lower_bound = diff if diff < 0 else 0
            upper_bound = diff if diff > 0 else 0
            if diff < 0:
                mode = DoctrineQuantityTolerance.MODE_MISSING_ONLY
                lower_bound = 0
                upper_bound = abs(diff)
            elif diff > 0:
                mode = DoctrineQuantityTolerance.MODE_EXTRA_ONLY
                lower_bound = 0
                upper_bound = diff
            tolerance, _ = DoctrineQuantityTolerance.objects.get_or_create(
                profile=profile,
                eve_type_id=expected_type_id,
                mode=mode,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                defaults={"penalty_points": Decimal("1.00")},
            )
            tolerance.penalty_points = tolerance.penalty_points or Decimal("1.00")
            tolerance.save(update_fields=["penalty_points"])
        elif action == "ignore_extra_item":
            target_type_id = actual_type_id or expected_type_id
            if not target_type_id:
                return JsonResponse({"ok": False, "error": "type_required"}, status=400)
            DoctrineItemRule.objects.get_or_create(
                profile=profile,
                eve_type_id=target_type_id,
                defaults={
                    "rule_kind": DoctrineItemRule.RULE_IGNORE,
                    "quantity_mode": DoctrineItemRule.QTY_EXACT,
                    "expected_quantity": 0,
                    "category": category or "cargo",
                },
            )
        else:
            return JsonResponse({"ok": False, "error": "unsupported_action"}, status=400)

        DoctrineContractDecision.objects.create(
            contract_id=cc.pk,
            fitting=fit,
            decision=DoctrineContractDecision.DECISION_CREATE_RULE,
            summary=f"Created rule via review: {action}",
            details_json="{}",
            created_by=request.user,
        )
        result = match_contract(cc.pk, persist=True)
        return JsonResponse({"ok": True, "match": _serialize_match_result(result)})


class ContractItemsView(PermissionRequiredMixin, View):
    permission_required = "aasubsidy.review_subsidy"

    def get(self, request, contract_id: int):
        cfg = SubsidyConfig.active()
        try:
            cc = CorporateContract.objects.select_related("aasubsidy_meta__forced_fitting").get(
                contract_id=contract_id, corporation_id=cfg.corporation_id
            )
        except CorporateContract.DoesNotExist:
            return JsonResponse({"ok": False, "error": "not_found"}, status=404)

        # IMPORTANT: Use refresh=False to respect forced matches
        result = get_or_match_contract(cc.pk, persist=True, refresh=False)
        analysis = _serialize_match_result(result, include_items=True)
        items = []
        for row in analysis.get("items", []):
            rendered = dict(row)
            included_qty = int(rendered.get("included_qty") or 0)
            excluded_qty = int(rendered.get("excluded_qty") or 0)
            if included_qty > 0:
                rendered["is_included"] = True
            elif excluded_qty > 0:
                rendered["is_included"] = False
            else:
                rendered["is_included"] = None
            rendered["qty"] = int(rendered.get("qty") or 0)
            items.append(rendered)

        if not items:
            raw_items = (
                CorporateContractItem.objects.filter(contract_id=cc.pk)
                .values("type_name_id", "type_name__name", "is_included")
                .annotate(total_qty=Sum("quantity"))
                .order_by("-is_included", "type_name__name")
            )
            items = [
                {
                    "name": item["type_name__name"] or str(item["type_name_id"]),
                    "type_id": int(item["type_name_id"]),
                    "qty": int(item["total_qty"] or 0),
                    "included_qty": int(item["total_qty"] or 0) if item["is_included"] else 0,
                    "excluded_qty": int(item["total_qty"] or 0) if not item["is_included"] else 0,
                    "is_included": item["is_included"],
                    "status": "ok",
                    "reason": "",
                    "actions": [],
                }
                for item in raw_items
            ]

        analysis["can_accept_once"] = bool(
            analysis.get("selected_fit_id") and analysis.get("match_status") != "matched"
        )

        # Check if we can undo an accept_once decision
        decision = analysis.get("evidence", {}).get("decision", {})
        analysis["can_undo_accept_once"] = decision.get("decision") == "accept_once"

        return JsonResponse({"ok": True, "items": items, "analysis": analysis})
