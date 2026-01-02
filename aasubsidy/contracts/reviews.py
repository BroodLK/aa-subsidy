from datetime import datetime
from typing import Iterable
from django.db.models import F, Sum, Value, OuterRef, Subquery, Exists, ExpressionWrapper, Q
from django.db.models.functions import Coalesce
from fittings.models import Fitting, FittingItem
from corptools.models import CorporateContract, CorporateContractItem
from .summaries import INCR
from ..helpers.db import Ceil, Round
from ..models import SubsidyItemPrice, SubsidyConfig, CorporateContractSubsidy
from decimal import Decimal
from django.db.models import DecimalField
from eveuniverse.models import EveType
from ..models import FittingRequest
from allianceauth.eveonline.models import EveCharacter
from allianceauth.authentication.models import CharacterOwnership
from eveuniverse.models import EveEntity


def _cfg():
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or INCR,
        "corporation_id": cfg.corporation_id,
    }

def _matched_fit_names_for_contract(contract_pk: int) -> list[str]:
    ci_for_contract_type = (
        CorporateContractItem.objects.filter(contract_id=contract_pk, type_name_id=OuterRef("type_id"), is_included=True)
        .values("type_name_id")
        .annotate(q=Sum("quantity"))
        .values("q")
    )
    missing_item = (
        FittingItem.objects.filter(fit_id=OuterRef("pk"))
        .annotate(have_qty=Coalesce(Subquery(ci_for_contract_type), Value(0)))
        .filter(have_qty__lt=F("quantity"))
    )
    
    # Check if hull is present
    ci_for_hull = (
        CorporateContractItem.objects.filter(contract_id=contract_pk, type_name_id=OuterRef("ship_type_type_id"), is_included=True)
        .values("type_name_id")
        .annotate(q=Sum("quantity"))
        .values("q")
    )
    hull_check = Coalesce(Subquery(ci_for_hull), Value(0))

    return list(
        Fitting.objects.annotate(has_missing=Exists(missing_item), hull_qty=hull_check)
        .filter(has_missing=False, hull_qty__gte=1)
        .values_list("name", flat=True)
        .order_by("name")
    )

def _display_issuer_name(entity_name: str) -> str:
    try:
        ent = EveEntity.objects.filter(name=entity_name).values("id").first()
        if not ent:
            return entity_name
        char = EveCharacter.objects.filter(character_id=ent.get("id")).select_related("character_ownership__user__profile").first()
        if not char or not getattr(char, "character_ownership", None):
            return entity_name
        main_char = char.character_ownership.user.profile.main_character
        main_name = getattr(main_char, "character_name", None)
        if main_name and main_name != entity_name:
            return f"{main_name} ({entity_name})"
        return entity_name
    except Exception:
        return entity_name

def _bulk_display_issuer_names(entity_names: Iterable[str]) -> dict[str, str]:
    names = list(set(entity_names))
    entities = EveEntity.objects.filter(name__in=names).values("name", "id")
    entity_map = {e["name"]: e for e in entities}
    
    ids = {e["id"] for e in entities}
    all_target_ids = ids
    
    chars = EveCharacter.objects.filter(
        character_id__in=all_target_ids
    ).select_related("character_ownership__user__profile__main_character")
    
    char_map = {}
    for char in chars:
        char_map[char.character_id] = char
        
    results = {}
    for name in names:
        ent = entity_map.get(name)
        if not ent:
            results[name] = name
            continue
        
        char = char_map.get(ent["id"])
        if not char or not getattr(char, "character_ownership", None):
            results[name] = name
            continue
            
        main_char = char.character_ownership.user.profile.main_character
        main_name = getattr(main_char, "character_name", None)
        if main_name and main_name != name:
            results[name] = f"{main_name} ({name})"
        else:
            results[name] = name
    return results

def reviewer_table(start: datetime, end: datetime, corporation_id: int | None = None):
    cfg = _cfg()
    if corporation_id is None:
        corporation_id = cfg.get("corporation_id")
    DEC_0 = DecimalField(max_digits=30, decimal_places=0)
    DEC_2 = DecimalField(max_digits=30, decimal_places=2)
    DEC_4 = DecimalField(max_digits=30, decimal_places=4)
    price_field = "sell" if cfg["basis"] == "sell" else "buy"
    incr_val = cfg["incr"] or INCR
    safe_incr_val = Decimal(str(incr_val)) if Decimal(str(incr_val or 0)) != 0 else Decimal(str(INCR))

    base_subsidies = (
        CorporateContractSubsidy.objects
        .select_related(
            "contract__issuer_name",
            "contract__start_location_name",
            "contract",
            "forced_fitting"
        )
        .filter(
            contract__date_issued__gte=start,
            contract__date_issued__lte=end,
        )
    )
    if corporation_id is not None:
        base_subsidies = base_subsidies.filter(contract__corporation_id=corporation_id)

    base_contracts = CorporateContract.objects.filter(
        pk__in=base_subsidies.values("contract_id"),
        corporation_id=corporation_id if corporation_id is not None else F("corporation_id"),
    )

    ci_qty = (
        CorporateContractItem.objects
        .filter(contract_id=OuterRef("pk"), type_name_id=OuterRef("type_id"), is_included=True)
        .values("type_name_id")
        .annotate(q=Sum("quantity"))
        .values("q")
    )
    missing_item = (
        FittingItem.objects
        .filter(fit_id=OuterRef("fit_id"))
        .annotate(have_qty=Coalesce(Subquery(ci_qty), Value(0)))
        .filter(have_qty__lt=F("quantity"))
    )

    # Check if hull is present
    ci_for_hull_subq = (
        CorporateContractItem.objects.filter(contract_id=OuterRef("pk"), type_name_id=OuterRef("ship_type_type_id"), is_included=True)
        .values("type_name_id")
        .annotate(q=Sum("quantity"))
        .values("q")
    )
    hull_check_subq = Coalesce(Subquery(ci_for_hull_subq), Value(0))

    matching_fit_ids = (
        Fitting.objects
        .annotate(fit_id=F("pk"), hull_qty=hull_check_subq)
        .annotate(has_missing=Exists(missing_item))
        .filter(has_missing=False, hull_qty__gte=1)
        .values("pk")
    )

    fi_for_fit = FittingItem.objects.filter(fit_id=OuterRef("pk")).values("fit_id")

    items_basis_fit = (
        fi_for_fit.annotate(
            line_val=Coalesce(
                F("quantity") * Coalesce(
                    Subquery(
                        SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("type_id")).values(price_field)[:1],
                        output_field=DEC_2,
                    ),
                    Value(Decimal("0"), output_field=DEC_2),
                ),
                Value(Decimal("0"), output_field=DEC_2),
            )
        ).values("fit_id").annotate(total=Sum("line_val", output_field=DEC_2)).values("total")
    )
    items_volume_fit = (
        fi_for_fit.annotate(
            line_vol=Coalesce(
                F("quantity") * Coalesce(
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
        ).values("fit_id").annotate(total=Sum("line_vol", output_field=DEC_4)).values("total")
    )

    ship_basis_fit = Subquery(
        SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("ship_type_type_id")).values(price_field)[:1],
        output_field=DEC_2,
    )
    ship_vol_fit = Subquery(
        EveType.objects.filter(id=OuterRef("ship_type_type_id"))
        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
        .values("eff_vol")[:1],
        output_field=DEC_4,
    )

    round_items = ExpressionWrapper(
        Ceil(ExpressionWrapper(
            Coalesce(Subquery(items_basis_fit, output_field=DEC_2), Value(Decimal("0"), output_field=DEC_2))
            / Value(safe_incr_val, output_field=DEC_2), output_field=DEC_2
        ), output_field=DEC_0) * Value(safe_incr_val, output_field=DEC_2), output_field=DEC_2
    )
    round_ship = ExpressionWrapper(
        Ceil(ExpressionWrapper(
            Coalesce(ship_basis_fit, Value(Decimal("0"), output_field=DEC_2)) / Value(safe_incr_val, output_field=DEC_2),
            output_field=DEC_2
        ), output_field=DEC_0) * Value(safe_incr_val, output_field=DEC_2), output_field=DEC_2
    )
    fit_basis_total = ExpressionWrapper(round_items + round_ship, output_field=DEC_2)
    fit_total_vol = ExpressionWrapper(
        Coalesce(Subquery(items_volume_fit, output_field=DEC_4), Value(Decimal("0"), output_field=DEC_4))
        + Coalesce(ship_vol_fit, Value(Decimal("0"), output_field=DEC_4)),
        output_field=DEC_4,
    )
    fit_suggested = ExpressionWrapper(
        Ceil(ExpressionWrapper(
            Round(ExpressionWrapper(
                (F("basis_total") * Value(cfg["pct"], output_field=DEC_2))
                + (F("total_vol") * Value(cfg["m3"], output_field=DEC_2)),
                output_field=DEC_2
            ), Value(2), output_field=DEC_2) / Value(safe_incr_val, output_field=DEC_2),
            output_field=DEC_2
        ), output_field=DEC_0) * Value(safe_incr_val, output_field=DEC_2),
        output_field=DEC_2,
    )

    per_contract_fit_calc = (
        Fitting.objects
        .filter(pk__in=Subquery(matching_fit_ids))
        .annotate(basis_total=fit_basis_total, total_vol=fit_total_vol)
        .annotate(suggested=fit_suggested)
        .values("pk", "basis_total", "suggested", "name", "total_vol")
        .order_by("basis_total")
    )

    min_basis_subq = Subquery(per_contract_fit_calc.values("basis_total")[:1], output_field=DEC_2)
    suggested_subq = Subquery(per_contract_fit_calc.values("suggested")[:1], output_field=DEC_2)
    best_fit_id_subq = Subquery(per_contract_fit_calc.values("pk")[:1])
    best_fit_name_subq = Subquery(per_contract_fit_calc.values("name")[:1])
    best_fit_vol_subq = Subquery(per_contract_fit_calc.values("total_vol")[:1], output_field=DEC_4)

    qs_list = (
        base_contracts
        .annotate(
            min_basis=min_basis_subq, 
            suggested_subsidy=suggested_subq,
            best_fit_id=best_fit_id_subq,
            best_fit_name=best_fit_name_subq,
            best_fit_vol=best_fit_vol_subq,
        )
        .select_related("issuer_name", "start_location_name__system", "aasubsidy_meta")
        .order_by("-date_issued")
        .values(
            "pk", "contract_id", "date_issued", "price", "status", "title", "start_location_id",
            "issuer_name__name", "start_location_name__location_name", "start_location_name__system__name",
            "aasubsidy_meta__review_status", "aasubsidy_meta__subsidy_amount",
            "aasubsidy_meta__reason", "aasubsidy_meta__paid",
            "aasubsidy_meta__forced_fitting_id",
            "min_basis", "suggested_subsidy",
            "best_fit_id", "best_fit_name", "best_fit_vol",
        )
        .distinct()
    )
    qs = list(qs_list)

    # Pre-calculate issuer display names
    issuer_names = [c["issuer_name__name"] for c in qs]
    display_name_map = _bulk_display_issuer_names(issuer_names)

    # Pre-calculate forced fit names
    forced_fit_ids = {c["aasubsidy_meta__forced_fitting_id"] for c in qs if c["aasubsidy_meta__forced_fitting_id"]}
    forced_fit_names = {f["pk"]: f["name"] for f in Fitting.objects.filter(pk__in=forced_fit_ids).values("pk", "name")}

    # Pre-calculate all requested fits subsidy info to avoid qfit in loop
    requested_fit_ids = set(FittingRequest.objects.values_list("fitting_id", flat=True))
    all_requested_fits_info = {}
    if requested_fit_ids:
        # We need a new fi_for_fit that doesn't use the same OuterRef if we're in a different context,
        # but here we can just reuse the logic.
        fits_with_info = Fitting.objects.filter(pk__in=requested_fit_ids).annotate(
            basis_total=fit_basis_total, 
            total_vol=fit_total_vol
        ).annotate(
            suggested=fit_suggested
        ).values("pk", "basis_total", "total_vol", "suggested")
        for f in fits_with_info:
            all_requested_fits_info[f["pk"]] = f

    # Matching fits for doctrine_html
    # To avoid N+1, we'll fetch all items and do it in Python
    contract_pks = [c["pk"] for c in qs]
    all_contract_items = CorporateContractItem.objects.filter(
        contract_id__in=contract_pks, is_included=True
    ).values("contract_id", "type_name_id", "quantity")
    
    contract_items_map = {}
    for item in all_contract_items:
        cid = item["contract_id"]
        if cid not in contract_items_map:
            contract_items_map[cid] = {}
        contract_items_map[cid][item["type_name_id"]] = contract_items_map[cid].get(item["type_name_id"], 0) + item["quantity"]
        
    all_fittings = Fitting.objects.all().values("pk", "name", "ship_type_type_id")
    all_fitting_items = FittingItem.objects.all().values("fit_id", "type_id", "quantity")
    fit_items_map = {}
    for item in all_fitting_items:
        fid = item["fit_id"]
        if fid not in fit_items_map:
            fit_items_map[fid] = {}
        fit_items_map[fid][item["type_id"]] = item["quantity"]
        
    def get_matches(contract_pk):
        c_items = contract_items_map.get(contract_pk, {})
        matches = []
        for f in all_fittings:
            f_items = fit_items_map.get(f["pk"], {})
            # Check hull
            if c_items.get(f["ship_type_type_id"], 0) < 1:
                continue
            # Check items
            possible = True
            for t_id, qty in f_items.items():
                if c_items.get(t_id, 0) < qty:
                    possible = False
                    break
            if possible:
                matches.append(f)
        return sorted(matches, key=lambda x: x["name"])

    rows = []
    for c in qs:
        basis_val = float(c["min_basis"] or 0.0)
        contract_price = float(c["price"] or 0.0)
        pct_jita = round((contract_price / basis_val) * 100, 2) if basis_val else 0.0
        review_status = {1: "Approved", -1: "Rejected"}.get(c["aasubsidy_meta__review_status"], "Pending")

        forced_id = c.get("aasubsidy_meta__forced_fitting_id") or None
        if forced_id:
            fit_name = forced_fit_names.get(forced_id) or "Forced Doctrine"
            doctrine_html = fit_name + " (forced)"
            matched_fit_id = forced_id
        else:
            matches = get_matches(c["pk"])
            doctrine_html = "<br>".join([m["name"] for m in matches])
            matched_fit_id = c.get("best_fit_id")

        jita_sell_isk = 0.00
        total_vol = 0.00
        suggested = float(c.get("suggested_subsidy") or 0.0)

        if matched_fit_id is not None and matched_fit_id in requested_fit_ids:
            fit_info = all_requested_fits_info.get(matched_fit_id)
            if fit_info:
                jita_sell_isk = float(fit_info["basis_total"] or 0.0)
                suggested = float(fit_info["suggested"] or 0.0)
                total_vol = float(fit_info["total_vol"] or 0.0)
                pct_jita = round(((jita_sell_isk / contract_price) * 100), 2) if contract_price else 0.0

        prefill_subsidy = float(c["aasubsidy_meta__subsidy_amount"] or 0.0) or suggested
        station_val = (
            c["start_location_name__location_name"] or
            c["start_location_name__system__name"] or
            "Unknown"
        )

        issuer_display = display_name_map.get(c["issuer_name__name"], c["issuer_name__name"])
        rows.append({
            "id": c["contract_id"],
            "issuer": issuer_display,
            "date_issued": c["date_issued"],
            "price_listed": int(contract_price),
            "pct_jita": pct_jita,
            "status": c["status"],
            "title": c["title"] or "",
            "station": station_val,
            "doctrine": doctrine_html,
            "review_status": review_status,
            "subsidy_amount": prefill_subsidy,
            "reason": c["aasubsidy_meta__reason"] or "",
            "status_num": 1 if review_status == "Approved" else (-1 if review_status == "Rejected" else 0),
            "paid": c["aasubsidy_meta__paid"],
            "basis_isk": round(basis_val, 2),
            "suggested_subsidy": round(suggested, 2),
        })
    return rows
