from functools import lru_cache

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.hooks import get_extension_logger
from corptools.models import (
    CorporateContract,
    CorporateContractItem,
    CorporationAudit,
    EveItemCategory,
    EveItemGroup,
    EveItemType,
    EveName,
)
from esi.errors import TokenError
from esi.exceptions import HTTPClientError, HTTPNotModified
from esi.models import Token
from esi.openapi_clients import ESIClientProvider

from . import __title__, __version__
from .contracts.filters import apply_contract_exclusions
from .contracts.matching import match_contracts
from .helpers.contract_import import plan_claim_clearance
from .helpers.services_update import update_all_prices
from .models import SubsidyConfig, SubsidyItemPrice
from fittings.models import Fitting
from .models import FittingRequest
from .models import CorporateContractSubsidy, FittingClaim, FittingClaimAutoClearance

logger = get_extension_logger(__name__)

DEFAULT_SUBSIDY_CORPORATION_ID = 98660859
ESI_CONTRACT_SCOPE = "esi-contracts.read_corporation_contracts.v1"
ESI_CONTRACT_ITEM_TYPES_BATCH_SIZE = 1000

try:
    from eveuniverse.models import EveType, EveMarketPrice
except Exception:
    EveType = None
    EveMarketPrice = None


def _corporation_contract_queryset(corporation_id: int):
    return CorporateContract.objects.filter(
        corporation__corporation__corporation_id=corporation_id
    )


@lru_cache(maxsize=1)
def _esi_contract_client():
    return ESIClientProvider(
        compatibility_date=timezone.now().date(),
        ua_appname=__title__,
        ua_version=__version__,
        tags=["Contracts"],
    ).client


@lru_cache(maxsize=1)
def _esi_universe_client():
    return ESIClientProvider(
        compatibility_date=timezone.now().date(),
        ua_appname=__title__,
        ua_version=__version__,
        tags=["Universe"],
    ).client


def _esi_value(payload, field: str, default=None):
    if isinstance(payload, dict):
        return payload.get(field, default)
    return getattr(payload, field, default)


def _normalize_int(value, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    return int(value)


def _unique_positive_ids(values) -> list[int]:
    return sorted({int(value) for value in values if value})


def _placeholder_eve_item_type(type_id: int) -> EveItemType:
    eve_type, _ = EveItemType.objects.update_or_create(
        type_id=type_id,
        defaults={
            "name": str(type_id),
            "group": None,
            "description": None,
            "mass": None,
            "packaged_volume": None,
            "portion_size": None,
            "volume": None,
            "published": False,
            "radius": None,
        },
    )
    return eve_type


def _sync_eve_item_category_via_esi(category_id: int):
    if not category_id:
        return None

    category = EveItemCategory.objects.filter(category_id=category_id).first()
    if category is not None:
        return category

    payload = _esi_universe_client().Universe.GetUniverseCategoriesCategoryId(
        category_id=category_id
    ).result()
    category, _ = EveItemCategory.objects.update_or_create(
        category_id=category_id,
        defaults={"name": str(_esi_value(payload, "name", category_id))},
    )
    return category


def _sync_eve_item_group_via_esi(group_id: int):
    if not group_id:
        return None

    group = EveItemGroup.objects.filter(group_id=group_id).first()
    if group is not None:
        return group

    payload = _esi_universe_client().Universe.GetUniverseGroupsGroupId(
        group_id=group_id
    ).result()
    category = None
    category_id = _normalize_int(_esi_value(payload, "category_id"))
    if category_id:
        category = _sync_eve_item_category_via_esi(category_id)

    group, _ = EveItemGroup.objects.update_or_create(
        group_id=group_id,
        defaults={
            "name": str(_esi_value(payload, "name", group_id)),
            "category": category,
        },
    )
    return group


def _sync_eve_item_type_via_esi(type_id: int):
    payload = _esi_universe_client().Universe.GetUniverseTypesTypeId(
        type_id=type_id
    ).result()
    group = None
    group_id = _normalize_int(_esi_value(payload, "group_id"))
    if group_id:
        group = _sync_eve_item_group_via_esi(group_id)

    eve_type, _ = EveItemType.objects.update_or_create(
        type_id=type_id,
        defaults={
            "name": str(_esi_value(payload, "name", type_id)),
            "group": group,
            "description": _esi_value(payload, "description"),
            "mass": _esi_value(payload, "mass"),
            "packaged_volume": _esi_value(payload, "packaged_volume"),
            "portion_size": _esi_value(payload, "portion_size"),
            "volume": _esi_value(payload, "volume"),
            "published": bool(_esi_value(payload, "published", False)),
            "radius": _esi_value(payload, "radius"),
        },
    )
    return eve_type


def _ensure_eve_item_types_via_esi(type_ids) -> None:
    missing_type_ids = set(_unique_positive_ids(type_ids))
    if not missing_type_ids:
        return

    existing_type_ids = set(
        EveItemType.objects.filter(type_id__in=missing_type_ids).values_list("type_id", flat=True)
    )
    for type_id in sorted(missing_type_ids - existing_type_ids):
        try:
            _sync_eve_item_type_via_esi(type_id)
        except Exception as exc:
            logger.warning(
                "Failed to sync item type %s from ESI; creating placeholder row instead: %s",
                type_id,
                exc,
                exc_info=True,
            )
            _placeholder_eve_item_type(type_id)


def _get_corporation_audit(corporation_id: int) -> CorporationAudit:
    return CorporationAudit.objects.select_related("corporation").get(
        corporation__corporation_id=corporation_id
    )


def _get_corporation_contract_tokens(corporation_id: int) -> list[Token]:
    character_ids = EveCharacter.objects.filter(
        corporation_id=corporation_id
    ).values_list("character_id", flat=True)
    return list(
        Token.objects.filter(character_id__in=character_ids)
        .require_scopes([ESI_CONTRACT_SCOPE])
        .order_by("-created")
    )


def _fetch_corporation_contracts_from_esi(
    corporation_id: int,
    *,
    force_refresh: bool,
) -> tuple[list, Token]:
    client = _esi_contract_client()
    tokens = _get_corporation_contract_tokens(corporation_id)
    if not tokens:
        raise RuntimeError(
            f"No ESI token with scope {ESI_CONTRACT_SCOPE} is available for corporation {corporation_id}."
        )

    last_error: Exception | None = None
    for token in tokens:
        try:
            contracts = client.Contracts.GetCorporationsCorporationIdContracts(
                corporation_id=corporation_id,
                token=token,
            ).results(force_refresh=force_refresh)
            return list(contracts), token
        except HTTPClientError as exc:
            if getattr(exc, "status_code", None) in {401, 403}:
                last_error = exc
                continue
            raise
        except TokenError as exc:
            last_error = exc
            continue

    raise last_error or RuntimeError(
        f"Unable to authenticate a corporation contract token for corporation {corporation_id}."
    )


def _fetch_contract_items_from_esi(
    corporation_id: int,
    contract_id: int,
    *,
    token: Token,
    force_refresh: bool,
) -> tuple[list, Token]:
    client = _esi_contract_client()
    tokens = [token]
    tokens.extend(
        candidate
        for candidate in _get_corporation_contract_tokens(corporation_id)
        if candidate.pk != token.pk
    )

    last_error: Exception | None = None
    for candidate in tokens:
        try:
            items = client.Contracts.GetCorporationsCorporationIdContractsContractIdItems(
                corporation_id=corporation_id,
                contract_id=contract_id,
                token=candidate,
            ).results(force_refresh=force_refresh)
            return list(items), candidate
        except HTTPClientError as exc:
            if getattr(exc, "status_code", None) == 404:
                raise
            if getattr(exc, "status_code", None) in {401, 403}:
                last_error = exc
                continue
            raise
        except TokenError as exc:
            last_error = exc
            continue

    raise last_error or RuntimeError(
        f"Unable to authenticate a corporation contract-item token for corporation {corporation_id}."
    )


def _sync_corporate_contract_items_via_esi(
    *,
    corporation_id: int,
    contracts_by_id: dict[int, CorporateContract],
    token: Token,
    existing_item_contract_ids: set[int],
    force_refresh: bool,
) -> dict:
    items_synced = 0
    contracts_with_items = 0
    contracts_without_items = 0
    reused_existing_items = 0
    matchable_contract_ids: set[int] = set()
    failures: list[dict[str, object]] = []
    active_token = token

    for contract_id, contract in contracts_by_id.items():
        if str(contract.status).lower() == "deleted":
            continue

        try:
            items, active_token = _fetch_contract_items_from_esi(
                corporation_id,
                contract_id,
                token=active_token,
                force_refresh=force_refresh,
            )
        except HTTPClientError as exc:
            if getattr(exc, "status_code", None) == 404:
                if contract_id in existing_item_contract_ids:
                    reused_existing_items += 1
                    matchable_contract_ids.add(contract_id)
                else:
                    contracts_without_items += 1
                failures.append(
                    {
                        "contract_id": contract_id,
                        "status_code": 404,
                        "error": "contract_items_not_ready",
                    }
                )
                logger.info(
                    "Corporate contract items are not yet available from ESI for corporation %s contract %s.",
                    corporation_id,
                    contract_id,
                )
                continue

            if contract_id in existing_item_contract_ids:
                reused_existing_items += 1
                matchable_contract_ids.add(contract_id)
            failures.append(
                {
                    "contract_id": contract_id,
                    "status_code": getattr(exc, "status_code", None),
                    "error": str(exc),
                }
            )
            logger.warning(
                "Failed to sync contract items from ESI for corporation %s contract %s: %s",
                corporation_id,
                contract_id,
                exc,
                exc_info=True,
            )
            continue
        except Exception as exc:
            if contract_id in existing_item_contract_ids:
                reused_existing_items += 1
                matchable_contract_ids.add(contract_id)
            failures.append({"contract_id": contract_id, "error": str(exc)})
            logger.warning(
                "Failed to sync contract items from ESI for corporation %s contract %s: %s",
                corporation_id,
                contract_id,
                exc,
                exc_info=True,
            )
            continue

        type_ids = _unique_positive_ids(_esi_value(item, "type_id") for item in items)
        if type_ids:
            _ensure_eve_item_types_via_esi(type_ids)

        new_items: list[CorporateContractItem] = []
        for item in items:
            record_id = _normalize_int(_esi_value(item, "record_id"))
            type_id = _normalize_int(_esi_value(item, "type_id"))
            if not record_id or not type_id:
                continue
            new_items.append(
                CorporateContractItem(
                    contract_id=contract.id,
                    is_included=bool(_esi_value(item, "is_included", False)),
                    is_singleton=bool(_esi_value(item, "is_singleton", False)),
                    quantity=int(_esi_value(item, "quantity", 0) or 0),
                    raw_quantity=_normalize_int(_esi_value(item, "raw_quantity")),
                    record_id=record_id,
                    type_name_id=type_id,
                )
            )

        with transaction.atomic():
            CorporateContractItem.objects.filter(contract_id=contract.id).delete()
            if new_items:
                CorporateContractItem.objects.bulk_create(
                    new_items,
                    batch_size=ESI_CONTRACT_ITEM_TYPES_BATCH_SIZE,
                )

        if new_items:
            items_synced += len(new_items)
            contracts_with_items += 1
            matchable_contract_ids.add(contract_id)
            existing_item_contract_ids.add(contract_id)
        else:
            contracts_without_items += 1
            existing_item_contract_ids.discard(contract_id)

    return {
        "items_synced": items_synced,
        "contracts_with_items": contracts_with_items,
        "contracts_without_items": contracts_without_items,
        "contracts_reused_existing_items": reused_existing_items,
        "contract_ids": sorted(matchable_contract_ids),
        "failures": failures,
    }


def _sync_corporate_contracts_via_esi(corporation_id: int, *, force_refresh: bool = True) -> dict:
    try:
        audit_corp = _get_corporation_audit(corporation_id)
    except CorporationAudit.DoesNotExist:
        logger.warning(
            "Skipping direct ESI contract sync for corporation %s: no matching CorporationAudit row exists.",
            corporation_id,
        )
        return {
            "attempted": False,
            "ok": False,
            "skipped": True,
            "error": "missing_corporation_audit",
            "corporation_id": corporation_id,
        }

    try:
        contracts, token = _fetch_corporation_contracts_from_esi(
            corporation_id,
            force_refresh=force_refresh,
        )
    except HTTPNotModified:
        logger.info(
            "Corporate contracts were unchanged for corporation %s.",
            corporation_id,
        )
        return {
            "attempted": True,
            "ok": True,
            "contracts_refreshed": 0,
            "contract_ids": [],
            "all_contract_ids": [],
            "items_synced": 0,
            "contracts_with_items": 0,
            "contracts_without_items": 0,
            "contracts_reused_existing_items": 0,
            "item_failures": [],
            "mode": "django_esi",
            "not_modified": True,
        }
    except Exception as exc:
        logger.warning(
            "Direct ESI contract sync failed for corporation %s: %s",
            corporation_id,
            exc,
            exc_info=True,
        )
        return {
            "attempted": True,
            "ok": False,
            "error": str(exc),
            "corporation_id": corporation_id,
            "mode": "django_esi",
        }

    eve_name_ids = _unique_positive_ids(
        value
        for contract in contracts
        for value in (
            _esi_value(contract, "acceptor_id"),
            _esi_value(contract, "assignee_id"),
            _esi_value(contract, "issuer_id"),
            _esi_value(contract, "issuer_corporation_id"),
        )
    )
    if eve_name_ids:
        EveName.objects.create_bulk_from_esi(eve_name_ids)

    existing_contract_ids = set(
        CorporateContract.objects.filter(corporation=audit_corp).values_list("contract_id", flat=True)
    )
    existing_item_contract_ids = set(
        CorporateContractItem.objects.filter(contract__corporation=audit_corp)
        .values_list("contract__contract_id", flat=True)
        .distinct()
    )

    contracts_to_create: list[CorporateContract] = []
    contracts_to_update: list[CorporateContract] = []
    contracts_by_id: dict[int, CorporateContract] = {}
    deleted_contract_pks: list[str] = []

    for payload in contracts:
        contract_id = _normalize_int(_esi_value(payload, "contract_id"))
        if not contract_id:
            continue

        contract = CorporateContract(
            id=CorporateContract.build_pk(audit_corp.id, contract_id),
            corporation=audit_corp,
            contract_id=contract_id,
            acceptor_id=_normalize_int(_esi_value(payload, "acceptor_id")),
            acceptor_name_id=_normalize_int(_esi_value(payload, "acceptor_id")),
            assignee_id=_normalize_int(_esi_value(payload, "assignee_id")),
            assignee_name_id=_normalize_int(_esi_value(payload, "assignee_id")),
            issuer_id=_normalize_int(_esi_value(payload, "issuer_id")),
            issuer_name_id=_normalize_int(_esi_value(payload, "issuer_id")),
            issuer_corporation_id=_normalize_int(_esi_value(payload, "issuer_corporation_id")),
            issuer_corporation_name_id=_normalize_int(_esi_value(payload, "issuer_corporation_id")),
            availability=str(_esi_value(payload, "availability", "") or ""),
            buyout=_esi_value(payload, "buyout"),
            collateral=_esi_value(payload, "collateral"),
            date_accepted=_esi_value(payload, "date_accepted"),
            date_completed=_esi_value(payload, "date_completed"),
            date_expired=_esi_value(payload, "date_expired"),
            date_issued=_esi_value(payload, "date_issued"),
            days_to_complete=_normalize_int(_esi_value(payload, "days_to_complete")),
            end_location_id=_normalize_int(_esi_value(payload, "end_location_id")),
            for_corporation=bool(_esi_value(payload, "for_corporation", False)),
            price=_esi_value(payload, "price"),
            reward=_esi_value(payload, "reward"),
            start_location_id=_normalize_int(_esi_value(payload, "start_location_id")),
            status=str(_esi_value(payload, "status", "") or ""),
            title=str(_esi_value(payload, "title", "") or ""),
            contract_type=str(_esi_value(payload, "type", "") or ""),
            volume=_esi_value(payload, "volume"),
        )
        contracts_by_id[contract_id] = contract

        if contract_id in existing_contract_ids:
            contracts_to_update.append(contract)
        else:
            contracts_to_create.append(contract)

        if contract.status.lower() == "deleted":
            deleted_contract_pks.append(contract.id)

    if contracts_to_create:
        CorporateContract.objects.bulk_create(
            contracts_to_create,
            batch_size=1000,
            ignore_conflicts=True,
        )

    if contracts_to_update:
        CorporateContract.objects.bulk_update(
            contracts_to_update,
            fields=[
                "acceptor_id",
                "acceptor_name_id",
                "assignee_id",
                "assignee_name_id",
                "issuer_id",
                "issuer_name_id",
                "issuer_corporation_id",
                "issuer_corporation_name_id",
                "availability",
                "buyout",
                "collateral",
                "date_accepted",
                "date_completed",
                "date_expired",
                "date_issued",
                "days_to_complete",
                "end_location_id",
                "for_corporation",
                "price",
                "reward",
                "start_location_id",
                "status",
                "title",
                "contract_type",
                "volume",
            ],
            batch_size=1000,
        )

    if deleted_contract_pks:
        CorporateContractItem.objects.filter(contract_id__in=deleted_contract_pks).delete()

    item_result = _sync_corporate_contract_items_via_esi(
        corporation_id=corporation_id,
        contracts_by_id=contracts_by_id,
        token=token,
        existing_item_contract_ids=existing_item_contract_ids,
        force_refresh=force_refresh,
    )

    audit_corp.last_update_contracts = timezone.now()
    audit_corp.save(update_fields=["last_update_contracts"])

    logger.info(
        "Direct ESI contract sync completed for corporation %s: %s contracts, %s contracts with items, %s item rows, %s item failures.",
        corporation_id,
        len(contracts_by_id),
        item_result["contracts_with_items"],
        item_result["items_synced"],
        len(item_result["failures"]),
    )

    return {
        "attempted": True,
        "ok": True,
        "contracts_refreshed": len(contracts_by_id),
        "contract_ids": item_result["contract_ids"],
        "all_contract_ids": sorted(contracts_by_id.keys()),
        "items_synced": item_result["items_synced"],
        "contracts_with_items": item_result["contracts_with_items"],
        "contracts_without_items": item_result["contracts_without_items"],
        "contracts_reused_existing_items": item_result["contracts_reused_existing_items"],
        "item_failures": item_result["failures"],
        "mode": "django_esi",
        "force_refresh": force_refresh,
        "token_character_id": getattr(token, "character_id", None),
    }


@shared_task(bind=True)
def sync_corporate_contracts_from_esi(
    self,
    corporation_id: int | None = None,
    force_refresh: bool = True,
) -> dict:
    if corporation_id is None:
        corporation_id = DEFAULT_SUBSIDY_CORPORATION_ID
    return _sync_corporate_contracts_via_esi(
        corporation_id,
        force_refresh=force_refresh,
    )


def _resolve_corporate_contract_pks(corporation_id: int, identifiers: list[int] | None = None) -> list[int]:
    raw_ids = sorted({int(identifier) for identifier in (identifiers or []) if identifier})
    if not raw_ids:
        return []

    rows = _corporation_contract_queryset(corporation_id).filter(
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

    unresolved_contracts = _corporation_contract_queryset(corporation_id).filter(
        aasubsidy_meta__isnull=False,
        doctrine_match__isnull=True,
    ).values_list("id", flat=True)
    target_contract_pks.update(int(contract_pk) for contract_pk in unresolved_contracts)

    cfg = SubsidyConfig.active()
    filtered_contract_pks = [
        int(contract_pk)
        for contract_pk in apply_contract_exclusions(
            _corporation_contract_queryset(corporation_id).filter(
                pk__in=target_contract_pks,
            ),
            cfg,
        ).values_list("id", flat=True)
    ]

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
        corporation_id = DEFAULT_SUBSIDY_CORPORATION_ID

    refresh_result = {"attempted": False, "ok": False}
    if force_refresh_contracts:
        if corptools_force_refresh is not None:
            logger.info(
                "Ignoring deprecated corptools_force_refresh=%s for corporation %s; using direct django-esi sync.",
                corptools_force_refresh,
                corporation_id,
            )
        refresh_result = _sync_corporate_contracts_via_esi(
            corporation_id,
            force_refresh=True,
        )

    qs = _corporation_contract_queryset(corporation_id).only("id")

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
