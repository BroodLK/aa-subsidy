from datetime import datetime
from django.db.models import F, Sum, Value, Case, When, IntegerField, OuterRef, Subquery, Exists, ExpressionWrapper, Count
from django.db.models.functions import Coalesce
from ..helpers.db import Ceil, Round
from fittings.models import Fitting, FittingItem
from eveuniverse.models import EveType
from corptools.models import CorporateContract, CorporateContractItem
from ..models import FittingRequest, SubsidyItemPrice, SubsidyConfig, FittingClaim
from decimal import Decimal
from django.db.models import DecimalField
from django.db import connection
from allianceauth.eveonline.models import EveCharacter
from allianceauth.authentication.models import CharacterOwnership

INCR = 250000

def _cfg():
    cfg = SubsidyConfig.active()
    return {
        "basis": cfg.price_basis,
        "pct": cfg.pct_over_basis,
        "m3": cfg.cost_per_m3,
        "incr": cfg.rounding_increment or INCR,
    }



def doctrine_stock_summary(start, end, corporation_id=1, status_filter=None):
    cfg = _cfg()
    incr_val = cfg["incr"]

    params = [start, end, 1, "outstanding"]
    where = "c.date_issued >= %s AND c.date_issued <= %s AND c.corporation_id = %s AND c.status = %s"

    sql = f"""
    WITH ci_sum AS (
      SELECT c.id AS contract_pk, ci.type_name_id AS type_id, SUM(ci.quantity) AS total_qty
      FROM corptools_corporatecontractitem ci
      JOIN corptools_corporatecontract c ON c.id = ci.contract_id
      WHERE {where}
      GROUP BY c.id, ci.type_name_id
    ),
    match_fitting AS (
      SELECT f.id AS fit_id, MIN(cs.contract_pk) AS contract_pk
      FROM fittings_fitting f
      JOIN fittings_fittingitem fi ON fi.fit_id = f.id
      JOIN ci_sum cs ON cs.type_id = fi.type_id AND cs.total_qty >= fi.quantity
      GROUP BY f.id, cs.contract_pk
      HAVING COUNT(*) = (SELECT COUNT(*) FROM fittings_fittingitem fi2 WHERE fi2.fit_id = f.id)
    )
    SELECT fit_id, COUNT(DISTINCT contract_pk) AS cnt
    FROM match_fitting
    GROUP BY fit_id
    """
    with connection.cursor() as cur:
        cur.execute(sql, params)
        match_counts = dict(cur.fetchall())

    fi = FittingItem.objects.filter(fit_id=OuterRef("pk")).values("fit_id")
    DEC_0 = DecimalField(max_digits=30, decimal_places=0)
    DEC_2 = DecimalField(max_digits=30, decimal_places=2)
    DEC_4 = DecimalField(max_digits=30, decimal_places=4)

    price_field = "sell" if cfg["basis"] == "sell" else "buy"

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

    qs = (
        Fitting.objects.filter(
            Exists(FittingRequest.objects.filter(fitting_id=OuterRef("pk"), requested__gt=0))
        )
        .annotate(
            items_sell_isk_raw=Coalesce(Subquery(items_sell, output_field=DEC_2), Value(Decimal("0"), output_field=DEC_2)),
            items_volume_m3=Coalesce(Subquery(items_volume, output_field=DEC_4), Value(Decimal("0"), output_field=DEC_4)),
            ship_sell_isk_raw=Coalesce(ship_sell, Value(Decimal("0"), output_field=DEC_2)),
            ship_volume_m3=Coalesce(ship_vol, Value(Decimal("0"), output_field=DEC_4)),
        )
        .annotate(
            items_sell_isk=ExpressionWrapper(
                Ceil(
                    ExpressionWrapper(F("items_sell_isk_raw") / Value(incr_val, output_field=DEC_2), output_field=DEC_2),
                    output_field=DEC_0,
                )
                * Value(incr_val, output_field=DEC_2),
                output_field=DEC_2,
            ),
            ship_sell_isk=ExpressionWrapper(
                Ceil(
                    ExpressionWrapper(F("ship_sell_isk_raw") / Value(incr_val, output_field=DEC_2), output_field=DEC_2),
                    output_field=DEC_0,
                )
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
            ),
            alliance_purchase_isk=ExpressionWrapper(
                Ceil(
                    ExpressionWrapper(
                        Round(
                            ExpressionWrapper(
                                F("jita_sell_isk")
                                + (F("jita_sell_isk") * Value(cfg["pct"], output_field=DEC_2))
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
            ),
        )
        .annotate(stock_requested=Coalesce(Subquery(FittingRequest.objects.filter(fitting_id=OuterRef("pk")).values("requested")[:1]), Value(0)))
        .values(
            "pk",
            "name",
            "stock_requested",
            "total_vol",
            "jita_sell_isk",
            "subsidy_isk",
            "alliance_purchase_isk",
        )
        .order_by("name")
    )

    fit_ids = [r["pk"] for r in qs]
    claims_by_fit = {fid: 0 for fid in fit_ids}
    my_claims_by_fit = {fid: 0 for fid in fit_ids}
    from django.contrib.auth import get_user_model
    User = get_user_model()
    req_user_id = getattr(doctrine_stock_summary, "request_user_id", None)

    all_claims = FittingClaim.objects.filter(fitting_id__in=fit_ids).values("fitting_id").annotate(total=Sum("quantity"))
    for c in all_claims:
        claims_by_fit[c["fitting_id"]] = int(c["total"] or 0)

    if req_user_id:
        my_claims = (
            FittingClaim.objects.filter(fitting_id__in=fit_ids, user_id=req_user_id)
            .values("fitting_id")
            .annotate(total=Sum("quantity"))
        )
        for mc in my_claims:
            my_claims_by_fit[mc["fitting_id"]] = int(mc["total"] or 0)

    rows = []
    for r in qs:
        available = int(match_counts.get(r["pk"], 0))
        needed = max(int(r["stock_requested"] or 0) - available, 0)
        claimed_total = int(claims_by_fit.get(r["pk"], 0))
        claimed_by_me = int(my_claims_by_fit.get(r["pk"], 0))
        adj_needed = max(needed - claimed_total, 0)

        claimant_pairs = []
        for c in FittingClaim.objects.filter(fitting_id=r["pk"]).select_related("user"):
            main_char = None
            try:
                first_char = EveCharacter.objects.filter(character_ownership__user=c.user).first()
                if first_char:
                    main_char = first_char.character_ownership.user.profile.main_character
            except Exception:
                main_char = None
            display_name = getattr(main_char, "character_name", None) or c.user.username
            claimant_pairs.append(f"{display_name} ({c.quantity})")
        claimants_str = ", ".join(claimant_pairs)

        rows.append(
            {
                "fit_id": r["pk"],
                "doctrine": r["name"],
                "stock_requested": int(r["stock_requested"] or 0),
                "stock_available": available,
                "stock_needed": needed,
                "claimed_total": claimed_total,
                "claimed_by_me": claimed_by_me,
                "adjusted_needed": adj_needed,
                "claimants": claimants_str,
                "volume_m3": round(r["total_vol"] or 0, 2),
                "jita_sell_isk": int(r["jita_sell_isk"] or 0),
                "subsidy_isk": int(r["subsidy_isk"] or 0),
                "alliance_purchase_isk": int(r["alliance_purchase_isk"] or 0),
            }
        )
    return rows
