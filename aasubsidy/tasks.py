import requests
from celery import shared_task
from django.db import Error, transaction
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.services.hooks import get_extension_logger

from .models import SubsidyConfig, SubsidyItemPrice
from fittings.models import Fitting
from .models import FittingRequest
from corptools.models import CorporateContract
from .models import CorporateContractSubsidy, FittingClaim, FittingClaimAutoClearance
from .contracts.filters import apply_contract_exclusions
from .contracts.matching import match_contracts
from .helpers.contract_import import plan_claim_clearance, resolve_corptools_force_refresh
from .helpers.services_update import update_all_prices
from django.utils import timezone

logger = get_extension_logger(__name__)

try:
    from eveuniverse.models import EveType, EveMarketPrice
except Exception:
    EveType = None
    EveMarketPrice = None


def _is_missing_corporation_audit(exc: Exception) -> bool:
    if not isinstance(exc, ObjectDoesNotExist):
        return False
    exc_type = exc.__class__
    return (
        exc_type.__name__ == "DoesNotExist"
        and "corptools.models.audits" in getattr(exc_type, "__module__", "")
        and "CorporationAudit" in getattr(exc_type, "__qualname__", "")
    )


def _contract_ids_from_refresh_result(result) -> list[int]:
    if isinstance(result, dict):
        for key in ("contract_ids", "contracts", "refreshed_contract_ids"):
            values = result.get(key)
            if values:
                return [int(value) for value in values if value]
        return []
    if isinstance(result, tuple) and len(result) > 1 and result[1]:
        return [int(value) for value in result[1] if value]
    if isinstance(result, list):
        return [int(value) for value in result if value]
    return []


def _force_refresh_corporate_contracts(corporation_id: int, *, force_refresh: bool = True) -> dict:
    try:
        from corptools.tasks import update_corp_contracts
    except Exception as exc:
        try:
            from corptools.tasks.corporation.contracts import corp_contract_update as update_corp_contracts
        except Exception as nested_exc:
            logger.warning(
                "Corporate contract refresh unavailable for corporation %s: %s; fallback failed: %s",
                corporation_id,
                exc,
                nested_exc,
            )
            return {"attempted": False, "ok": False, "error": str(nested_exc)}

    try:
        result = update_corp_contracts(corporation_id, force_refresh=force_refresh)
        refreshed_ids = _contract_ids_from_refresh_result(result)
        logger.info(
            "Triggered corptools contract refresh for corporation %s with force_refresh=%s (%s contract identifiers returned).",
            corporation_id,
            force_refresh,
            len(refreshed_ids),
        )
        return {
            "attempted": True,
            "ok": True,
            "contracts_refreshed": len(refreshed_ids),
            "contract_ids": refreshed_ids,
            "force_refresh": force_refresh,
        }
    except Exception as exc:
        if _is_missing_corporation_audit(exc):
            logger.info(
                "Skipping corptools contract refresh for corporation %s: no CorporationAudit is configured.",
                corporation_id,
            )
            return {
                "attempted": False,
                "ok": False,
                "skipped": True,
                "error": "missing_corporation_audit",
            }
        logger.warning(
            "Corptools contract refresh failed for corporation %s: %s",
            corporation_id,
            exc,
            exc_info=True,
        )
        return {"attempted": True, "ok": False, "error": str(exc)}


def _resolve_corporate_contract_pks(corporation_id: int, identifiers: list[int] | None = None) -> list[int]:
    raw_ids = sorted({int(identifier) for identifier in (identifiers or []) if identifier})
    if not raw_ids:
        return []

    rows = CorporateContract.objects.filter(
        corporation_id=corporation_id,
    ).filter(
        Q(pk__in=raw_ids) | Q(contract_id__in=raw_ids)
    ).values_list("id", flat=True)
    return sorted({int(contract_pk) for contract_pk in rows})


def _auto_clear_claims_for_matched_contracts(matched_results: dict, candidate_contract_pks: set[int]) -> dict:
    eligible_results = {
        int(contract_pk): result
        for contract_pk, result in matched_results.items()
        if int(contract_pk) in candidate_contract_pks
        and getattr(result, "match_status", None) == "matched"
        and getattr(result, "matched_fitting_id", None)
    }
    if not eligible_results:
        return {"checked": 0, "cleared": 0, "skipped": 0}

    already_cleared = set(
        FittingClaimAutoClearance.objects.filter(
            contract_id__in=eligible_results.keys(),
            quantity__gt=0,
        )
        .values_list("contract_id", flat=True)
    )
    contract_rows = list(
        CorporateContract.objects.filter(
            pk__in=[contract_pk for contract_pk in eligible_results.keys() if contract_pk not in already_cleared],
            status__iexact="outstanding",
        )
        .values("id", "contract_id", "issuer_name__eve_id")
    )
    if not contract_rows:
        return {"checked": len(eligible_results), "cleared": 0, "skipped": len(eligible_results)}

    issuer_eve_ids = {
        int(row["issuer_name__eve_id"])
        for row in contract_rows
        if row.get("issuer_name__eve_id")
    }
    user_by_issuer_eve_id = {
        int(character_id): int(user_id)
        for character_id, user_id in CharacterOwnership.objects.filter(
            character__character_id__in=issuer_eve_ids
        ).values_list("character__character_id", "user_id")
    }

    cleared = 0
    skipped = len(already_cleared)
    for row in contract_rows:
        contract_pk = int(row["id"])
        issuer_eve_id = row.get("issuer_name__eve_id")
        user_id = user_by_issuer_eve_id.get(int(issuer_eve_id)) if issuer_eve_id else None
        result = eligible_results.get(contract_pk)
        fitting_id = int(getattr(result, "matched_fitting_id", 0) or 0) if result else 0
        if not user_id or not fitting_id:
            skipped += 1
            continue

        with transaction.atomic():
            clearance = (
                FittingClaimAutoClearance.objects.select_for_update()
                .filter(contract_id=contract_pk)
                .first()
            )

            claim = (
                FittingClaim.objects.select_for_update()
                .filter(user_id=user_id, fitting_id=fitting_id, quantity__gt=0)
                .first()
            )
            plan = plan_claim_clearance(
                getattr(clearance, "quantity", None),
                getattr(claim, "quantity", None),
            )
            if plan["status"] != "clear":
                skipped += 1
                continue

            if plan["delete_claim"]:
                claim.delete()
            else:
                claim.quantity = int(plan["remaining_claim_quantity"])
                claim.save(update_fields=["quantity"])

            if clearance is None:
                FittingClaimAutoClearance.objects.create(
                    contract_id=contract_pk,
                    user_id=user_id,
                    fitting_id=fitting_id,
                    quantity=1,
                )
            else:
                clearance.user_id = user_id
                clearance.fitting_id = fitting_id
                clearance.quantity = 1
                clearance.save(update_fields=["user", "fitting", "quantity"])
            cleared += 1

    checked = len(eligible_results)
    return {"checked": checked, "cleared": cleared, "skipped": max(checked - cleared, skipped)}


def _match_imported_contracts(
    *,
    corporation_id: int,
    created_contract_pks: list[int],
    refreshed_contract_identifiers: list[int] | None = None,
    chunk_size: int = 250,
    auto_clear_claims: bool = True,
) -> dict:
    created_pk_set = {int(contract_pk) for contract_pk in created_contract_pks if contract_pk}
    refreshed_pk_set = set(_resolve_corporate_contract_pks(corporation_id, refreshed_contract_identifiers))
    target_contract_pks = set(created_pk_set)
    target_contract_pks.update(refreshed_pk_set)

    unresolved_contracts = CorporateContract.objects.filter(
        corporation_id=corporation_id,
        aasubsidy_meta__isnull=False,
        doctrine_match__isnull=True,
    ).values_list("id", flat=True)
    target_contract_pks.update(int(contract_pk) for contract_pk in unresolved_contracts)

    cfg = SubsidyConfig.active()
    filtered_contract_pks = list(
        apply_contract_exclusions(
            CorporateContract.objects.filter(
                corporation_id=corporation_id,
                pk__in=target_contract_pks,
            ),
            cfg,
        ).values_list("id", flat=True)
    )

    if not filtered_contract_pks:
        return {
            "matched": 0,
            "created_contract_matches": 0,
            "refreshed_contract_matches": 0,
            "claim_clearance": {"checked": 0, "cleared": 0, "skipped": 0},
        }

    matched = 0
    matched_results = {}
    for index in range(0, len(filtered_contract_pks), max(int(chunk_size or 250), 1)):
        batch = filtered_contract_pks[index : index + max(int(chunk_size or 250), 1)]
        matched_results.update(match_contracts(batch, persist=True))
        matched += len(batch)

    created_count = sum(1 for contract_pk in filtered_contract_pks if contract_pk in created_pk_set)
    refreshed_count = sum(1 for contract_pk in filtered_contract_pks if contract_pk in refreshed_pk_set)
    claim_clearance = {"checked": 0, "cleared": 0, "skipped": 0}
    if auto_clear_claims:
        claim_clearance = _auto_clear_claims_for_matched_contracts(matched_results, set(filtered_contract_pks))
    return {
        "matched": matched,
        "created_contract_matches": created_count,
        "refreshed_contract_matches": refreshed_count,
        "claim_clearance": claim_clearance,
    }


@shared_task(bind=True)
def sync_fitting_requests(self, default_requested: int = 0, chunk_size: int = 1000) -> dict:
    missing_ids = list(
        Fitting.objects.filter(subsidy_request__isnull=True).values_list("id", flat=True)
    )
    total_missing = len(missing_ids)
    if not total_missing:
        return {"created": 0, "missing": 0}

    objs = [FittingRequest(fitting_id=fid, requested=default_requested) for fid in missing_ids]

    created = 0
    with transaction.atomic():
        for i in range(0, total_missing, chunk_size):
            batch = objs[i : i + chunk_size]
            FittingRequest.objects.bulk_create(batch, ignore_conflicts=True)
            created += len(batch)

    return {"created": created, "missing": total_missing}

@shared_task(bind=True)
def import_corporate_contract_reviews(
    self,
    corporation_id: int | None = None,
    chunk_size: int = 1000,
    force_refresh_contracts: bool = True,
    corptools_force_refresh: bool | None = None,
    match_contracts_on_import: bool = True,
    match_chunk_size: int = 250,
    auto_clear_claims: bool = True,
) -> dict:
    """Imports contract subsidies idempotently; refreshes contracts optionally; exempts qualifying records"""
    if corporation_id is None:
        corporation_id = SubsidyConfig.active().corporation_id

    refresh_result = {"attempted": False, "ok": False}
    if force_refresh_contracts:
        effective_force_refresh = resolve_corptools_force_refresh(corptools_force_refresh)
        refresh_result = _force_refresh_corporate_contracts(
            corporation_id,
            force_refresh=effective_force_refresh,
        )

    qs = CorporateContract.objects.filter(corporation_id=corporation_id).only("id")

    created = 0
    created_contract_pks: list[int] = []


    existing_ids = set(
        CorporateContractSubsidy.objects.filter(
            contract_id__in=qs.values_list("id", flat=True)
        ).values_list("contract_id", flat=True)
    )

    ids = list(qs.values_list("id", flat=True))
    total = len(ids)
    to_create = []

    for i in range(0, total, chunk_size):
        batch_ids = ids[i : i + chunk_size]
        for cc_id in batch_ids:
            if cc_id in existing_ids:
                continue
            to_create.append(CorporateContractSubsidy(contract_id=cc_id))
            created_contract_pks.append(cc_id)
            created += 1

        if len(to_create) >= chunk_size:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_create(to_create, ignore_conflicts=True)
            to_create.clear()

    if to_create:
        with transaction.atomic():
            CorporateContractSubsidy.objects.bulk_create(to_create, ignore_conflicts=True)

    now = timezone.now()
    to_exempt = CorporateContractSubsidy.objects.filter(
        exempt=False,
        contract__status="deleted",
        contract__date_expired__isnull=False,
        contract__date_expired__gt=now,
    ).only("id")
    if to_exempt.exists():
        CorporateContractSubsidy.objects.filter(id__in=to_exempt.values_list("id", flat=True)).update(exempt=True)

    match_result = {
        "matched": 0,
        "created_contract_matches": 0,
        "refreshed_contract_matches": 0,
        "claim_clearance": {"checked": 0, "cleared": 0, "skipped": 0},
    }
    if match_contracts_on_import:
        refreshed_contract_identifiers = refresh_result.get("contract_ids") if isinstance(refresh_result, dict) else []
        match_result = _match_imported_contracts(
            corporation_id=corporation_id,
            created_contract_pks=created_contract_pks,
            refreshed_contract_identifiers=refreshed_contract_identifiers,
            chunk_size=match_chunk_size,
            auto_clear_claims=auto_clear_claims,
        )

    return {
        "created": created,
        "updated": 0,
        "total_contracts": total,
        "contract_refresh": refresh_result,
        "contract_matching": match_result,
    }

@shared_task(bind=True)
def refresh_subsidy_item_prices(self) -> dict:
    try:
        result = update_all_prices.delay()
        return {"queued": True, "task_id": result.id}
    except Exception as e:
        logger.exception("Failed to queue price update: %s", e)
        return {"queued": False, "error": str(e)}

@shared_task
def seed_all_types_into_subsidy(chunk_size: int = 5000) -> dict:
    """
    Ensure every EveType has a SubsidyItemPrice row. Intended to run weekly
    before price refresh. Operates in chunks to limit memory usage.
    """
    if EveType is None:
        logger.warning("eveuniverse not available; cannot seed SubsidyItemPrice.")
        return {"created": 0, "skipped": 0}

    total_types = EveType.objects.count()
    created = 0
    skipped = 0

    logger.info("Seeding SubsidyItemPrice for %s EveTypes…", total_types)

    ids = list(EveType.objects.order_by("id").values_list("id", flat=True))
    for i in range(0, len(ids), chunk_size):
        batch_ids = ids[i:i + chunk_size]
        existing = set(
            SubsidyItemPrice.objects.filter(eve_type_id__in=batch_ids).values_list("eve_type_id", flat=True)
        )
        to_create = [SubsidyItemPrice(eve_type_id=tid, buy=0, sell=0) for tid in batch_ids if tid not in existing]
        skipped += len(batch_ids) - len(to_create)
        if to_create:
            with transaction.atomic():
                SubsidyItemPrice.objects.bulk_create(to_create, ignore_conflicts=True)
            created += len(to_create)
            logger.debug("Seeded %s rows (progress: %s/%s)", len(to_create), min(i + chunk_size, len(ids)), len(ids))

    logger.info("Seeding complete. Created=%s, Skipped=%s", created, skipped)
    return {"created": created, "skipped": skipped}
