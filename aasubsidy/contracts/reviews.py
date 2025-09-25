from datetime import datetime
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
    }

def _matched_fit_names_for_contract(contract_pk: int) -> list[str]:
    ci_for_contract_type = (
        CorporateContractItem.objects.filter(contract_id=contract_pk, type_name_id=OuterRef("type_id"))
        .values("type_name_id")
        .annotate(q=Sum("quantity"))
        .values("q")
    )
    missing_item = (
        FittingItem.objects.filter(fit_id=OuterRef("pk"))
        .annotate(have_qty=Coalesce(Subquery(ci_for_contract_type), Value(0)))
        .filter(have_qty__lt=F("quantity"))
    )
    return list(
        Fitting.objects.annotate(has_missing=Exists(missing_item))
        .filter(has_missing=False)
        .values_list("name", flat=True)
        .order_by("name")
    )

def _display_issuer_name(entity_name: str) -> str:
    try:
        ent = EveEntity.objects.filter(name=entity_name).values("id", "eve_id").first()
        if not ent:
            return entity_name
        char = EveCharacter.objects.filter(eve_id=ent.get("eve_id")).select_related("character_ownership__user__profile").first()
        if not char:
            char = EveCharacter.objects.filter(eve_id=ent.get("id")).select_related("character_ownership__user__profile").first()
        if not char or not getattr(char, "character_ownership", None):
            return entity_name
        main_char = char.character_ownership.user.profile.main_character
        main_name = getattr(main_char, "character_name", None)
        if main_name and main_name != entity_name:
            return f"{main_name} ({entity_name})"
        return entity_name
    except Exception:
        return entity_name

def reviewer_table(start: datetime, end: datetime, corporation_id: int | None = 1):
    cfg = _cfg()
    DEC_0 = DecimalField(max_digits=30, decimal_places=0)
    DEC_2 = DecimalField(max_digits=30, decimal_places=2)
    DEC_4 = DecimalField(max_digits=30, decimal_places=4)
    price_field = "sell" if cfg["basis"] == "sell" else "buy"
    incr_val = cfg["incr"]

    base_subsidies = (
        CorporateContractSubsidy.objects
        .select_related(
            "contract__issuer_name",
            "contract__start_location_name",
            "contract",
        )
        .filter(
            contract__date_issued__gte=start,
            contract__date_issued__lte=end,
        )
    )
    if corporation_id is not None:
        base_subsidies = base_subsidies.filter(contract__corporation_id=corporation_id)

    # We still need to perform per-contract calculations; reference contracts by the subsidies' FK
    base_contracts = CorporateContract.objects.filter(pk__in=base_subsidies.values("contract_id"))

    ci_qty = (
        CorporateContractItem.objects
        .filter(contract_id=OuterRef("pk"), type_name_id=OuterRef("type_id"))
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

    matching_fit_ids = (
        Fitting.objects
        .annotate(fit_id=F("pk"))
        .annotate(has_missing=Exists(missing_item))
        .filter(has_missing=False)
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
            / Value(incr_val, output_field=DEC_2), output_field=DEC_2
        ), output_field=DEC_0) * Value(incr_val, output_field=DEC_2), output_field=DEC_2
    )
    round_ship = ExpressionWrapper(
        Ceil(ExpressionWrapper(
            Coalesce(ship_basis_fit, Value(Decimal("0"), output_field=DEC_2)) / Value(incr_val, output_field=DEC_2),
            output_field=DEC_2
        ), output_field=DEC_0) * Value(incr_val, output_field=DEC_2), output_field=DEC_2
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
            ), Value(2), output_field=DEC_2) / Value(incr_val, output_field=DEC_2),
            output_field=DEC_2
        ), output_field=DEC_0) * Value(incr_val, output_field=DEC_2),
        output_field=DEC_2,
    )

    per_contract_fit_calc = (
        Fitting.objects
        .filter(pk__in=Subquery(matching_fit_ids))               # fits that match THIS contract
        .annotate(basis_total=fit_basis_total, total_vol=fit_total_vol)
        .annotate(suggested=fit_suggested)
        .values("basis_total", "suggested")
        .order_by("basis_total")
    )

    min_basis_subq = Subquery(per_contract_fit_calc.values("basis_total")[:1], output_field=DEC_2)
    suggested_subq = Subquery(per_contract_fit_calc.values("suggested")[:1], output_field=DEC_2)

    qs = (
        base_contracts
        .annotate(min_basis=min_basis_subq, suggested_subsidy=suggested_subq)
        .select_related("issuer_name", "start_location_name", "aasubsidy_meta")
        .order_by("-date_issued")
        .values(
            "pk", "contract_id", "date_issued", "price", "status", "title",
            "issuer_name__name", "start_location_name__location_name",
            "aasubsidy_meta__review_status", "aasubsidy_meta__subsidy_amount",
            "aasubsidy_meta__reason", "aasubsidy_meta__paid",
            "min_basis", "suggested_subsidy",
        )
        .distinct()
    )

    rows = []
    for c in qs:
        basis_val = float(c["min_basis"] or 0.0)
        contract_price = float(c["price"] or 0.0)
        pct_jita = round((contract_price / basis_val) * 100, 2) if basis_val else 0.0
        review_status = {1: "Approved", -1: "Rejected"}.get(c["aasubsidy_meta__review_status"], "Pending")
        doctrine_names = _matched_fit_names_for_contract(c["pk"])
        matched_fit = None
        if doctrine_names:
            matched_fit = (
                Fitting.objects
                .annotate(fit_id=F("pk"))
                .annotate(has_missing=Exists(
                    FittingItem.objects
                    .filter(fit_id=OuterRef("fit_id"))
                    .annotate(have_qty=Coalesce(Subquery(
                        CorporateContractItem.objects
                        .filter(contract_id=c["pk"], type_name_id=OuterRef("type_id"))
                        .values("type_name_id").annotate(q=Sum("quantity")).values("q")
                    ), Value(0)))
                    .filter(have_qty__lt=F("quantity"))
                ))
                .filter(has_missing=False)
                .order_by("pk")
                .values_list("pk", flat=True)[:1]
            )
            matched_fit = list(matched_fit)
            matched_fit_id = matched_fit[0] if matched_fit else None
        else:
            matched_fit_id = None

        jita_sell_isk = 0.0
        total_vol = 0.0
        suggested = float(c.get("suggested_subsidy") or 0.0)

        if matched_fit_id is not None:
            fi = FittingItem.objects.filter(fit_id=OuterRef("pk")).values("fit_id")
            items_sell = (
                fi.annotate(
                    line_sell=Coalesce(
                        F("quantity")
                        * Coalesce(
                            Subquery(
                                SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("type_id")).values(price_field)[:1],
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
                SubsidyItemPrice.objects.filter(eve_type_id=OuterRef("ship_type_type_id")).values(price_field)[:1],
                output_field=DEC_2,
            )
            ship_vol = Subquery(
                EveType.objects.filter(id=OuterRef("ship_type_type_id"))
                .annotate(eff_vol=Coalesce(F("packaged_volume"), F("volume")))
                .values("eff_vol")[:1],
                output_field=DEC_4,
            )
            qfit = (
                Fitting.objects
                .filter(pk=matched_fit_id)
                .annotate(has_req=Exists(FittingRequest.objects.filter(fitting_id=OuterRef("pk"))))
                .filter(has_req=True)
                .annotate(
                    items_sell_isk_raw=Coalesce(Subquery(items_sell, output_field=DEC_2), Value(Decimal("0"), output_field=DEC_2)),
                    items_volume_m3=Coalesce(Subquery(items_volume, output_field=DEC_4), Value(Decimal("0"), output_field=DEC_4)),
                    ship_sell_isk_raw=Coalesce(ship_sell, Value(Decimal("0"), output_field=DEC_2)),
                    ship_volume_m3=Coalesce(ship_vol, Value(Decimal("0"), output_field=DEC_4)),
                )
                .annotate(
                    items_sell_isk=ExpressionWrapper(
                        Ceil(ExpressionWrapper(F("items_sell_isk_raw") / Value(incr_val, output_field=DEC_2), output_field=DEC_2), output_field=DEC_0)
                        * Value(incr_val, output_field=DEC_2),
                        output_field=DEC_2,
                    ),
                    ship_sell_isk=ExpressionWrapper(
                        Ceil(ExpressionWrapper(F("ship_sell_isk_raw") / Value(incr_val, output_field=DEC_2), output_field=DEC_2), output_field=DEC_0)
                        * Value(incr_val, output_field=DEC_2),
                        output_field=DEC_2,
                    ),
                )
                .annotate(
                    jita_sell_isk=ExpressionWrapper(F("items_sell_isk") + F("ship_sell_isk"), output_field=DEC_2),
                    total_vol=ExpressionWrapper(F("items_volume_m3") + F("ship_volume_m3"), output_field=DEC_4),
                )
                .annotate(
                    subsidy_isk=ExpressionWrapper(
                        Ceil(
                            ExpressionWrapper(
                                Round(
                                    ExpressionWrapper(
                                        (F("jita_sell_isk") * Value(cfg["pct"], output_field=DEC_2))
                                        + (F("total_vol") * Value(cfg["m3"], output_field=DEC_2)),
                                        output_field=DEC_2,
                                    ),
                                    Value(2),
                                    output_field=DEC_2,
                                )
                                / Value(incr_val, output_field=DEC_2),
                                output_field=DEC_2,
                            ),
                            output_field=DEC_0,
                        )
                        * Value(incr_val, output_field=DEC_2),
                        output_field=DEC_2,
                    )
                )
                .values("jita_sell_isk", "total_vol", "subsidy_isk")[:1]
            )
            qfit_row = list(qfit)
            if qfit_row:
                jita_sell_isk = float(qfit_row[0]["jita_sell_isk"] or 0.0)
                suggested = float(qfit_row[0]["subsidy_isk"] or 0.0)
                pct_jita = round((jita_sell_isk / int(contract_price)) * 100, 2)
            else:
                suggested = 0.0
                pct_jita = 0.0

        prefill_subsidy = float(c["aasubsidy_meta__subsidy_amount"] or 0.0) or suggested
        station_val = c["start_location_name__location_name"] or "Unknown"

        issuer_display = _display_issuer_name(c["issuer_name__name"])
        rows.append({
            "id": c["contract_id"],
            "issuer": issuer_display,
            "date_issued": c["date_issued"],
            "price_listed": int(contract_price),
            "pct_jita": pct_jita,
            "status": c["status"],
            "title": c["title"] or "",
            "station": station_val,
            "doctrine": "<br>".join(doctrine_names),
            "review_status": review_status,
            "subsidy_amount": prefill_subsidy,
            "reason": c["aasubsidy_meta__reason"] or "",
            "status_num": 1 if review_status == "Approved" else (-1 if review_status == "Rejected" else 0),
            "paid": c["aasubsidy_meta__paid"],
            "basis_isk": round(basis_val, 2),
            "suggested_subsidy": round(suggested, 2),
        })
    return rows
