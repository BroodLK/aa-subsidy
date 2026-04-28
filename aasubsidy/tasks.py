import requests
from celery import shared_task
from django.db import Error, transaction
from allianceauth.services.hooks import get_extension_logger

from .models import SubsidyItemPrice
from fittings.models import Fitting
from .models import FittingRequest
from corptools.models import CorporateContract
from .models import CorporateContractSubsidy
from .helpers.services_update import update_all_prices
from django.utils import timezone

logger = get_extension_logger(__name__)

try:
    from eveuniverse.models import EveType, EveMarketPrice
except Exception:
    EveType = None
    EveMarketPrice = None


def _force_refresh_corporate_contracts(corporation_id: int) -> dict:
    try:
        from corptools.tasks.corporation.contracts import corp_contract_update
    except Exception as exc:
        logger.warning(
            "Corporate contract refresh unavailable for corporation %s: %s",
            corporation_id,
            exc,
        )
        return {"attempted": False, "ok": False, "error": str(exc)}

    try:
        result = corp_contract_update(corporation_id, force_refresh=True)
        refreshed_ids = []
        if isinstance(result, tuple) and len(result) > 1 and result[1]:
            refreshed_ids = list(result[1])
        logger.info(
            "Forced corptools contract refresh for corporation %s (%s contracts queued for item refresh).",
            corporation_id,
            len(refreshed_ids),
        )
        return {
            "attempted": True,
            "ok": True,
            "contracts_refreshed": len(refreshed_ids),
        }
    except Exception as exc:
        logger.warning(
            "Forced corptools contract refresh failed for corporation %s: %s",
            corporation_id,
            exc,
            exc_info=True,
        )
        return {"attempted": True, "ok": False, "error": str(exc)}


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
) -> dict:
    from .models import SubsidyConfig
    if corporation_id is None:
        corporation_id = SubsidyConfig.active().corporation_id

    refresh_result = {"attempted": False, "ok": False}
    if force_refresh_contracts:
        refresh_result = _force_refresh_corporate_contracts(corporation_id)

    qs = CorporateContract.objects.filter(corporation_id=corporation_id).only("id")

    created = 0


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

    return {
        "created": created,
        "updated": 0,
        "total_contracts": total,
        "contract_refresh": refresh_result,
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
