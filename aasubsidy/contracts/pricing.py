from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from eveuniverse.models import EveType
from fittings.models import Fitting, FittingItem

from ..helpers.db import Ceil, Round
from ..models import SubsidyConfig, SubsidyItemPrice


DEFAULT_ROUNDING_INCREMENT = 250_000


def get_active_pricing_config() -> dict[str, Decimal | int | str | None]:
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or DEFAULT_ROUNDING_INCREMENT,
        "corporation_id": cfg.corporation_id,
    }


def get_fitting_pricing_map(fit_ids: Iterable[int]) -> dict[int, dict[str, object]]:
    fit_ids = sorted({int(fit_id) for fit_id in fit_ids if fit_id})
    if not fit_ids:
        return {}

    cfg = get_active_pricing_config()
    price_field = "sell" if cfg["basis"] == "sell" else "buy"
    incr_value = Decimal(str(cfg["incr"] or DEFAULT_ROUNDING_INCREMENT))
    safe_incr_value = incr_value if incr_value != 0 else Decimal(str(DEFAULT_ROUNDING_INCREMENT))

    dec_0 = DecimalField(max_digits=30, decimal_places=0)
    dec_2 = DecimalField(max_digits=30, decimal_places=2)
    dec_4 = DecimalField(max_digits=30, decimal_places=4)

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
                / Value(safe_incr_value, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_value, output_field=dec_2),
        output_field=dec_2,
    )
    round_ship = ExpressionWrapper(
        Ceil(
            ExpressionWrapper(
                Coalesce(ship_basis_fit, Value(Decimal("0"), output_field=dec_2))
                / Value(safe_incr_value, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_value, output_field=dec_2),
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
                / Value(safe_incr_value, output_field=dec_2),
                output_field=dec_2,
            ),
            output_field=dec_0,
        )
        * Value(safe_incr_value, output_field=dec_2),
        output_field=dec_2,
    )

    return {
        row["pk"]: row
        for row in Fitting.objects.filter(pk__in=fit_ids)
        .annotate(basis_total=fit_basis_total, total_vol=fit_total_vol)
        .annotate(suggested=fit_suggested)
        .values("pk", "name", "basis_total", "total_vol", "suggested")
    }
