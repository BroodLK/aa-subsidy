from collections import defaultdict
from typing import Dict, List, Tuple

from django.db.models import Sum, Q
from allianceauth.eveonline.models import EveCharacter
from allianceauth.authentication.models import CharacterOwnership
from corptools.models import CorporateContract

from ..models import CorporateContractSubsidy


def _user_id_for_issuer_eve_id(issuer_eve_id: int | None) -> int | None:
    if not issuer_eve_id:
        return None
    char = (
        EveCharacter.objects.filter(eve_id=issuer_eve_id)
        .select_related("character_ownership__user")
        .only("id", "eve_id")
        .first()
    )
    if not char or not getattr(char, "character_ownership", None):
        return None
    return getattr(char.character_ownership.user, "id", None)


def _main_name_for_user_id(user_id: int | None, fallback_name: str) -> str:
    """
    Resolve a user's main character name; fall back to provided name if unavailable.
    """
    if not user_id:
        return fallback_name
    any_char = (
        EveCharacter.objects.filter(character_ownership__user_id=user_id)
        .select_related("character_ownership__user__profile__main_character")
        .only("id")
        .first()
    )
    if not any_char or not getattr(any_char, "character_ownership", None):
        return fallback_name
    profile = getattr(any_char.character_ownership.user, "profile", None)
    main = getattr(profile, "main_character", None) if profile else None
    return getattr(main, "character_name", None) or fallback_name


def _all_character_eve_ids_for_user(user_id: int) -> List[int]:
    """
    Return all EveCharacter.eve_id values owned by a user.
    """
    return list(
        EveCharacter.objects.filter(character_ownership__user_id=user_id)
        .values_list("eve_id", flat=True)
    )


def aggregate_payments_to_main() -> Tuple[List[dict], Dict[str, int]]:
    """
    Build rows per user-main:
      - Find issuer.eve_id on each approved subsidy
      - Map to user_id
      - Aggregate across all issuers belonging to that user
      - Display the user's main character name
    Excludes exempt & unpaid from approved_unpaid.
    """
    per_user: Dict[int, Dict[str, int]] = defaultdict(lambda: {
        "approved_unpaid": 0,
        "approved_paid": 0,
        "unpaid_before_exempt": 0,
        "exempt_unpaid": 0,
        "exempt_paid_negative_abs": 0,
        "fallback_name": "Unknown",
    })

    # Summarize by contract issuer + paid/exempt
    base_qs = (
        CorporateContractSubsidy.objects
        .select_related("contract__issuer_name")
        .filter(review_status=1)
        .values("contract__issuer_name__eve_id", "contract__issuer_name__name", "paid", "exempt")
        .annotate(total=Sum("subsidy_amount"))
    )

    user_ids_seen: set[int] = set()

    for row in base_qs:
        issuer_eve_id = row.get("contract__issuer_name__eve_id")
        issuer_name = row.get("contract__issuer_name__name") or "Unknown"
        user_id = _user_id_for_issuer_eve_id(issuer_eve_id)

        # If no owner link, bucket under a synthetic user_id using negative hash to avoid collision
        if user_id is None:
            synthetic_key = -abs(hash(issuer_name))
            user_id = synthetic_key

        user_bucket = per_user[user_id]
        if user_bucket["fallback_name"] == "Unknown":
            user_bucket["fallback_name"] = issuer_name

        paid = bool(row["paid"])
        exempt = bool(row["exempt"])
        amt = int(row["total"] or 0)

        if not paid:
            user_bucket["unpaid_before_exempt"] += amt
            if exempt:
                user_bucket["exempt_unpaid"] += amt
            else:
                user_bucket["approved_unpaid"] += amt
        else:
            user_bucket["approved_paid"] += amt
            if exempt and amt < 0:
                user_bucket["exempt_paid_negative_abs"] += abs(amt)

        user_ids_seen.add(user_id)

    rows: List[dict] = []
    totals = {"approved_unpaid": 0, "approved_paid": 0, "total_approved": 0}

    def display_name_for_user(uid: int, fallback: str) -> str:
        if uid < 0:
            return fallback
        return _main_name_for_user_id(uid, fallback)

    for uid in sorted(per_user.keys(), key=lambda k: display_name_for_user(k, per_user[k]["fallback_name"]).lower()):
        b = per_user[uid]
        display_name = display_name_for_user(uid, b["fallback_name"])
        approved_unpaid = b["approved_unpaid"]
        approved_paid = b["approved_paid"]
        total = approved_unpaid + approved_paid

        rows.append({
            "character": display_name,
            "approved_unpaid": approved_unpaid,
            "approved_paid": approved_paid,
            "total_approved": total,
            "unpaid_before_exempt": b["unpaid_before_exempt"],
            "exempt_unpaid": b["exempt_unpaid"],
            "exempt_paid_negative_abs": b["exempt_paid_negative_abs"],
        })

        totals["approved_unpaid"] += approved_unpaid
        totals["approved_paid"] += approved_paid
        totals["total_approved"] += total

    return rows, totals


def mark_all_unpaid_for_main_as_paid(main_character_name: str) -> int:

    try:
        # Resolve user_id(s) with this main name
        user_ids = list(
            CharacterOwnership.objects.filter(
                user__profile__main_character__character_name=main_character_name
            ).values_list("user_id", flat=True).distinct()
        )
        if not user_ids:
            return 0

        # Collect all character ids for those users
        all_eve_ids = list(
            EveCharacter.objects.filter(character_ownership__user_id__in=user_ids)
            .values_list("eve_id", flat=True)
        )
        if not all_eve_ids:
            return 0

        # All relevant contracts for these characters as issuers
        contract_ids = list(
            CorporateContract.objects.filter(issuer_name__eve_id__in=all_eve_ids)
            .values_list("pk", flat=True)
        )
        if not contract_ids:
            return 0

        qs = CorporateContractSubsidy.objects.filter(
            review_status=1,
            paid=False,
            contract_id__in=contract_ids,
        ).only("id", "subsidy_amount", "exempt")

        to_flip: List[CorporateContractSubsidy] = []
        ids_to_update: List[int] = []
        for s in qs:
            # Non-exempt: mark as paid
            # Exempt: if positive, flip to negative then mark as paid
            if s.exempt and s.subsidy_amount > 0:
                s.subsidy_amount = -s.subsidy_amount
                s.paid = True
                to_flip.append(s)
            else:
                ids_to_update.append(s.id)

        updated = 0
        if to_flip:
            CorporateContractSubsidy.objects.bulk_update(to_flip, ["subsidy_amount", "paid"])
            updated += len(to_flip)
        if ids_to_update:
            updated += int(
                CorporateContractSubsidy.objects.filter(id__in=ids_to_update).update(paid=True)
            )
        return int(updated)
    except Exception:
        return 0