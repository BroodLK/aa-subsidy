import requests
from celery import shared_task
from django.conf import settings
from django.db import Error
from allianceauth.services.hooks import get_extension_logger

from ..models import SubsidyItemPrice

logger = get_extension_logger(__name__)

try:
    from eveuniverse.models import EveMarketPrice
except Exception:
    EveMarketPrice = None


"""External pricing API helpers"""
def valid_janice_api_key() -> bool:
    try:
        c = requests.get(
            "https://janice.e-351.com/api/rest/v2/markets",
            headers={
                "Content-Type": "text/plain",
                "X-ApiKey": getattr(settings, "SUBSIDY_JANICE_API_KEY", ""),
                "accept": "application/json",
            },
            timeout=20,
        )
        c.raise_for_status()
        data = c.json()
        if isinstance(data, dict) and "status" in data:
            logger.info("Janice API status: %s", data)
            return False
        return True
    except Exception as e:
        logger.warning("Janice API check failed: %s", e)
        return False

def _update_price_bulk(type_ids: list[int]) -> dict:
    api_key = getattr(settings, "SUBSIDY_JANICE_API_KEY", "") or ""
    if api_key:
        logger.info("Using Janice API for price updates")
        try:
            r = requests.post(
                "https://janice.e-351.com/api/rest/v2/pricer?market=2",
                data="\n".join([str(x) for x in type_ids]),
                headers={
                    "Content-Type": "text/plain",
                    "X-ApiKey": api_key,
                    "accept": "application/json",
                },
                timeout=60,
            )
            r.raise_for_status()
            payload = r.json()
            output: dict[str, dict] = {}
            for item in payload:
                try:
                    eid = str(item["itemType"]["eid"])
                    output[eid] = {
                        "buy": {"percentile": str(item["top5AveragePrices"]["buyPrice5DayMedian"])},
                        "sell": {"percentile": str(item["top5AveragePrices"]["sellPrice5DayMedian"])},
                    }
                except Exception:
                    continue
            return output
        except Exception as e:
            logger.warning("Janice request failed, falling back to Fuzzworks: %s", e)

"""Scheduled updates"""
@shared_task
def update_all_prices() -> dict:
    type_ids: list[int] = []
    market_data: dict = {}

    prices = SubsidyItemPrice.objects.select_related("eve_type").all()
    total = prices.count()
    logger.info("SubsidyItemPrice update starting for %s items (Janice)...", total)

    for item in prices:
        type_ids.append(item.eve_type_id)
        if len(type_ids) == 1000:
            market_data.update(_update_price_bulk(type_ids))
            type_ids.clear()
    if type_ids:
        market_data.update(_update_price_bulk(type_ids))

    logger.info("Market data fetched, starting database update...")
    missing_items: list[str] = []
    updated = 0

    for price in prices:
        key = str(price.eve_type_id)
        if key in market_data:
            try:
                buy = float(market_data[key]["buy"]["percentile"])
            except Exception:
                buy = 0.0
            try:
                sell = float(market_data[key]["sell"]["percentile"])
            except Exception:
                sell = 0.0
        else:
            missing_items.append(getattr(price.eve_type, "name", str(price.eve_type_id)))
            buy, sell = 0.0, 0.0

        price.buy = buy
        price.sell = sell
        updated += 1

    try:
        SubsidyItemPrice.objects.bulk_update(prices, ["buy", "sell"])
        logger.info("Updated %s SubsidyItemPrice rows.", updated)
    except Error as e:
        logger.error("Error updating SubsidyItemPrice: %s", e)

    return {"updated": updated, "missing": len(missing_items)}


@shared_task
def ensure_prices_for_types(type_ids: list[int] | None = None) -> dict:
    if not type_ids:
        logger.info("ensure_prices_for_types: no type_ids provided; skipping.")
        return {"created": 0, "skipped": 0}
    existing = set(
        SubsidyItemPrice.objects.filter(eve_type_id__in=type_ids).values_list("eve_type_id", flat=True)
    )
    to_create: list[SubsidyItemPrice] = []
    for tid in type_ids:
        if tid not in existing:
            to_create.append(SubsidyItemPrice(eve_type_id=tid, buy=0, sell=0))
    if to_create:
        SubsidyItemPrice.objects.bulk_create(to_create, ignore_conflicts=True)
        logger.info("Created %s SubsidyItemPrice rows.", len(to_create))
        return {"created": len(to_create), "skipped": len(type_ids) - len(to_create)}
    logger.info("No missing SubsidyItemPrice rows.")
    return {"created": 0, "skipped": len(type_ids)}
