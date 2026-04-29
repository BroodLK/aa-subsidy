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
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from eveuniverse.models import EveType
from fittings.models import Fitting, FittingItem, Doctrine
from .filters import apply_contract_exclusions
from ..helpers.db import Ceil, Round
from ..models import (
    FittingClaim,
    FittingRequest,
    SubsidyConfig,
    SubsidyItemPrice,
    DoctrineSystem,
)
from corptools.models import CorporateContract
from .matching import get_or_match_contracts
from allianceauth.eveonline.models import EveCharacter

INCR = 250_000


def _cfg() -> dict:
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or INCR,
        "corporation_id": cfg.corporation_id,
    }


def _ceil_to_increment(x: Decimal, inc: Decimal) -> Decimal:
    if inc <= 0:
        return x
    q = (x / inc).quantize(Decimal("1."), rounding=ROUND_UP)
    return q * inc


def doctrine_stock_summary(
    start,
    end,
    corporation_id: int | None = None,
    statuses: Tuple[str, ...] | None = ("outstanding",),
    request_user_id: int | None = None,
):
    cfg_model = SubsidyConfig.active()
    cfg = _cfg()
    if corporation_id is None:
        corporation_id = cfg["corporation_id"]
    incr_val = Decimal(cfg["incr"])
    price_field = "sell" if cfg["basis"] == "sell" else "buy"

    contract_filters = {
        "corporation_id": corporation_id,
        "date_issued__gte": start,
        "date_issued__lte": end,
    }
    if statuses:
        contract_filters["status__in"] = list(statuses)

    if statuses:
        status_q = Q()
        for s in statuses:
            status_q |= Q(status__iexact=s)
    else:
        status_q = Q(status__iexact="outstanding")

    contract_qs = (
        CorporateContract.objects.filter(
            status_q,
            corporation_id=corporation_id,
            date_expired__gt=timezone.now(),
            date_issued__gte=start,
            date_issued__lte=end,
        )
        .select_related("start_location_name__system")
    )
    contract_qs = apply_contract_exclusions(contract_qs, cfg_model)

    # Use values to avoid object overhead and ensure we get the IDs correctly
    contract_data = list(
        contract_qs.values(
            "id",
            "start_location_id",
            "start_location_name_id",
            "start_location_name__system_id",
        )
    )

    # Bulk resolve missing systems if possible
    missing_system_loc_ids = {
        c["start_location_id"]
        for c in contract_data
        if not c["start_location_name__system_id"]
    }
    resolved_systems = {}
    if missing_system_loc_ids:
        from corptools.models import EveLocation

        resolved_systems = dict(
            EveLocation.objects.filter(
                location_id__in=missing_system_loc_ids, system__isnull=False
            ).values_list("location_id", "system_id")
        )

    contract_locations = {}
    for c in contract_data:
        cid = c["id"]
        locs = {c["start_location_id"]}

        # Add the system ID from FK if present
        if c["start_location_name__system_id"]:
            locs.add(c["start_location_name__system_id"])
        # Or from bulk resolved map
        elif c["start_location_id"] in resolved_systems:
            locs.add(resolved_systems[c["start_location_id"]])

        contract_locations[cid] = locs

    contract_pks = list(contract_qs.values_list("pk", flat=True))
    match_map = get_or_match_contracts(contract_pks, persist=True, refresh=False)

    # DEBUG
    print(f"\n=== SUMMARY PAGE DEBUG ===")
    print(f"Found {len(contract_pks)} contracts")
    print(f"Got {len(match_map)} match results")
    matched_results = [(pk, r) for pk, r in match_map.items() if r.matched_fitting_id]
    print(f"Of those, {len(matched_results)} have a matched_fitting_id")
    # Show first 3 MATCHED contracts (not just first 3)
    print("First 3 matched contracts:")
    for pk, result in matched_results[:3]:
        print(f"  PK={pk}: fitting_id={result.matched_fitting_id}, name={result.matched_fitting_name}, status={result.match_status}, source={result.match_source}")
    print(f"matched_fit_map will have {len(matched_results)} entries")
    print("=== END DEBUG ===\n")

    # Count all contracts that have a matched fitting, regardless of status
    # This includes "matched", "needs_review", and even forced matches
    matched_fit_map: Dict[int, int] = {
        contract_pk: int(result.matched_fitting_id)
        for contract_pk, result in match_map.items()
        if result.matched_fitting_id  # Any contract with a fitting ID assigned
    }

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
        SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("ship_type_type_id")).values(
            price_field
        )[:1],
        output_field=DEC_2,
    )
    ship_vol = Subquery(
        EveType.objects.filter(id=OuterRef("ship_type_type_id"))
        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
        .values("eff_vol")[:1],
        output_field=DEC_4,
    )

    qs_base = (
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
            ship_sell_isk_raw=Coalesce(ship_sell, Value(Decimal("0"), output_field=DEC_2)),
            ship_volume_m3=Coalesce(ship_vol, Value(Decimal("0"), output_field=DEC_4)),
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
            jita_sell_isk=ExpressionWrapper(F("items_sell_isk") + F("ship_sell_isk"), output_field=DEC_2),
            total_vol=ExpressionWrapper(F("items_volume_m3") + F("ship_volume_m3"), output_field=DEC_4),
        )
    )

    systems = list(
        DoctrineSystem.objects.filter(is_active=True)
        .prefetch_related("locations")
        .order_by("name")
    )
    system_locations: Dict[int, set] = {}
    for system in systems:
        locs = {loc.location_id for loc in system.locations.all()}
        if locs:
            system_locations[system.id] = locs

    results = []
    pct = Decimal(cfg["pct"])
    per_m3 = Decimal(cfg["m3"])

    fit_ids = list(Fitting.objects.values_list("id", flat=True))
    claims_by_fit: Dict[int, int] = defaultdict(int)
    my_claims_by_fit: Dict[int, int] = defaultdict(int)

    from ..contracts.view import get_main_for_character

    # claimants_display[fit_id] = string representation
    # claimants_raw_str[fit_id] = "user_id:name:qty|user_id:name:qty"
    claimants_display: Dict[int, str] = {}
    claimants_raw_str: Dict[int, str] = {}

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
        
        # Format: user_id:name:quantity
        raw_piece = f"{uid}:{name}:{qty}"
        existing_raw = claimants_raw_str.get(fid, "")
        claimants_raw_str[fid] = f"{existing_raw}|{raw_piece}" if existing_raw else raw_piece

    for c in (
        FittingClaim.objects.filter(fitting_id__in=fit_ids)
        .values("fitting_id")
        .annotate(total=Sum("quantity"))
    ):
        claims_by_fit[int(c["fitting_id"])] = int(c["total"] or 0)

    if request_user_id:
        my_claims = (
            FittingClaim.objects.filter(fitting_id__in=fit_ids, user_id=request_user_id)
            .values("fitting_id")
            .annotate(total=Sum("quantity"))
        )
        for mc in my_claims:
            my_claims_by_fit[mc["fitting_id"]] = int(mc["total"] or 0)

    # Map each fitting to its doctrine names for display
    fit_to_doctrines_list = defaultdict(list)
    all_doctrines = list(Doctrine.objects.prefetch_related("fittings").all())
    for d in all_doctrines:
        for f in d.fittings.all():
            fit_to_doctrines_list[f.id].append(d)

    for system in systems:
        allowed_locations = system_locations.get(system.id)
        system_stock_counts: Dict[int, int] = defaultdict(int)

        # DEBUG
        print(f"System '{system.name}': allowed_locations = {allowed_locations}")

        matched_count = 0
        skipped_count = 0
        for cid, fit_id in matched_fit_map.items():
            if allowed_locations is not None:
                c_locs = contract_locations.get(cid, set())
                if not (c_locs & allowed_locations):
                    skipped_count += 1
                    if skipped_count <= 3:  # Show first 3 skipped contracts
                        print(f"  SKIP contract cid={cid}: its locations {c_locs} don't match {allowed_locations}")
                    continue
                else:
                    matched_count += 1
            else:
                # ISSUE: If no locations configured for this system, skip ALL contracts
                continue
            system_stock_counts[int(fit_id)] += 1

        print(f"  -> Counted {sum(system_stock_counts.values())} contracts ({matched_count} matched, {skipped_count} skipped)")

        # Calculate doctrine-level totals for this system
        # requested_per_fit[fit_id] = FittingRequest.requested in this system
        system_fit_reqs = dict(
            FittingRequest.objects.filter(system=system).values_list("fitting_id", "requested")
        )
        doctrine_sum_req = defaultdict(int)
        for d in all_doctrines:
            for f in d.fittings.all():
                doctrine_sum_req[d.id] += system_fit_reqs.get(f.id, 0)

        fittings_with_stock = [
            fid for fid, count in system_stock_counts.items() if count > 0
        ]

        system_qs = (
            qs_base.annotate(
                stock_requested=Coalesce(
                    Subquery(
                        FittingRequest.objects.filter(
                            fitting_id=OuterRef("pk"), system=system
                        ).values("requested")[:1]
                    ),
                    Value(0),
                )
            )
            .filter(Q(stock_requested__gt=0) | Q(pk__in=fittings_with_stock))
            .values("pk", "name", "stock_requested", "total_vol", "jita_sell_isk")
            .order_by("name")
        )

        system_rows = []
        for r in system_qs:
            fit_id = int(r["pk"])
            available = int(system_stock_counts.get(fit_id, 0))
            requested = int(r["stock_requested"] or 0)
            needed = max(requested - available, 0)

            claimed_total = int(claims_by_fit.get(fit_id, 0))
            claimed_by_me = int(my_claims_by_fit.get(fit_id, 0))
            adjusted_needed = max(needed - claimed_total, 0)

            jita_sell = Decimal(r["jita_sell_isk"] or 0)
            total_vol = Decimal(r["total_vol"] or 0)
            base = (jita_sell * pct) + (total_vol * per_m3)
            base = base.quantize(Decimal("0.01"))
            subsidy_isk = _ceil_to_increment(base, incr_val)
            alliance_purchase_isk = _ceil_to_increment(jita_sell + base, incr_val)

            if requested == 0:
                subsidy_isk = Decimal("0")
                alliance_purchase_isk = Decimal("0")

            # Pick the best doctrine for this fitting in this system
            fitting_doctrines = fit_to_doctrines_list.get(fit_id, [])
            if not fitting_doctrines:
                best_doctrine_name = "No Doctrine"
                best_doctrine_id = None
            else:
                # Sort doctrines by doctrine_sum_req[d.id] desc, then name asc
                # Convert to list to avoid mutating the original
                sorted_docs = sorted(
                    fitting_doctrines,
                    key=lambda doc: (-doctrine_sum_req[doc.id], doc.name)
                )
                best_doctrine_name = sorted_docs[0].name
                best_doctrine_id = sorted_docs[0].id

            system_rows.append(
                {
                    "fit_id": fit_id,
                    "doctrine": best_doctrine_name,
                    "doctrine_id": best_doctrine_id,
                    "fitting_name": r["name"],
                    "stock_requested": requested,
                    "stock_available": available,
                    "stock_needed": needed,
                    "claimed_total": claimed_total,
                    "claimed_by_me": claimed_by_me,
                    "adjusted_needed": adjusted_needed,
                    "claimants": claimants_display.get(fit_id, ""),
                    "claimants_raw": claimants_raw_str.get(fit_id, ""),
                    "volume_m3": round(float(total_vol or 0), 2),
                    "jita_sell_isk": int(jita_sell),
                    "subsidy_isk": int(subsidy_isk),
                    "alliance_purchase_isk": int(alliance_purchase_isk),
                }
            )

        if system_rows:
            results.append(
                {
                    "system_name": system.name,
                    "system_description": system.description,
                    "has_locations": allowed_locations is not None,
                    "rows": system_rows,
                    "totals": {
                        "requested": sum(r["stock_requested"] for r in system_rows),
                        "available": sum(r["stock_available"] for r in system_rows),
                        "needed": sum(r["stock_needed"] for r in system_rows),
                    },
                }
            )

    return results


def doctrine_insights(corporation_id: int | None = None):
    from .payments import _user_id_for_issuer_eve_id, _main_name_for_user_id

    cfg_model = SubsidyConfig.active()
    cfg = _cfg()
    if corporation_id is None:
        corporation_id = cfg["corporation_id"]
    now = timezone.now()
    slow_threshold = now - timezone.timedelta(days=7)
    expired_threshold = now - timezone.timedelta(days=30)
    sold_threshold = now - timezone.timedelta(days=30)

    slow_contracts_qs = (
        CorporateContract.objects.filter(
            corporation_id=corporation_id,
            status__iexact="outstanding",
            date_issued__lt=slow_threshold,
            date_expired__gt=now,
        )
        .exclude(aasubsidy_meta__review_status__in=[-1, 1])
        .exclude(aasubsidy_meta__exempt=True)
        .exclude(aasubsidy_meta__paid=True)
        .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
        .order_by("date_issued")
    )
    slow_contracts_qs = apply_contract_exclusions(slow_contracts_qs, cfg_model)

    all_contracts = list(slow_contracts_qs)

    expired_q = (
        Q(status__iexact="deleted")
        | Q(status__iexact="expired")
        | Q(status__iexact="cancelled")
    )
    expired_contracts_qs = (
        CorporateContract.objects.filter(
            expired_q,
            corporation_id=corporation_id,
            date_expired__gte=expired_threshold,
            date_expired__lte=now,
        )
        .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
        .order_by("-date_expired")
    )
    expired_contracts_qs = apply_contract_exclusions(expired_contracts_qs, cfg_model)
    all_contracts += list(expired_contracts_qs)

    sold_contracts_qs = (
        CorporateContract.objects.filter(
            corporation_id=corporation_id,
            status__iexact="finished",
            date_issued__gte=sold_threshold,
        )
        .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
    )
    sold_contracts_qs = apply_contract_exclusions(sold_contracts_qs, cfg_model)
    all_contracts += list(sold_contracts_qs)

    contract_pks = [c.id for c in all_contracts]
    match_map = get_or_match_contracts(contract_pks, persist=True)
    contract_titles = {}
    contract_fit_pk = {}
    valid_contract_ids = set()
    fitting_name_map = {f["pk"]: f["name"] for f in Fitting.objects.values("pk", "name")}

    for c in all_contracts:
        result = match_map.get(c.id)
        if not result or result.match_status != "matched" or not result.matched_fitting_id:
            continue
        contract_titles[c.id] = result.matched_fitting_name or fitting_name_map.get(result.matched_fitting_id, "Unknown")
        contract_fit_pk[c.id] = int(result.matched_fitting_id)
        valid_contract_ids.add(c.id)

    def get_display_issuer(c):
        char_id = getattr(c.issuer_name, "eve_id", None)
        uid = _user_id_for_issuer_eve_id(char_id)
        fallback = getattr(c.issuer_name, "name", "Unknown")
        return _main_name_for_user_id(uid, fallback)

    slow_contracts = []
    for c in slow_contracts_qs:
        if c.id not in valid_contract_ids:
            continue
        slow_contracts.append(
            {
                "contract_id": c.contract_id,
                "issuer": get_display_issuer(c),
                "location": getattr(c.start_location_name, "location_name", "Unknown"),
                "date_issued": c.date_issued,
                "days_outstanding": (now - c.date_issued).days,
                "title": contract_titles.get(c.id, "No Title"),
                "price": c.price,
            }
        )

    expired_contracts = []
    for c in expired_contracts_qs:
        if c.id not in valid_contract_ids:
            continue
        expired_contracts.append(
            {
                "contract_id": c.contract_id,
                "issuer": get_display_issuer(c),
                "location": getattr(c.start_location_name, "location_name", "Unknown"),
                "date_expired": c.date_expired,
                "title": contract_titles.get(c.id, "No Title"),
                "price": c.price,
                "status": c.status,
            }
        )

    start = now - timezone.timedelta(days=365)
    end = now + timezone.timedelta(days=1)
    summary_data = doctrine_stock_summary(start, end, corporation_id=corporation_id)

    unfulfilled_doctrines = []
    for system in summary_data:
        for row in system["rows"]:
            if row["stock_needed"] > 0:
                unfulfilled_doctrines.append(
                    {
                        "system": system["system_name"],
                        "fit_name": row["fitting_name"],
                        "fit_id": row["fit_id"],
                        "requested": row["stock_requested"],
                        "available": row["stock_available"],
                        "needed": row["stock_needed"],
                        "fulfillment_pct": round(
                            (row["stock_available"] / row["stock_requested"] * 100), 1
                        )
                        if row["stock_requested"] > 0
                        else 0,
                    }
                )

    unfulfilled_doctrines.sort(key=lambda x: (-x["needed"], x["fulfillment_pct"]))

    fits_sold_counts = Counter()
    for c in sold_contracts_qs:
        fid = contract_fit_pk.get(c.id)
        if fid:
            fits_sold_counts[fid] += 1

    fit_to_doctrine = {}
    for system in summary_data:
        for row in system["rows"]:
            fid = row["fit_id"]
            if fid not in fit_to_doctrine:
                fit_to_doctrine[fid] = row["doctrine"]

    all_doctrines = list(Doctrine.objects.prefetch_related("fittings").all())
    fit_to_doctrines_list = defaultdict(list)
    for d in all_doctrines:
        for f in d.fittings.all():
            fit_to_doctrines_list[f.id].append(d)

    requested_by_fit = {
        r["fitting_id"]: int(r["total"] or 0)
        for r in FittingRequest.objects.values("fitting_id").annotate(total=Sum("requested"))
    }
    doctrine_sum_req = defaultdict(int)
    for d in all_doctrines:
        for f in d.fittings.all():
            doctrine_sum_req[d.id] += requested_by_fit.get(f.id, 0)

    def get_best_doctrine(fid):
        if fid in fit_to_doctrine:
            return fit_to_doctrine[fid]
        docs = fit_to_doctrines_list.get(fid, [])
        if not docs:
            return "No Doctrine"
        sorted_docs = sorted(docs, key=lambda doc: (-doctrine_sum_req[doc.id], doc.name))
        return sorted_docs[0].name

    fits_sold = []
    for fid, count in fits_sold_counts.items():
        fits_sold.append({
            "doctrine": get_best_doctrine(fid),
            "fit_name": fitting_name_map.get(fid, "Unknown"),
            "fit_id": fid,
            "count": count
        })
    fits_sold.sort(key=lambda x: (x["doctrine"], x["fit_name"]))

    stock_requirements = []
    all_relevant_fit_ids = set(fits_sold_counts.keys()) | set(requested_by_fit.keys())
    for fid in all_relevant_fit_ids:
        sold = fits_sold_counts.get(fid, 0)
        stock_requested = requested_by_fit.get(fid, 0)
        difference = stock_requested - sold
        if sold == 0 and stock_requested == 0:
            continue
        stock_requirements.append({
            "doctrine": get_best_doctrine(fid),
            "fit_name": fitting_name_map.get(fid, "Unknown"),
            "fit_id": fid,
            "sold": sold,
            "stock_requested": stock_requested,
            "difference": difference
        })
    stock_requirements.sort(key=lambda x: (x["doctrine"], x["fit_name"]))

    return {
        "slow_contracts": slow_contracts,
        "expired_contracts": expired_contracts,
        "unfulfilled_doctrines": unfulfilled_doctrines,
        "fits_sold": fits_sold,
        "stock_requirements": stock_requirements,
    }
