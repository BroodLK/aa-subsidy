from collections import defaultdict
from typing import Dict, List, Tuple

from django.db.models import Sum, Q
from allianceauth.eveonline.models import EveCharacter
from allianceauth.authentication.models import CharacterOwnership
from eveuniverse.models import EveEntity
from corptools.models import CorporateContract

from ..models import CorporateContractSubsidy


def get_user_main_character_name_from_issuer_eve_id(issuer_eve_id: int) -> str | None:
    try:
        first_char = EveCharacter.objects.filter(eve_id=issuer_eve_id).select_related("character_ownership__user__profile").first()
        if not first_char or not getattr(first_char, "character_ownership", None):
            return None
        main_char = first_char.character_ownership.user.profile.main_character
        return getattr(main_char, "character_name", None) or first_char.name
    except Exception:
        return None

def _main_name_for_issuer_entity(issuer_entity_eve_id: int | None, issuer_entity_name: str) -> str:
    if issuer_entity_eve_id is None:
        return issuer_entity_name
    try:
        char = EveCharacter.objects.filter(eve_id=issuer_entity_eve_id).select_related("character_ownership__user__profile").first()
        if not char or not getattr(char, "character_ownership", None):
            return issuer_entity_name
        main = char.character_ownership.user.profile.main_character
        return getattr(main, "character_name", None) or issuer_entity_name
    except Exception:
        return issuer_entity_name

def aggregate_payments_to_main() -> Tuple[List[dict], Dict[str, int]]:
    """
    Aggregation rules:
    - Include only review_status=1 (approved).
    - Exclude exempt & unpaid from approved_unpaid.
    - Paid negatives (from exempt items flipped on payment) flow naturally in approved_paid and totals.
    - Provide extra context values per character for UI row:
        * unpaid_before_exempt (approved_unpaid before excluding exempt unpaid)
        * exempt_unpaid (sum of exempt & unpaid amounts; excluded from nets)
        * exempt_paid_negative_abs (absolute sum of negative paid amounts)
    """
    per_main: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "approved_unpaid": 0,
        "approved_paid": 0,
        "unpaid_before_exempt": 0,
        "exempt_unpaid": 0,
        "exempt_paid_negative_abs": 0,
    })

    # Base queryset over approved subsidies
    base_qs = (
        CorporateContractSubsidy.objects
        .select_related("contract__issuer_name")
        .filter(review_status=1)
        .values("contract__issuer_name__eve_id", "contract__issuer_name__name", "paid", "exempt")
        .annotate(total=Sum("subsidy_amount"))
    )

    for row in base_qs:
        issuer_eve_id = row.get("contract__issuer_name__eve_id")
        issuer_name = row.get("contract__issuer_name__name") or "Unknown"
        main_name = _main_name_for_issuer_entity(issuer_eve_id, issuer_name)

        paid = bool(row["paid"])
        exempt = bool(row["exempt"])
        amt = int(row["total"] or 0)

        if not paid:
            per_main[main_name]["unpaid_before_exempt"] += amt

        if not paid:
            if exempt:
                per_main[main_name]["exempt_unpaid"] += amt
            else:
                per_main[main_name]["approved_unpaid"] += amt
            continue

        per_main[main_name]["approved_paid"] += amt
        if exempt and amt < 0:
            per_main[main_name]["exempt_paid_negative_abs"] += abs(amt)

    rows = []
    totals = {
        "approved_unpaid": 0,
        "approved_paid": 0,
        "total_approved": 0,
    }

    for main_name in sorted(per_main.keys(), key=lambda x: x.lower()):
        approved_unpaid = per_main[main_name]["approved_unpaid"]
        approved_paid = per_main[main_name]["approved_paid"]
        total = approved_unpaid + approved_paid

        rows.append({
            "character": main_name,
            "approved_unpaid": approved_unpaid,
            "approved_paid": approved_paid,
            "total_approved": total,
            "unpaid_before_exempt": per_main[main_name]["unpaid_before_exempt"],
            "exempt_unpaid": per_main[main_name]["exempt_unpaid"],
            "exempt_paid_negative_abs": per_main[main_name]["exempt_paid_negative_abs"],
        })

        totals["approved_unpaid"] += approved_unpaid
        totals["approved_paid"] += approved_paid
        totals["total_approved"] += total
    return rows, totals

def mark_all_unpaid_for_main_as_paid(main_character_name: str) -> int:
    """
    Marks all approved & unpaid subsidies for a main as paid.
    Special rule: if a subsidy is exempt and currently positive, flip it to negative when marking as paid.
    """
    try:
        user_ids = list(
            CharacterOwnership.objects.filter(
                user__profile__main_character__character_name=main_character_name
            ).values_list("user_id", flat=True).distinct()
        )

        issuer_eve_ids: list[int] = []
        if user_ids:
            issuer_eve_ids = list(
                EveCharacter.objects.filter(
                    character_ownership__user_id__in=user_ids
                ).values_list("eve_id", flat=True)
            )

        contracts_qs = CorporateContract.objects.all()
        if issuer_eve_ids:
            contracts_qs = contracts_qs.filter(issuer_name__eve_id__in=issuer_eve_ids)
        else:
            contracts_qs = contracts_qs.filter(issuer_name__name__iexact=main_character_name)

        contract_pk_list = list(contracts_qs.values_list("pk", flat=True))
        if not contract_pk_list:
            return 0

        qs = CorporateContractSubsidy.objects.filter(
            review_status=1, paid=False, contract_id__in=contract_pk_list
        ).only("id", "subsidy_amount", "exempt")

        updated = 0
        ids_to_update: list[int] = []
        to_flip: list[CorporateContractSubsidy] = []
        for s in qs:
            ids_to_update.append(s.id)
            if s.exempt and s.subsidy_amount > 0:
                s.subsidy_amount = -s.subsidy_amount
                to_flip.append(s)

        if to_flip:
            for s in to_flip:
                s.paid = True
            CorporateContractSubsidy.objects.bulk_update(to_flip, ["subsidy_amount", "paid"])
            updated += len(to_flip)

            flipped_ids = {s.id for s in to_flip}
            ids_to_update = [i for i in ids_to_update if i not in flipped_ids]

        if ids_to_update:
            updated += int(
                CorporateContractSubsidy.objects.filter(id__in=ids_to_update).update(paid=True)
            )

        return int(updated)
    except Exception:
        return 0