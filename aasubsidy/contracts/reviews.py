from datetime import datetime
from decimal import Decimal
from typing import Iterable

from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from allianceauth.eveonline.models import EveCharacter
from eveuniverse.models import EveEntity, EveType
from fittings.models import Fitting, FittingItem
from corptools.models import CorporateContract

from .matching import match_contracts
from .summaries import INCR
from ..helpers.db import Ceil, Round
from ..models import CorporateContractSubsidy, SubsidyConfig, SubsidyItemPrice


def _cfg():
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or INCR,
        "corporation_id": cfg.corporation_id,
    }


def _bulk_display_issuer_names(entity_names: Iterable[str]) -> dict[str, str]:
    names = list(set(entity_names))
    entities = EveEntity.objects.filter(name__in=names).values("name", "id")
    entity_map = {e["name"]: e for e in entities}

    chars = EveCharacter.objects.filter(
        character_id__in={e["id"] for e in entities}
    ).select_related("character_ownership__user__profile__main_character")
    char_map = {char.character_id: char for char in chars}

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
        results[name] = f"{main_name} ({name})" if main_name and main_name != name else name
    return results


def _match_source_label(source: str) -> str:
    return {
        "auto": "Exact",
        "learned_rule": "Rule",
        "forced": "Forced",
        "manual_accept": "One-off",
    }.get(source, source.replace("_", " ").title())


def reviewer_table(start: datetime, end: datetime, corporation_id: int | None = None):
    cfg = _cfg()
    if corporation_id is None:
        corporation_id = cfg.get("corporation_id")

    dec_0 = DecimalField(max_digits=30, decimal_places=0)
    dec_2 = DecimalField(max_digits=30, decimal_places=2)
    dec_4 = DecimalField(max_digits=30, decimal_places=4)
    price_field = "sell" if cfg["basis"] == "sell" else "buy"
    incr_val = cfg["incr"] or INCR
    safe_incr_val = Decimal(str(incr_val)) if Decimal(str(incr_val or 0)) != 0 else Decimal(str(INCR))

    base_subsidies = CorporateContractSubsidy.objects.select_related(
        "contract__issuer_name",
        "contract__start_location_name",
        "contract",
        "forced_fitting",
    ).filter(
        contract__date_issued__gte=start,
        contract__date_issued__lte=end,
    )
    if corporation_id is not None:
        base_subsidies = base_subsidies.filter(contract__corporation_id=corporation_id)

    base_contracts = CorporateContract.objects.filter(
        pk__in=base_subsidies.values("contract_id"),
        corporation_id=corporation_id if corporation_id is not None else F("corporation_id"),
    )

    fi_for_fit = FittingItem.objects.filter(fit_id=OuterRef("pk")).values("fit_id")
    items_basis_fit = (
        fi_for_fit.annotate(
            line_val=Coalesce(
                F("quantity")
                * Coalesce(
                    Subquery(
                        SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("type_id")).values(price_field)[:1],
                        output_field=dec_2,
                    ),
                    Value(Decimal("0"), output_field=dec_2),
                ),
                Value(Decimal("0"), output_field=dec_2),
            )
        )
        .values("fit_id")
        .annotate(total=Sum("line_val", output_field=dec_2))
        .values("total")
    )
    items_volume_fit = (
        fi_for_fit.annotate(
            line_vol=Coalesce(
                F("quantity")
                * Coalesce(
                    Subquery(
                        EveType.objects.filter(id=OuterRef("type_id"))
                        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
                        .values("eff_vol")[:1],
                        output_field=dec_4,
                    ),
                    Value(Decimal("0"), output_field=dec_4),
                ),
                Value(Decimal("0"), output_field=dec_4),
            )
        )
        .values("fit_id")
        .annotate(total=Sum("line_vol", output_field=dec_4))
        .values("total")
    )

    ship_basis_fit = Subquery(
        SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("ship_type_type_id")).values(price_field)[:1],
        output_field=dec_2,
    )
    ship_vol_fit = Subquery(
        EveType.objects.filter(id=OuterRef("ship_type_type_id"))
        .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
        .values("eff_vol")[:1],
        output_field=dec_4,
    )

    round_items = ExpressionWrapper(
        Ceil(
            ExpressionWrapper(
                Coalesce(Subquery(items_basis_fit, output_field=dec_2), Value(Decimal("0"), output_field=dec_2))
                / Value(safe_incr_val, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_val, output_field=dec_2),
        output_field=dec_2,
    )
    round_ship = ExpressionWrapper(
        Ceil(
            ExpressionWrapper(
                Coalesce(ship_basis_fit, Value(Decimal("0"), output_field=dec_2))
                / Value(safe_incr_val, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_val, output_field=dec_2),
        output_field=dec_2,
    )
    fit_basis_total = ExpressionWrapper(round_items + round_ship, output_field=dec_2)
    fit_total_vol = ExpressionWrapper(
        Coalesce(Subquery(items_volume_fit, output_field=dec_4), Value(Decimal("0"), output_field=dec_4))
        + Coalesce(ship_vol_fit, Value(Decimal("0"), output_field=dec_4)),
        output_field=dec_4,
    )
    fit_suggested = ExpressionWrapper(
        Ceil(
            ExpressionWrapper(
                Round(
                    ExpressionWrapper(
                        (F("basis_total") * Value(cfg["pct"], output_field=dec_2))
                        + (F("total_vol") * Value(cfg["m3"], output_field=dec_2)),
                        output_field=dec_2,
                    ),
                    Value(2),
                    output_field=dec_2,
                )
                / Value(safe_incr_val, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_val, output_field=dec_2),
        output_field=dec_2,
    )

    contracts = list(
        base_contracts.select_related("issuer_name", "start_location_name__system", "aasubsidy_meta")
        .order_by("-date_issued")
        .values(
            "pk",
            "contract_id",
            "date_issued",
            "price",
            "status",
            "title",
            "issuer_name__name",
            "start_location_name__location_name",
            "start_location_name__system__name",
            "aasubsidy_meta__review_status",
            "aasubsidy_meta__subsidy_amount",
            "aasubsidy_meta__reason",
            "aasubsidy_meta__paid",
        )
        .distinct()
    )

    issuer_names = [contract["issuer_name__name"] for contract in contracts]
    display_name_map = _bulk_display_issuer_names(issuer_names)

    all_fittings_info = {
        row["pk"]: row
        for row in Fitting.objects.annotate(basis_total=fit_basis_total, total_vol=fit_total_vol)
        .annotate(suggested=fit_suggested)
        .values("pk", "name", "basis_total", "total_vol", "suggested")
    }

    match_map = match_contracts([contract["pk"] for contract in contracts], persist=True)

    rows = []
    for contract in contracts:
        result = match_map.get(contract["pk"])
        contract_price = float(contract["price"] or 0.0)
        review_status = {1: "Approved", -1: "Rejected"}.get(contract["aasubsidy_meta__review_status"], "Pending")

        evidence = result.evidence if result else {}
        candidate_summaries = evidence.get("candidates", [])
        candidate_names = [candidate["fit_name"] for candidate in candidate_summaries[:3]]
        selected_fit_id = result.matched_fitting_id if result else None
        pricing_fit_id = selected_fit_id or evidence.get("selected_fit_id")
        basis_val = 0.0
        suggested = 0.0
        if pricing_fit_id and pricing_fit_id in all_fittings_info:
            info = all_fittings_info[pricing_fit_id]
            basis_val = float(info["basis_total"] or 0.0)
            suggested = float(info["suggested"] or 0.0)

        match_source = result.match_source if result else "auto"
        source_label = _match_source_label(match_source)
        warning_count = len(result.warnings) if result else 0
        hard_failure_count = len(result.hard_failures) if result else 0
        selected_name = result.matched_fitting_name if result and result.matched_fitting_name else "No Match"
        alt_candidates = [name for name in candidate_names if name != selected_name]
        doctrine_html = (
            f'<div class="fw-semibold">{selected_name}</div>'
            f'<div class="small text-muted">'
            f'<span class="badge text-bg-secondary">{source_label}</span>'
            f' <span>{result.match_status.replace("_", " ").title() if result else "Rejected"}</span>'
            f"</div>"
        )
        if alt_candidates:
            doctrine_html += f'<div class="small text-muted">Also: {", ".join(alt_candidates)}</div>'

        pct_jita = round((contract_price / basis_val) * 100, 2) if (basis_val > 0 and contract_price > 0) else 0.0
        prefill_subsidy = float(contract["aasubsidy_meta__subsidy_amount"] or 0.0) or suggested
        station_val = (
            contract["start_location_name__location_name"]
            or contract["start_location_name__system__name"]
            or "Unknown"
        )
        issuer_display = display_name_map.get(contract["issuer_name__name"], contract["issuer_name__name"])

        rows.append(
            {
                "id": contract["contract_id"],
                "issuer": issuer_display,
                "date_issued": contract["date_issued"],
                "price_listed": int(contract_price),
                "pct_jita": pct_jita,
                "status": contract["status"],
                "title": contract["title"] or "",
                "station": station_val,
                "doctrine": doctrine_html,
                "review_status": review_status,
                "subsidy_amount": prefill_subsidy,
                "reason": contract["aasubsidy_meta__reason"] or "",
                "status_num": 1 if review_status == "Approved" else (-1 if review_status == "Rejected" else 0),
                "paid": contract["aasubsidy_meta__paid"],
                "basis_isk": round(basis_val, 2),
                "suggested_subsidy": round(suggested, 2),
                "match_score": float(result.score) if result else 0.0,
                "match_source": source_label,
                "match_status": result.match_status.replace("_", " ").title() if result else "Rejected",
                "warning_count": warning_count,
                "hard_failure_count": hard_failure_count,
            }
        )
    return rows
