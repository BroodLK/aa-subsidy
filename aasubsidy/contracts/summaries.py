# file: aasubsidy/contracts/summaries.py
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_UP
from typing import Dict, Iterable, List, Tuple
from django.utils import timezone

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Prefetch,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from eveuniverse.models import EveType
from fittings.models import Fitting, FittingItem
from ..helpers.db import Ceil, Round
from ..models import FittingClaim, FittingRequest, SubsidyConfig, SubsidyItemPrice
from corptools.models import CorporateContract, CorporateContractItem

INCR = 250_000


def _cfg() -> dict:
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or INCR,
    }


def _ceil_to_increment(x: Decimal, inc: Decimal) -> Decimal:
    if inc <= 0:
        return x
    q = (x / inc).quantize(Decimal("1."), rounding=ROUND_UP)
    return q * inc


def doctrine_stock_summary(
    start,
    end,
    corporation_id: int = 1,
    statuses: Tuple[str, ...] | None = ("outstanding",),
):
    cfg = _cfg()
    incr_val = Decimal(cfg["incr"])
    price_field = "sell" if cfg["basis"] == "sell" else "buy"

    contract_filters = {
        "corporation_id": corporation_id,
        "date_issued__gte": start,
        "date_issued__lte": end,
    }
    if statuses:
        contract_filters["status__in"] = list(statuses)

    contract_qs = (
        CorporateContract.objects.filter(
            corporation_id=corporation_id,
            status="outstanding",
            date_expired__gt=timezone.now(),
            date_issued__gte=start,
            date_issued__lte=end,
        )
        .only("id")
    )

    contract_item_counts: Dict[str, Counter] = {}
    items_qs = (
        CorporateContractItem.objects.filter(contract__in=contract_qs, is_included=True)
        .values_list("contract_id", "type_name_id")
        .annotate(total=Sum("quantity"))
        .order_by("contract_id")
    )
    for contract_id, type_name_id, total in items_qs:
        ctr = contract_item_counts.setdefault(contract_id, Counter())
        ctr[int(type_name_id)] += int(total or 0)
    if not contract_item_counts:
        return []

    fit_items_qs = FittingItem.objects.all().only("fit_id", "type_id", "quantity")
    fittings: List[Fitting] = list(
        Fitting.objects.all()
        .only("id", "name", "ship_type_type_id")
        .prefetch_related(Prefetch("items", queryset=fit_items_qs))
    )

    fits_by_hull: Dict[int, List[int]] = defaultdict(list)
    reqs_by_fit: Dict[int, Dict[int, int]] = {}
    fit_order_key: Dict[int, tuple] = {}

    for fit in fittings:
        hull_id = getattr(fit, "ship_type_type_id", None)
        if not hull_id:
            continue
        agg: Dict[int, int] = defaultdict(int)
        for it in fit.items.all():
            agg[int(it.type_id)] += int(it.quantity or 0)
        reqs = dict(agg)
        reqs_by_fit[fit.id] = reqs
        strictness = sum(reqs.values())
        kinds = len(reqs)
        fit_order_key[fit.id] = (-strictness, -kinds, fit.id)
        fits_by_hull[int(hull_id)].append(fit.id)

    for hull_id, fit_list in fits_by_hull.items():
        fit_list.sort(key=lambda fid: fit_order_key[fid])

    def full_sets_for_fit(counter: Counter, requirements: Dict[int, int]) -> int:
        sets = None
        for t, need in requirements.items():
            if need <= 0:
                continue
            have = int(counter.get(t, 0))
            s = have // need
            if sets is None or s < sets:
                sets = s
            if sets == 0:
                return 0
        return sets or 0

    stock_counts: Dict[int, int] = defaultdict(int)
    for ctr in contract_item_counts.values():
        hull_candidates: List[int] = []
        for type_id in ctr.keys():
            if type_id in fits_by_hull:
                hull_candidates.append(int(type_id))
        if not hull_candidates:
            continue
        for hull_id in sorted(set(hull_candidates)):
            assigned = False
            for fit_id in fits_by_hull[hull_id]:
                reqs = reqs_by_fit.get(fit_id) or {}
                if ctr.get(hull_id, 0) < 1:
                    continue
                sets = full_sets_for_fit(ctr, reqs)
                if sets >= 1:
                    stock_counts[fit_id] += 1
                    assigned = True
                    break
            if assigned:
                break

    DEC_0 = DecimalField(max_digits=30, decimal_places=0)
    DEC_2 = DecimalField(max_digits=30, decimal_places=2)
    DEC_4 = DecimalField(max_digits=30, decimal_places=4)

    fi = FittingItem.objects.filter(fit_id=OuterRef("pk")).values("fit_id")

    items_sell = (
        fi.annotate(
            line_sell=Coalesce(
                F("quantity")
                * Coalesce(
                    Subquery(
                        SubsidyItemPrice.objects.filter(
                            eve_type_id=OuterRef("type_id")
                        ).values(price_field)[:1],
                        output_field=DEC_2,
                    ),
                    Value(Decimal("0"), output_field=DEC_2),
                ),
                Value(Decimal("0"), output_field=DEC_2),
            )
        )
        .values("fit_id")
        .annotate(total=Sum("line_sell", output_field=DEC_2))
        .values("total")
    )

    items_volume = (
        fi.annotate(
            line_vol=Coalesce(
                F("quantity")
                * Coalesce(
                    Subquery(
                        EveType.objects.filter(id=OuterRef("type_id"))
                        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
                        .values("eff_vol")[:1],
                        output_field=DEC_4,
                    ),
                    Value(Decimal("0"), output_field=DEC_4),
                ),
                Value(Decimal("0"), output_field=DEC_4),
            )
        )
        .values("fit_id")
        .annotate(total=Sum("line_vol", output_field=DEC_4))
        .values("total")
    )

    ship_sell = Subquery(
        SubsidyItemPrice.objects.filter(
            eve_type_id=OuterRef("ship_type_type_id")
        ).values(price_field)[:1],
        output_field=DEC_2,
    )
    ship_vol = Subquery(
        EveType.objects.filter(id=OuterRef("ship_type_type_id"))
        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
        .values("eff_vol")[:1],
        output_field=DEC_4,
    )

    qs = (
        Fitting.objects.all()
        .annotate(
            items_sell_isk_raw=Coalesce(
                Subquery(items_sell, output_field=DEC_2),
                Value(Decimal("0"), output_field=DEC_2),
            ),
            items_volume_m3=Coalesce(
                Subquery(items_volume, output_field=DEC_4),
                Value(Decimal("0"), output_field=DEC_4),
            ),
            ship_sell_isk_raw=Coalesce(
                ship_sell, Value(Decimal("0"), output_field=DEC_2)
            ),
            ship_volume_m3=Coalesce(
                ship_vol, Value(Decimal("0"), output_field=DEC_4)
            ),
        )
        .annotate(
            items_sell_isk=ExpressionWrapper(
                Ceil(
                    ExpressionWrapper(
                        F("items_sell_isk_raw") / Value(int(INCR), output_field=DEC_2),
                        output_field=DEC_2,
                    ),
                    output_field=DEC_0,
                )
                * Value(int(INCR), output_field=DEC_2),
                output_field=DEC_2,
            ),
            ship_sell_isk=ExpressionWrapper(
                Ceil(
                    ExpressionWrapper(
                        F("ship_sell_isk_raw") / Value(int(INCR), output_field=DEC_2),
                        output_field=DEC_2,
                    ),
                    output_field=DEC_0,
                )
                * Value(int(INCR), output_field=DEC_2),
                output_field=DEC_2,
            ),
        )
        .annotate(
            jita_sell_isk=ExpressionWrapper(
                F("items_sell_isk") + F("ship_sell_isk"), output_field=DEC_2
            ),
            total_vol=ExpressionWrapper(
                F("items_volume_m3") + F("ship_volume_m3"), output_field=DEC_4
            ),
        )
        .annotate(
            stock_requested=Coalesce(
                Subquery(
                    FittingRequest.objects.filter(fitting_id=OuterRef("pk")).values(
                        "requested"
                    )[:1]
                ),
                Value(0),
            )
        )
        .values("pk", "name", "stock_requested", "total_vol", "jita_sell_isk")
        .order_by("name")
    )

    rows: List[dict] = []
    pct = Decimal(cfg["pct"])
    per_m3 = Decimal(cfg["m3"])

    fit_ids = [r["pk"] for r in qs]
    claims_by_fit: Dict[int, int] = defaultdict(int)
    my_claims_by_fit: Dict[int, int] = defaultdict(int)
    req_user_id = getattr(doctrine_stock_summary, "request_user_id", None)

    from allianceauth.eveonline.models import EveCharacter
    from ..contracts.view import get_main_for_character

    claimants_display: Dict[int, str] = {}
    user_any_char = {
        row["user_id"]: EveCharacter.objects.filter(
            character_ownership__user_id=row["user_id"]
        )
        .select_related("character_ownership__user__profile__main_character")
        .only("id")
        .first()
        for row in FittingClaim.objects.filter(fitting_id__in=fit_ids)
        .values("user_id")
        .distinct()
    }

    per_fit_user = (
        FittingClaim.objects.filter(fitting_id__in=fit_ids)
        .values("fitting_id", "user_id")
        .annotate(total=Sum("quantity"))
        .order_by("fitting_id")
    )
    for row in per_fit_user:
        fid = int(row["fitting_id"])
        uid = int(row["user_id"]) if row["user_id"] is not None else None
        qty = int(row["total"] or 0)
        if qty <= 0:
            continue
        any_char = user_any_char.get(uid)
        main_char = get_main_for_character(any_char) if any_char else None
        name = getattr(main_char, "character_name", None) or "Unknown"
        existing = claimants_display.get(fid, "")
        piece = f"{name} ({qty})"
        claimants_display[fid] = f"{existing}, {piece}"[2:] if existing else piece

    all_claims = FittingClaim.objects.filter(fitting_id__in=fit_ids).values("fitting_id").annotate(
        total=Sum("quantity"))

    for c in all_claims:
        claims_by_fit[c["fitting_id"]] = int(c["total"] or 0)

    for c in (
        FittingClaim.objects.filter(fitting_id__in=fit_ids)
        .values("fitting_id")
        .annotate(total=Sum("quantity"))
    ):
        claims_by_fit[int(c["fitting_id"])] = int(c["total"] or 0)

    if req_user_id:
        my_claims = (
            FittingClaim.objects.filter(fitting_id__in=fit_ids, user_id=req_user_id)
            .values("fitting_id")
            .annotate(total=Sum("quantity"))
        )
        for mc in my_claims:
            my_claims_by_fit[mc["fitting_id"]] = int(mc["total"] or 0)

    for r in qs:
        fit_id = int(r["pk"])
        available = int(stock_counts.get(fit_id, 0))
        requested = int(r["stock_requested"] or 0)
        needed = max(requested - available, 0)
        if requested == 0:
            continue

        claimed_total = int(claims_by_fit.get(r["pk"], 0))
        claimed_by_me = int(my_claims_by_fit.get(r["pk"], 0))
        adjusted_needed = max(needed - claimed_total, 0)

        jita_sell = Decimal(r["jita_sell_isk"] or 0)
        total_vol = Decimal(r["total_vol"] or 0)
        base = (jita_sell * pct) + (total_vol * per_m3)
        base = base.quantize(Decimal("0.01"))
        subsidy_isk = _ceil_to_increment(base, incr_val)
        alliance_purchase_isk = _ceil_to_increment(jita_sell + base, incr_val)

        rows.append(
            {
                "fit_id": fit_id,
                "doctrine": r["name"],
                "stock_requested": requested,
                "stock_available": available,
                "stock_needed": needed,
                "claimed_total": claimed_total,
                "claimed_by_me": claimed_by_me,
                "adjusted_needed": adjusted_needed,
                "claimants": claimants_display.get(fit_id, ""),
                "volume_m3": round(float(total_vol or 0), 2),
                "jita_sell_isk": int(jita_sell),
                "subsidy_isk": int(subsidy_isk),
                "alliance_purchase_isk": int(alliance_purchase_isk),
            }
        )

    return rows
