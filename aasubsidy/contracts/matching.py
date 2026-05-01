from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable


MAX_SCORE = Decimal("100.00")
ZERO = Decimal("0.00")
MATCH_ENGINE_VERSION = 7


@dataclass(slots=True)
class TypeInfo:
    type_id: int
    name: str
    group_id: int | None = None
    market_group_id: int | None = None
    meta_level: int | None = None
    meta_group_id: int | None = None
    faction: bool = False


@dataclass(slots=True)
class ContractItemData:
    type_id: int
    name: str
    included_qty: int = 0
    excluded_qty: int = 0
    group_id: int | None = None
    market_group_id: int | None = None
    meta_level: int | None = None
    meta_group_id: int | None = None
    faction: bool = False


@dataclass(slots=True)
class MatchProfileData:
    fitting_id: int
    enabled: bool = True
    auto_match_threshold: Decimal = Decimal("95.00")
    review_threshold: Decimal = Decimal("80.00")
    allow_extra_items: bool = True
    allow_meta_variants: bool = False
    allow_faction_variants: bool = False
    notes: str = ""


@dataclass(slots=True)
class ItemRuleData:
    expected_type_id: int
    expected_type_name: str
    rule_kind: str = "required"
    quantity_mode: str = "exact"
    expected_quantity: int = 1
    min_quantity: int = 0
    max_quantity: int = 0
    category: str = "module"
    slot_label: str = ""
    sort_order: int = 0
    is_hull: bool = False


@dataclass(slots=True)
class SubstitutionRuleData:
    expected_type_id: int
    rule_type: str = "specific"
    allowed_type_id: int | None = None
    max_meta_level_delta: int = 0
    same_slot_only: bool = True
    same_group_only: bool = True
    penalty_points: Decimal = ZERO
    notes: str = ""


@dataclass(slots=True)
class QuantityToleranceData:
    eve_type_id: int
    mode: str = "absolute"
    lower_bound: int = 0
    upper_bound: int = 0
    penalty_points: Decimal = ZERO


@dataclass(slots=True)
class FittingDefinition:
    fitting_id: int
    fitting_name: str
    ship_type_id: int
    ship_type_name: str
    profile: MatchProfileData
    item_rules: list[ItemRuleData]
    substitutions: list[SubstitutionRuleData] = field(default_factory=list)
    quantity_tolerances: dict[int, list[QuantityToleranceData]] = field(default_factory=dict)
    type_info: dict[int, TypeInfo] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateMatch:
    fitting_id: int
    fitting_name: str
    score: Decimal
    exact_match: bool
    hard_failures: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    evidence: dict[str, Any]
    source_hint: str
    auto_threshold: Decimal
    review_threshold: Decimal

    @property
    def viable(self) -> bool:
        return not self.hard_failures and self.score >= self.review_threshold

    @property
    def auto_match(self) -> bool:
        return not self.hard_failures and self.score >= self.auto_threshold


@dataclass(slots=True)
class MatchResultData:
    contract_id: int
    matched_fitting_id: int | None
    matched_fitting_name: str | None
    match_source: str
    match_status: str
    score: Decimal
    hard_failures: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    evidence: dict[str, Any]


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:
        return ZERO


def _min_required(rule: ItemRuleData) -> int:
    if rule.quantity_mode == "range":
        return max(rule.min_quantity, 0)
    if rule.quantity_mode == "minimum":
        return max(rule.expected_quantity, rule.min_quantity, 0)
    return max(rule.expected_quantity, 0)


def _preferred_quantity(rule: ItemRuleData) -> int:
    if rule.quantity_mode == "range":
        return max(rule.expected_quantity, rule.min_quantity, 0)
    if rule.quantity_mode == "minimum":
        return max(rule.expected_quantity, rule.min_quantity, 0)
    return max(rule.expected_quantity, 0)


def _max_allowed(rule: ItemRuleData) -> int | None:
    if rule.quantity_mode == "range":
        return rule.max_quantity if rule.max_quantity > 0 else None
    if rule.quantity_mode == "minimum":
        return None
    return max(rule.expected_quantity, 0)


def _issue(level: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    payload = {"level": level, "code": code, "message": message}
    payload.update({k: v for k, v in details.items() if v is not None})
    return payload


def _row(
    *,
    expected_type_id: int | None,
    expected_name: str,
    actual_type_id: int | None = None,
    actual_name: str | None = None,
    actual_qty: int = 0,
    expected_qty: int | None = None,
    included_qty: int = 0,
    excluded_qty: int = 0,
    status: str = "ok",
    reason: str = "",
    is_missing: bool = False,
    actions: list[str] | None = None,
    category: str = "module",
    matched_type_ids: list[int] | None = None,
    matched_types: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": actual_name or expected_name,
        "type_id": int(actual_type_id or expected_type_id or 0),
        "expected_type_id": int(expected_type_id or 0) or None,
        "actual_type_id": int(actual_type_id or 0) or None,
        "qty": int(actual_qty),
        "included_qty": int(included_qty),
        "excluded_qty": int(excluded_qty),
        "expected_qty": expected_qty,
        "status": status,
        "reason": reason,
        "is_missing": is_missing,
        "actions": actions or [],
        "category": category,
        "matched_type_ids": matched_type_ids or [],
        "matched_types": matched_types or [],
    }


def _name_tokens(name: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(name or "").lower())


def _token_overlap_count(left: str | None, right: str | None) -> int:
    return len(set(_name_tokens(left)) & set(_name_tokens(right)))


def _type_info_from_contract_item(item: ContractItemData | None) -> TypeInfo | None:
    if item is None:
        return None
    return TypeInfo(
        type_id=int(item.type_id),
        name=item.name,
        group_id=item.group_id,
        market_group_id=item.market_group_id,
        meta_level=item.meta_level,
        meta_group_id=item.meta_group_id,
        faction=bool(item.faction),
    )


def _has_matching_action(
    actions: list[str | dict[str, Any]],
    *,
    action_name: str,
    expected_type_id: int | None,
    actual_type_id: int | None,
) -> bool:
    for action in actions:
        if isinstance(action, str):
            if action == action_name and expected_type_id is None and actual_type_id is None:
                return True
            continue
        if (
            action.get("name") == action_name
            and int(action.get("expected_type_id") or 0) == int(expected_type_id or 0)
            and int(action.get("actual_type_id") or 0) == int(actual_type_id or 0)
        ):
            return True
    return False


def _maybe_add_substitution_suggestions(
    item_rows: list[dict[str, Any]],
    *,
    contract_items: dict[int, ContractItemData],
    fitting: FittingDefinition,
) -> None:
    missing_rows = []
    extra_rows = []

    for row in item_rows:
        expected_type_id = int(row.get("expected_type_id") or 0) or None
        actual_type_id = int(row.get("actual_type_id") or row.get("type_id") or 0) or None
        actual_qty = int(row.get("qty") or 0)
        expected_qty = int(row.get("expected_qty") or 0)

        if expected_type_id and expected_qty > actual_qty:
            missing_rows.append(row)
        if not expected_type_id and actual_type_id and actual_qty > 0:
            extra_rows.append(row)

    for missing_row in missing_rows:
        expected_type_id = int(missing_row.get("expected_type_id") or 0) or None
        missing_qty = max(int(missing_row.get("expected_qty") or 0) - int(missing_row.get("qty") or 0), 0)
        if not expected_type_id or missing_qty <= 0:
            continue
        expected_info = fitting.type_info.get(
            expected_type_id,
            TypeInfo(type_id=expected_type_id, name=str(missing_row.get("name") or expected_type_id)),
        )

        candidates: list[tuple[int, int, int, dict[str, Any]]] = []

        for extra_row in extra_rows:
            actual_type_id = int(extra_row.get("actual_type_id") or extra_row.get("type_id") or 0) or None
            actual_qty = int(extra_row.get("qty") or 0)
            if not actual_type_id or actual_qty != missing_qty:
                continue
            actual_info = _type_info_from_contract_item(contract_items.get(actual_type_id))
            if actual_info is None:
                continue

            meta_rank = 0
            if expected_info.group_id and actual_info.group_id and expected_info.group_id == actual_info.group_id:
                meta_rank = 2
            elif (
                expected_info.market_group_id
                and actual_info.market_group_id
                and expected_info.market_group_id == actual_info.market_group_id
            ):
                meta_rank = 1
            if meta_rank <= 0:
                continue

            overlap = _token_overlap_count(missing_row.get("name"), extra_row.get("name"))
            candidates.append((meta_rank, overlap, actual_type_id, extra_row))

        if not candidates:
            continue

        candidates.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
        top_meta_rank, top_overlap, _, top_extra_row = candidates[0]
        if len(candidates) > 1:
            next_meta_rank, next_overlap, _, _ = candidates[1]
            if (top_meta_rank, top_overlap) == (next_meta_rank, next_overlap):
                continue

        actual_type_id = int(top_extra_row.get("actual_type_id") or top_extra_row.get("type_id") or 0) or None
        if not actual_type_id:
            continue

        missing_actions = missing_row.setdefault("actions", [])
        if not _has_matching_action(
            missing_actions,
            action_name="specific_substitute",
            expected_type_id=expected_type_id,
            actual_type_id=actual_type_id,
        ):
            missing_actions.append(
                {
                    "name": "specific_substitute",
                    "expected_type_id": expected_type_id,
                    "actual_type_id": actual_type_id,
                    "label": f"Allow Substitute: {top_extra_row.get('name')}",
                    "title": f"Allow {top_extra_row.get('name')} as a substitute for {missing_row.get('name')}.",
                }
            )

        extra_actions = top_extra_row.setdefault("actions", [])
        if not _has_matching_action(
            extra_actions,
            action_name="specific_substitute",
            expected_type_id=expected_type_id,
            actual_type_id=actual_type_id,
        ):
            extra_actions.append(
                {
                    "name": "specific_substitute",
                    "expected_type_id": expected_type_id,
                    "actual_type_id": actual_type_id,
                    "label": f"Allow Substitute: {missing_row.get('name')}",
                    "title": f"Allow {top_extra_row.get('name')} as a substitute for {missing_row.get('name')}.",
                }
            )


def _substitution_matches(
    rule: SubstitutionRuleData,
    *,
    expected: TypeInfo,
    actual: TypeInfo,
) -> bool:
    if rule.rule_type == "specific":
        return rule.allowed_type_id == actual.type_id
    if rule.rule_type == "group":
        return bool(expected.group_id and actual.group_id and expected.group_id == actual.group_id)
    if rule.rule_type == "market_group":
        return bool(
            expected.market_group_id
            and actual.market_group_id
            and expected.market_group_id == actual.market_group_id
        )
    if rule.rule_type == "meta_family":
        if not (expected.group_id and actual.group_id and expected.group_id == actual.group_id):
            return False
        if expected.meta_level is None or actual.meta_level is None:
            return False
        return abs(actual.meta_level - expected.meta_level) <= max(rule.max_meta_level_delta, 0)
    return False


def _implicit_substitution_penalty(
    profile: MatchProfileData,
    *,
    expected: TypeInfo,
    actual: TypeInfo,
) -> Decimal | None:
    if expected.type_id == actual.type_id:
        return ZERO
    if profile.allow_meta_variants:
        if (
            expected.group_id
            and actual.group_id
            and expected.group_id == actual.group_id
            and expected.meta_level is not None
            and actual.meta_level is not None
        ):
            return Decimal("5.00")
    if profile.allow_faction_variants:
        if expected.group_id and actual.group_id and expected.group_id == actual.group_id and actual.faction:
            return Decimal("4.00")
    return None


def _match_tolerance(
    tolerances: Iterable[QuantityToleranceData],
    *,
    actual_qty: int,
    preferred_qty: int,
) -> QuantityToleranceData | None:
    if preferred_qty < 0:
        return None
    best: QuantityToleranceData | None = None
    diff = actual_qty - preferred_qty
    for tolerance in tolerances:
        matched = False
        if tolerance.mode == "absolute":
            matched = tolerance.lower_bound <= diff <= tolerance.upper_bound
        elif tolerance.mode == "percent" and preferred_qty > 0:
            pct_diff = Decimal(diff) * Decimal("100") / Decimal(preferred_qty)
            matched = Decimal(str(tolerance.lower_bound)) <= pct_diff <= Decimal(str(tolerance.upper_bound))
        elif tolerance.mode == "missing_only" and diff < 0:
            missing = abs(diff)
            matched = tolerance.lower_bound <= missing <= tolerance.upper_bound
        elif tolerance.mode == "extra_only" and diff > 0:
            matched = tolerance.lower_bound <= diff <= tolerance.upper_bound
        if matched and (best is None or tolerance.penalty_points < best.penalty_points):
            best = tolerance
    return best


# Cache for market group ancestry checks
_CONSUMABLE_MARKET_GROUPS_CACHE: dict[int, bool] = {}
_CONSUMABLE_ROOT_GROUPS = {11, 157}  # Charges & Components (11), Drones (157)


def _is_consumable_market_group(market_group_id: int | None) -> bool:
    """
    Check if a market group ID is a consumable (ammo, drones, paste, boosters, scripts, etc.)
    by checking if it's market group 11, 157, or a descendant of those groups.
    Uses django-eveonline-sde's ItemMarketGroup model to traverse the hierarchy.
    """
    if market_group_id is None:
        return False

    if market_group_id in _CONSUMABLE_MARKET_GROUPS_CACHE:
        return _CONSUMABLE_MARKET_GROUPS_CACHE[market_group_id]

    # Check if it's one of the root groups
    if market_group_id in _CONSUMABLE_ROOT_GROUPS:
        _CONSUMABLE_MARKET_GROUPS_CACHE[market_group_id] = True
        return True

    # Traverse up the market group hierarchy using eve_sde
    try:
        from eve_sde.models import ItemMarketGroup

        current_id = market_group_id
        visited = set()
        max_depth = 20  # Prevent infinite loops
        depth = 0

        while current_id and depth < max_depth:
            if current_id in visited:
                break  # Circular reference protection
            visited.add(current_id)

            if current_id in _CONSUMABLE_ROOT_GROUPS:
                _CONSUMABLE_MARKET_GROUPS_CACHE[market_group_id] = True
                return True

            # Get parent market group
            try:
                market_group = ItemMarketGroup.objects.only('parent_group_id').get(pk=current_id)
                current_id = market_group.parent_group_id if hasattr(market_group, 'parent_group_id') else None
            except ItemMarketGroup.DoesNotExist:
                break

            depth += 1

        _CONSUMABLE_MARKET_GROUPS_CACHE[market_group_id] = False
        return False
    except Exception:
        # If we can't query the database, assume it's not a consumable
        return False


def evaluate_contract_against_definition(
    contract_items: dict[int, ContractItemData],
    fitting: FittingDefinition,
) -> CandidateMatch:
    """
    NEW ITEM-COUNT BASED SCORING SYSTEM

    Scoring logic:
    - Start with expected_items points (max score)
    - Hull mismatch = automatic 0%
    - Modules/fits count as 1 point each
    - Consumables (ammo/drones/paste/boosters via market groups 11/157) count as 1 stack each
    - Missing required module/consumable: -1 point
    - Extra unexpected module/consumable: -1 point
    - Wrong consumable quantity (±20%): -0.5 points
    - Substitution: -1 point
    - Final score = (expected_items - penalty_points) / expected_items × 100
    """
    remaining = Counter({
        type_id: int(item.included_qty)
        for type_id, item in contract_items.items()
        if int(item.included_qty or 0) > 0
    })

    hard_failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    exact_match = True
    used_learned_rule = False

    # Track points for item-count scoring (START WITH MAX POINTS, SUBTRACT PENALTIES)
    expected_items = 0  # Total items we expect (max possible points)
    penalty_points = Decimal("0.00")  # Total penalties to subtract

    substitution_rules_by_expected: dict[int, list[SubstitutionRuleData]] = defaultdict(list)
    for substitution in fitting.substitutions:
        substitution_rules_by_expected[substitution.expected_type_id].append(substitution)

    # Process each expected item in the fitting
    for rule in sorted(fitting.item_rules, key=lambda entry: (entry.sort_order, entry.expected_type_name.lower())):
        expected_type = fitting.type_info.get(
            rule.expected_type_id,
            TypeInfo(type_id=rule.expected_type_id, name=rule.expected_type_name),
        )
        contract_item = contract_items.get(rule.expected_type_id)
        included_qty = int(contract_item.included_qty) if contract_item else 0
        excluded_qty = int(contract_item.excluded_qty) if contract_item else 0
        exact_qty = int(remaining.get(rule.expected_type_id, 0))

        # Check if this is a consumable
        is_consumable = _is_consumable_market_group(expected_type.market_group_id)

        # Ignore rules don't count toward scoring
        if rule.rule_kind == "ignore":
            if exact_qty > 0:
                remaining[rule.expected_type_id] -= exact_qty
                if remaining[rule.expected_type_id] <= 0:
                    remaining.pop(rule.expected_type_id, None)
                used_learned_rule = True
                item_rows.append(_row(
                    expected_type_id=rule.expected_type_id,
                    expected_name=expected_type.name,
                    actual_qty=exact_qty,
                    included_qty=included_qty,
                    excluded_qty=excluded_qty,
                    expected_qty=rule.expected_quantity,
                    reason="Ignored by doctrine policy.",
                    category=rule.category,
                ))
            continue

        # Count this item in our expected total (modules and consumable stacks both = 1 point)
        expected_items += 1

        preferred_qty = _preferred_quantity(rule)
        minimum_qty = _min_required(rule)

        # Try to find substitutes if needed
        applied_substitutions: list[dict[str, Any]] = []
        substitute_qty = 0
        shortage_target = max(preferred_qty - exact_qty, 0) if preferred_qty > 0 else 0

        if shortage_target > 0:
            explicit_rules = sorted(
                substitution_rules_by_expected.get(rule.expected_type_id, []),
                key=lambda entry: (entry.penalty_points, entry.rule_type, entry.allowed_type_id or 0),
            )
            candidate_actual_ids = [
                type_id for type_id, qty in remaining.items()
                if qty > 0 and type_id != rule.expected_type_id
            ]
            for actual_type_id in candidate_actual_ids:
                if substitute_qty >= shortage_target:
                    break
                actual_info = fitting.type_info.get(actual_type_id) or _type_info_from_contract_item(contract_items.get(actual_type_id)) or TypeInfo(type_id=actual_type_id, name=str(actual_type_id))
                matched_rule = next((
                    sub_rule for sub_rule in explicit_rules
                    if _substitution_matches(sub_rule, expected=expected_type, actual=actual_info)
                ), None)
                implicit_penalty = _implicit_substitution_penalty(fitting.profile, expected=expected_type, actual=actual_info)

                if matched_rule is None and implicit_penalty is None:
                    continue

                available = int(remaining.get(actual_type_id, 0))
                use_qty = min(available, shortage_target - substitute_qty)
                if use_qty <= 0:
                    continue

                remaining[actual_type_id] -= use_qty
                if remaining[actual_type_id] <= 0:
                    remaining.pop(actual_type_id, None)
                substitute_qty += use_qty

                # Substitutions now cost -1 point
                penalty_points += Decimal("1.00")
                exact_match = False
                used_learned_rule = True

                applied_substitutions.append({
                    "type_id": actual_info.type_id,
                    "name": actual_info.name,
                    "qty": use_qty,
                    "penalty_points": 1.0,
                    "rule_type": matched_rule.rule_type if matched_rule else "profile_variant",
                })
                warnings.append(_issue(
                    "warning",
                    "substitution",
                    f"{expected_type.name} matched with {actual_info.name}.",
                    expected_type_id=expected_type.type_id,
                    actual_type_id=actual_info.type_id,
                    quantity=use_qty,
                    fitting_id=fitting.fitting_id,
                ))

        actual_qty = exact_qty + substitute_qty
        if exact_qty > 0:
            remaining[rule.expected_type_id] -= exact_qty
            if remaining[rule.expected_type_id] <= 0:
                remaining.pop(rule.expected_type_id, None)

        status = "ok"
        reason = ""
        actions: list[str] = []

        # Check for hull mismatch first (automatic fail)
        if rule.is_hull and actual_qty <= 0:
            if not any(item.get("code") == "missing_required" and item.get("expected_type_id") == expected_type.type_id for item in hard_failures):
                hard_failures.append(_issue(
                    "error",
                    "wrong_hull",
                    f"Expected hull {expected_type.name}, but it is missing.",
                    expected_type_id=expected_type.type_id,
                    fitting_id=fitting.fitting_id,
                ))
            status = "error"
            exact_match = False
            reason = "Wrong hull for doctrine."
            actions = []
            # Hull mismatch doesn't affect item count, score will be forced to 0 later

        # Optional items missing
        elif rule.rule_kind == "optional" and actual_qty <= 0:
            status = "warning"
            exact_match = False
            penalty_points += Decimal("1.00")  # -1 point for missing optional
            used_learned_rule = True
            reason = "Optional doctrine item is missing."
            warnings.append(_issue(
                "warning",
                "optional_missing",
                reason,
                expected_type_id=expected_type.type_id,
                fitting_id=fitting.fitting_id,
            ))

        # Required item missing
        elif actual_qty < minimum_qty:
            status = "error"
            exact_match = False
            penalty_points += Decimal("1.00")  # -1 point for missing required item
            reason = f"Expected at least {minimum_qty}, found {actual_qty}."
            hard_failures.append(_issue(
                "error",
                "missing_required",
                f"{expected_type.name}: {reason}",
                expected_type_id=expected_type.type_id,
                actual_qty=actual_qty,
                expected_qty=minimum_qty,
                fitting_id=fitting.fitting_id,
            ))
            if not rule.is_hull:
                if actual_qty == 0:
                    actions.append("optional_item")
                else:
                    actions.append("optional_item")
                    actions.append("quantity_tolerance")

        # Item present - check quantity for consumables
        elif actual_qty > 0:
            # For consumables, check if quantity is within ±20%
            if is_consumable and preferred_qty > 0:
                tolerance_pct = Decimal("0.20")  # 20%
                lower_bound = int(Decimal(preferred_qty) * (Decimal("1.00") - tolerance_pct))
                upper_bound = int(Decimal(preferred_qty) * (Decimal("1.00") + tolerance_pct))

                if actual_qty < lower_bound or actual_qty > upper_bound:
                    # Outside tolerance: -0.5 points
                    penalty_points += Decimal("0.50")
                    exact_match = False
                    status = "warning"
                    reason = f"Consumable quantity {actual_qty} outside ±20% of expected {preferred_qty}."
                    warnings.append(_issue(
                        "warning",
                        "consumable_quantity_tolerance",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    ))
                    actions.append("quantity_tolerance")

        if applied_substitutions and "specific_substitute" not in actions:
            actions.append("specific_substitute")

        item_rows.append(_row(
            expected_type_id=rule.expected_type_id,
            expected_name=expected_type.name,
            actual_qty=actual_qty,
            included_qty=included_qty,
            excluded_qty=excluded_qty,
            expected_qty=preferred_qty,
            status=status,
            reason=reason,
            is_missing=actual_qty <= 0 and minimum_qty > 0,
            actions=actions,
            category=rule.category,
            matched_type_ids=[item["type_id"] for item in applied_substitutions],
            matched_types=[item["name"] for item in applied_substitutions],
        ))

    # Handle extra unexpected items
    for actual_type_id, qty in list(remaining.items()):
        if qty <= 0:
            continue
        contract_item = contract_items.get(actual_type_id)
        actual_name = contract_item.name if contract_item else str(actual_type_id)
        actual_type_info = fitting.type_info.get(actual_type_id) or _type_info_from_contract_item(contract_item) or TypeInfo(type_id=actual_type_id, name=actual_name)
        is_consumable_extra = _is_consumable_market_group(actual_type_info.market_group_id)

        if fitting.profile.allow_extra_items:
            exact_match = False
            penalty_points += Decimal("1.00")  # -1 point for extra item
            warnings.append(_issue(
                "warning",
                "unexpected_extra_item",
                f"Extra item: {actual_name} (qty: {qty}).",
                actual_type_id=actual_type_id,
                actual_qty=qty,
                fitting_id=fitting.fitting_id,
            ))
            item_rows.append(_row(
                expected_type_id=None,
                expected_name=actual_name,
                actual_type_id=actual_type_id,
                actual_name=actual_name,
                actual_qty=qty,
                included_qty=int(contract_item.included_qty) if contract_item else qty,
                excluded_qty=int(contract_item.excluded_qty) if contract_item else 0,
                status="warning",
                reason="Unexpected extra item allowed by profile.",
                actions=["ignore_extra_item"],
            ))
        else:
            exact_match = False
            penalty_points += Decimal("1.00")  # -1 point for extra item
            hard_failures.append(_issue(
                "error",
                "unexpected_extra_item",
                f"Extra item not allowed: {actual_name} (qty: {qty}).",
                actual_type_id=actual_type_id,
                actual_qty=qty,
                fitting_id=fitting.fitting_id,
            ))
            item_rows.append(_row(
                expected_type_id=None,
                expected_name=actual_name,
                actual_type_id=actual_type_id,
                actual_name=actual_name,
                actual_qty=qty,
                included_qty=int(contract_item.included_qty) if contract_item else qty,
                excluded_qty=int(contract_item.excluded_qty) if contract_item else 0,
                status="error",
                reason="Unexpected extra item is not allowed by profile.",
                actions=["ignore_extra_item"],
            ))

    # Calculate final score using item-count method
    # Score = (expected_items - penalty_points) / expected_items × 100
    has_wrong_hull = any(failure.get("code") == "wrong_hull" for failure in hard_failures)
    if has_wrong_hull:
        score = ZERO
    elif expected_items == 0:
        score = MAX_SCORE  # No items expected, perfect match
    else:
        points_earned = Decimal(expected_items) - penalty_points
        score = (points_earned / Decimal(expected_items)) * Decimal("100.00")
        score = max(score, ZERO).quantize(Decimal("0.01"))

    source_hint = "auto"
    if used_learned_rule or not exact_match:
        source_hint = "learned_rule"

    evidence = {
        "selected_fit_id": fitting.fitting_id,
        "selected_fit_name": fitting.fitting_name,
        "profile": {
            "auto_match_threshold": float(fitting.profile.auto_match_threshold),
            "review_threshold": float(fitting.profile.review_threshold),
            "allow_extra_items": fitting.profile.allow_extra_items,
        },
        "item_rows": item_rows,
        "substitutions": [warning for warning in warnings if warning.get("code") == "substitution"],
        "scoring_details": {
            "expected_items": expected_items,
            "penalty_points": float(penalty_points),
            "points_earned": float(Decimal(expected_items) - penalty_points),
        },
    }
    _maybe_add_substitution_suggestions(item_rows, contract_items=contract_items, fitting=fitting)

    return CandidateMatch(
        fitting_id=fitting.fitting_id,
        fitting_name=fitting.fitting_name,
        score=score,
        exact_match=exact_match,
        hard_failures=hard_failures,
        warnings=warnings,
        evidence=evidence,
        source_hint=source_hint,
        auto_threshold=fitting.profile.auto_match_threshold,
        review_threshold=fitting.profile.review_threshold,
    )


def evaluate_contract_against_definition_OLD_QUANTITY_BASED(
    contract_items: dict[int, ContractItemData],
    fitting: FittingDefinition,
) -> CandidateMatch:
    remaining = Counter(
        {
            type_id: int(item.included_qty)
            for type_id, item in contract_items.items()
            if int(item.included_qty or 0) > 0
        }
    )
    score = MAX_SCORE
    hard_failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    exact_match = True
    used_learned_rule = False
    substitution_rules_by_expected: dict[int, list[SubstitutionRuleData]] = defaultdict(list)
    for substitution in fitting.substitutions:
        substitution_rules_by_expected[substitution.expected_type_id].append(substitution)

    for rule in sorted(fitting.item_rules, key=lambda entry: (entry.sort_order, entry.expected_type_name.lower())):
        expected_type = fitting.type_info.get(
            rule.expected_type_id,
            TypeInfo(type_id=rule.expected_type_id, name=rule.expected_type_name),
        )
        contract_item = contract_items.get(rule.expected_type_id)
        included_qty = int(contract_item.included_qty) if contract_item else 0
        excluded_qty = int(contract_item.excluded_qty) if contract_item else 0
        exact_qty = int(remaining.get(rule.expected_type_id, 0))

        if rule.rule_kind == "ignore":
            if exact_qty > 0:
                remaining[rule.expected_type_id] -= exact_qty
                if remaining[rule.expected_type_id] <= 0:
                    remaining.pop(rule.expected_type_id, None)
                used_learned_rule = True
                item_rows.append(
                    _row(
                        expected_type_id=rule.expected_type_id,
                        expected_name=expected_type.name,
                        actual_qty=exact_qty,
                        included_qty=included_qty,
                        excluded_qty=excluded_qty,
                        expected_qty=rule.expected_quantity,
                        reason="Ignored by doctrine policy.",
                        category=rule.category,
                    )
                )
            continue

        preferred_qty = _preferred_quantity(rule)
        minimum_qty = _min_required(rule)
        maximum_qty = _max_allowed(rule)

        applied_substitutions: list[dict[str, Any]] = []
        substitute_qty = 0
        shortage_target = max(preferred_qty - exact_qty, 0)

        if shortage_target > 0:
            explicit_rules = sorted(
                substitution_rules_by_expected.get(rule.expected_type_id, []),
                key=lambda entry: (entry.penalty_points, entry.rule_type, entry.allowed_type_id or 0),
            )
            candidate_actual_ids = [
                type_id
                for type_id, qty in remaining.items()
                if qty > 0 and type_id != rule.expected_type_id
            ]
            for actual_type_id in candidate_actual_ids:
                if substitute_qty >= shortage_target:
                    break
                actual_info = fitting.type_info.get(actual_type_id, TypeInfo(type_id=actual_type_id, name=str(actual_type_id)))
                matched_rule = next(
                    (
                        sub_rule
                        for sub_rule in explicit_rules
                        if _substitution_matches(sub_rule, expected=expected_type, actual=actual_info)
                    ),
                    None,
                )
                implicit_penalty = _implicit_substitution_penalty(
                    fitting.profile,
                    expected=expected_type,
                    actual=actual_info,
                )
                if matched_rule is None and implicit_penalty is None:
                    continue
                available = int(remaining.get(actual_type_id, 0))
                use_qty = min(available, shortage_target - substitute_qty)
                if use_qty <= 0:
                    continue
                remaining[actual_type_id] -= use_qty
                if remaining[actual_type_id] <= 0:
                    remaining.pop(actual_type_id, None)
                substitute_qty += use_qty
                penalty = matched_rule.penalty_points if matched_rule else implicit_penalty or ZERO
                if penalty > 0:
                    score -= penalty
                exact_match = False
                used_learned_rule = True
                applied_substitutions.append(
                    {
                        "type_id": actual_info.type_id,
                        "name": actual_info.name,
                        "qty": use_qty,
                        "penalty_points": float(penalty),
                        "rule_type": matched_rule.rule_type if matched_rule else "profile_variant",
                    }
                )
                warnings.append(
                    _issue(
                        "warning",
                        "substitution",
                        f"{expected_type.name} matched with {actual_info.name}.",
                        expected_type_id=expected_type.type_id,
                        actual_type_id=actual_info.type_id,
                        quantity=use_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )

        actual_qty = exact_qty + substitute_qty
        if exact_qty > 0:
            remaining[rule.expected_type_id] -= exact_qty
            if remaining[rule.expected_type_id] <= 0:
                remaining.pop(rule.expected_type_id, None)

        status = "ok"
        reason = ""
        actions: list[str] = []

        if rule.rule_kind == "optional" and actual_qty <= 0:
            status = "warning"
            exact_match = False
            score -= Decimal("1.00")
            used_learned_rule = True
            reason = "Optional doctrine item is missing."
            warnings.append(
                _issue(
                    "warning",
                    "optional_missing",
                    reason,
                    expected_type_id=expected_type.type_id,
                    fitting_id=fitting.fitting_id,
                )
            )
            # Don't add "optional_item" action - it's already optional!
        elif actual_qty < minimum_qty:
            status = "error"
            exact_match = False
            reason = f"Expected at least {minimum_qty}, found {actual_qty}."
            hard_failures.append(
                _issue(
                    "error",
                    "missing_required",
                    f"{expected_type.name}: {reason}",
                    expected_type_id=expected_type.type_id,
                    actual_qty=actual_qty,
                    expected_qty=minimum_qty,
                    fitting_id=fitting.fitting_id,
                )
            )
            if not rule.is_hull:
                # If item is completely missing, only offer "allow missing" option
                if actual_qty == 0:
                    actions.append("optional_item")
                else:
                    # If some quantity exists but not enough, offer both options
                    actions.append("optional_item")
                    actions.append("quantity_tolerance")
        elif maximum_qty is not None and actual_qty > maximum_qty:
            tolerance = _match_tolerance(
                fitting.quantity_tolerances.get(rule.expected_type_id, []),
                actual_qty=actual_qty,
                preferred_qty=preferred_qty,
            )
            if tolerance:
                penalty = tolerance.penalty_points or Decimal("2.00")
                score -= penalty
                exact_match = False
                used_learned_rule = True
                status = "warning"
                reason = f"Quantity differs from preferred amount ({preferred_qty}) but is inside tolerance."
                warnings.append(
                    _issue(
                        "warning",
                        "quantity_tolerance",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")
            elif fitting.profile.allow_extra_items and rule.category in {"cargo", "ammo", "script", "drone"}:
                score -= Decimal("1.00")
                exact_match = False
                status = "warning"
                reason = f"Extra quantity above preferred amount ({preferred_qty})."
                warnings.append(
                    _issue(
                        "warning",
                        "extra_quantity",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")
            else:
                status = "error"
                exact_match = False
                reason = f"Expected at most {maximum_qty}, found {actual_qty}."
                hard_failures.append(
                    _issue(
                        "error",
                        "quantity_above_expected",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=maximum_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")
        elif actual_qty > preferred_qty and preferred_qty >= 0 and maximum_qty is None:
            # Minimum rules accept extras without penalty.
            reason = ""
        elif rule.quantity_mode == "exact" and actual_qty != preferred_qty:
            tolerance = _match_tolerance(
                fitting.quantity_tolerances.get(rule.expected_type_id, []),
                actual_qty=actual_qty,
                preferred_qty=preferred_qty,
            )
            if tolerance:
                penalty = tolerance.penalty_points or Decimal("2.00")
                score -= penalty
                exact_match = False
                used_learned_rule = True
                status = "warning"
                reason = f"Quantity differs from preferred amount ({preferred_qty}) but is inside tolerance."
                warnings.append(
                    _issue(
                        "warning",
                        "quantity_tolerance",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")
            elif actual_qty < preferred_qty and rule.rule_kind != "optional":
                status = "error"
                exact_match = False
                reason = f"Expected exactly {preferred_qty}, found {actual_qty}."
                hard_failures.append(
                    _issue(
                        "error",
                        "quantity_below_expected",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")
            elif actual_qty > preferred_qty:
                status = "error"
                exact_match = False
                reason = f"Expected exactly {preferred_qty}, found {actual_qty}."
                hard_failures.append(
                    _issue(
                        "error",
                        "quantity_above_expected",
                        f"{expected_type.name}: {reason}",
                        expected_type_id=expected_type.type_id,
                        actual_qty=actual_qty,
                        expected_qty=preferred_qty,
                        fitting_id=fitting.fitting_id,
                    )
                )
                actions.append("quantity_tolerance")

        if rule.is_hull and actual_qty <= 0:
            if not any(item.get("code") == "missing_required" and item.get("expected_type_id") == expected_type.type_id for item in hard_failures):
                hard_failures.append(
                    _issue(
                        "error",
                        "wrong_hull",
                        f"Expected hull {expected_type.name}, but it is missing.",
                        expected_type_id=expected_type.type_id,
                        fitting_id=fitting.fitting_id,
                    )
                )
            status = "error"
            exact_match = False
            reason = "Wrong hull for doctrine."
            actions = []

        if applied_substitutions and "specific_substitute" not in actions:
            actions.append("specific_substitute")

        item_rows.append(
            _row(
                expected_type_id=rule.expected_type_id,
                expected_name=expected_type.name,
                actual_qty=actual_qty,
                included_qty=included_qty,
                excluded_qty=excluded_qty,
                expected_qty=preferred_qty,
                status=status,
                reason=reason,
                is_missing=actual_qty <= 0 and minimum_qty > 0,
                actions=actions,
                category=rule.category,
                matched_type_ids=[item["type_id"] for item in applied_substitutions],
                matched_types=[item["name"] for item in applied_substitutions],
            )
        )

    for actual_type_id, qty in list(remaining.items()):
        if qty <= 0:
            continue
        contract_item = contract_items.get(actual_type_id)
        actual_name = contract_item.name if contract_item else str(actual_type_id)
        if fitting.profile.allow_extra_items:
            exact_match = False
            score -= Decimal("1.00")
            warnings.append(
                _issue(
                    "warning",
                    "unexpected_extra_item",
                    f"Extra item: {actual_name} (qty: {qty}).",
                    actual_type_id=actual_type_id,
                    actual_qty=qty,
                    fitting_id=fitting.fitting_id,
                )
            )
            item_rows.append(
                _row(
                    expected_type_id=None,
                    expected_name=actual_name,
                    actual_type_id=actual_type_id,
                    actual_name=actual_name,
                    actual_qty=qty,
                    included_qty=int(contract_item.included_qty) if contract_item else qty,
                    excluded_qty=int(contract_item.excluded_qty) if contract_item else 0,
                    status="warning",
                    reason="Unexpected extra item allowed by profile.",
                    actions=["ignore_extra_item"],
                )
            )
        else:
            exact_match = False
            hard_failures.append(
                _issue(
                    "error",
                    "unexpected_extra_item",
                    f"Extra item not allowed: {actual_name} (qty: {qty}).",
                    actual_type_id=actual_type_id,
                    actual_qty=qty,
                    fitting_id=fitting.fitting_id,
                )
            )
            item_rows.append(
                _row(
                    expected_type_id=None,
                    expected_name=actual_name,
                    actual_type_id=actual_type_id,
                    actual_name=actual_name,
                    actual_qty=qty,
                    included_qty=int(contract_item.included_qty) if contract_item else qty,
                    excluded_qty=int(contract_item.excluded_qty) if contract_item else 0,
                    status="error",
                    reason="Unexpected extra item is not allowed by profile.",
                    actions=["ignore_extra_item"],
                )
            )

    # Force score to 0 if hull doesn't match
    has_wrong_hull = any(failure.get("code") == "wrong_hull" for failure in hard_failures)
    if has_wrong_hull:
        score = ZERO
    else:
        score = max(score, ZERO).quantize(Decimal("0.01"))

    source_hint = "auto"
    if used_learned_rule or not exact_match:
        source_hint = "learned_rule"

    evidence = {
        "selected_fit_id": fitting.fitting_id,
        "selected_fit_name": fitting.fitting_name,
        "profile": {
            "auto_match_threshold": float(fitting.profile.auto_match_threshold),
            "review_threshold": float(fitting.profile.review_threshold),
            "allow_extra_items": fitting.profile.allow_extra_items,
        },
        "item_rows": item_rows,
        "substitutions": [warning for warning in warnings if warning.get("code") == "substitution"],
    }
    return CandidateMatch(
        fitting_id=fitting.fitting_id,
        fitting_name=fitting.fitting_name,
        score=score,
        exact_match=exact_match,
        hard_failures=hard_failures,
        warnings=warnings,
        evidence=evidence,
        source_hint=source_hint,
        auto_threshold=fitting.profile.auto_match_threshold,
        review_threshold=fitting.profile.review_threshold,
    )


def _select_result(
    *,
    contract_id: int,
    candidates: list[CandidateMatch],
    forced_fit_id: int | None = None,
    forced_fit_name: str | None = None,
    manual_decision: dict[str, Any] | None = None,
    close_match_threshold: Decimal = Decimal("70.00"),
) -> MatchResultData:
    candidate_by_fit = {candidate.fitting_id: candidate for candidate in candidates}

    if forced_fit_id:
        candidate = candidate_by_fit.get(forced_fit_id)
        if candidate is None:
            return MatchResultData(
                contract_id=contract_id,
                matched_fitting_id=forced_fit_id,
                matched_fitting_name=forced_fit_name,
                match_source="forced",
                match_status="needs_review",
                score=ZERO,
                hard_failures=[_issue("error", "invalid_forced_fit", "Forced doctrine could not be evaluated.")],
                warnings=[],
                evidence={"forced_fit_id": forced_fit_id, "candidates": _candidate_summaries(candidates)},
            )
        status = "matched" if candidate.auto_match else "needs_review"
        evidence = dict(candidate.evidence)
        evidence["forced_fit_id"] = forced_fit_id
        evidence["candidates"] = _candidate_summaries(candidates)
        return MatchResultData(
            contract_id=contract_id,
            matched_fitting_id=candidate.fitting_id,
            matched_fitting_name=candidate.fitting_name,
            match_source="forced",
            match_status=status,
            score=candidate.score,
            hard_failures=candidate.hard_failures,
            warnings=candidate.warnings,
            evidence=evidence,
        )

    if manual_decision and manual_decision.get("decision") == "accept_once":
        target_fit_id = manual_decision.get("fitting_id")
        candidate = candidate_by_fit.get(target_fit_id)
        if candidate is not None:
            evidence = dict(candidate.evidence)
            evidence["decision"] = manual_decision
            return MatchResultData(
                contract_id=contract_id,
                matched_fitting_id=candidate.fitting_id,
                matched_fitting_name=candidate.fitting_name,
                match_source="manual_accept",
                match_status="matched",
                score=max(candidate.score, candidate.review_threshold).quantize(Decimal("0.01")),
                hard_failures=candidate.hard_failures,
                warnings=candidate.warnings,
                evidence=evidence,
            )

    if manual_decision and manual_decision.get("decision") == "reject_once":
        return MatchResultData(
            contract_id=contract_id,
            matched_fitting_id=None,
            matched_fitting_name=None,
            match_source="auto",
            match_status="no_match",
            score=ZERO,
            hard_failures=[_issue("error", "manual_reject", "Manually marked as no match.")],
            warnings=[],
            evidence={"decision": manual_decision, "candidates": _candidate_summaries(candidates)},
        )
    viable = [candidate for candidate in candidates if candidate.viable]
    viable.sort(key=lambda candidate: (-candidate.score, candidate.fitting_name.lower(), candidate.fitting_id))

    if not viable:
        top_candidate = max(candidates, key=lambda candidate: (candidate.score, candidate.fitting_name.lower()), default=None)
        evidence = dict(getattr(top_candidate, "evidence", {}) or {})
        evidence["selected_fit_id"] = getattr(top_candidate, "fitting_id", None)
        evidence["selected_fit_name"] = getattr(top_candidate, "fitting_name", None)
        evidence["candidates"] = _candidate_summaries(candidates)

        if top_candidate and top_candidate.score >= close_match_threshold:
            return MatchResultData(
                contract_id=contract_id,
                matched_fitting_id=top_candidate.fitting_id,
                matched_fitting_name=top_candidate.fitting_name,
                match_source="auto",
                match_status="needs_review",
                score=top_candidate.score,
                hard_failures=[],
                warnings=top_candidate.warnings,
                evidence=evidence,
            )

        # Below close match threshold or has hard failures - no match
        return MatchResultData(
            contract_id=contract_id,
            matched_fitting_id=None,
            matched_fitting_name=None,
            match_source="auto",
            match_status="no_match",
            score=getattr(top_candidate, "score", ZERO),
            hard_failures=getattr(top_candidate, "hard_failures", []),
            warnings=getattr(top_candidate, "warnings", []),
            evidence=evidence,
        )

    selected = viable[0]
    ambiguous = len([candidate for candidate in viable if candidate.score == selected.score]) > 1
    match_status = "matched" if selected.auto_match and not ambiguous else "needs_review"
    warnings = list(selected.warnings)
    if ambiguous:
        warnings.append(
            _issue(
                "warning",
                "ambiguous_match",
                "Multiple fittings scored equally; review is required.",
                fitting_ids=[candidate.fitting_id for candidate in viable if candidate.score == selected.score],
            )
        )
    evidence = dict(selected.evidence)
    evidence["candidates"] = _candidate_summaries(candidates)
    if ambiguous or match_status != "matched":
        evidence["selected_fit_id"] = selected.fitting_id
        evidence["selected_fit_name"] = selected.fitting_name
    return MatchResultData(
        contract_id=contract_id,
        matched_fitting_id=selected.fitting_id,
        matched_fitting_name=selected.fitting_name,
        match_source=selected.source_hint,
        match_status=match_status,
        score=selected.score,
        hard_failures=selected.hard_failures,
        warnings=warnings,
        evidence=evidence,
    )


def _candidate_summaries(candidates: Iterable[CandidateMatch]) -> list[dict[str, Any]]:
    return [
        {
            "fit_id": candidate.fitting_id,
            "fit_name": candidate.fitting_name,
            "score": float(candidate.score),
            "hard_failure_count": len(candidate.hard_failures),
            "warning_count": len(candidate.warnings),
            "auto_match": candidate.auto_match,
            "viable": candidate.viable,
        }
        for candidate in sorted(candidates, key=lambda entry: (-entry.score, entry.fitting_name.lower(), entry.fitting_id))
    ]


def _model_refs():
    from django.utils import timezone
    from django.db.models import Sum
    from corptools.models import CorporateContract, CorporateContractItem
    from eveuniverse.models import EveType
    from fittings.models import Fitting, FittingItem

    from ..models import (
        CorporateContractSubsidy,
        DoctrineContractDecision,
        DoctrineItemRule,
        DoctrineMatchProfile,
        DoctrineMatchResult,
        DoctrineQuantityTolerance,
        DoctrineSubstitutionRule,
        SubsidyConfig,
    )

    return {
        "CorporateContract": CorporateContract,
        "CorporateContractItem": CorporateContractItem,
        "CorporateContractSubsidy": CorporateContractSubsidy,
        "DoctrineContractDecision": DoctrineContractDecision,
        "DoctrineItemRule": DoctrineItemRule,
        "DoctrineMatchProfile": DoctrineMatchProfile,
        "DoctrineMatchResult": DoctrineMatchResult,
        "DoctrineQuantityTolerance": DoctrineQuantityTolerance,
        "DoctrineSubstitutionRule": DoctrineSubstitutionRule,
        "EveType": EveType,
        "Fitting": Fitting,
        "FittingItem": FittingItem,
        "Sum": Sum,
        "SubsidyConfig": SubsidyConfig,
        "timezone": timezone,
    }


def _attr(obj: Any, names: tuple[str, ...], default=None):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _type_info_from_obj(obj: Any) -> TypeInfo:
    faction_hint = str(getattr(obj, "name", "") or "").lower()
    return TypeInfo(
        type_id=int(getattr(obj, "id")),
        name=getattr(obj, "name", str(getattr(obj, "id"))),
        group_id=_attr(obj, ("group_id", "eve_group_id", "group_fk_id")),
        market_group_id=_attr(obj, ("market_group_id", "eve_market_group_id")),
        meta_level=_attr(obj, ("meta_level",)),
        meta_group_id=_attr(obj, ("meta_group_id",)),
        faction=("faction" in faction_hint or "navy" in faction_hint),
    )


def _load_fit_definitions(fit_ids: Iterable[int]) -> dict[int, FittingDefinition]:
    refs = _model_refs()
    Fitting = refs["Fitting"]
    FittingItem = refs["FittingItem"]
    DoctrineMatchProfile = refs["DoctrineMatchProfile"]
    DoctrineItemRule = refs["DoctrineItemRule"]
    DoctrineSubstitutionRule = refs["DoctrineSubstitutionRule"]
    DoctrineQuantityTolerance = refs["DoctrineQuantityTolerance"]

    fit_ids = {int(fit_id) for fit_id in fit_ids if fit_id}
    if not fit_ids:
        return {}

    fit_rows = list(
        Fitting.objects.filter(pk__in=fit_ids)
        .select_related("ship_type")
        .order_by("name", "pk")
    )
    profile_map = {
        profile.fitting_id: profile
        for profile in DoctrineMatchProfile.objects.filter(fitting_id__in=fit_ids)
    }
    explicit_rules = list(
        DoctrineItemRule.objects.filter(profile__fitting_id__in=fit_ids)
        .select_related("eve_type", "profile")
        .order_by("sort_order", "id")
    )
    substitutions = list(
        DoctrineSubstitutionRule.objects.filter(profile__fitting_id__in=fit_ids)
        .select_related("expected_type", "allowed_type", "profile")
        .order_by("expected_type_id", "id")
    )
    tolerances = list(
        DoctrineQuantityTolerance.objects.filter(profile__fitting_id__in=fit_ids)
        .select_related("eve_type", "profile")
        .order_by("eve_type_id", "id")
    )
    default_fit_item_rows = list(
        FittingItem.objects.filter(fit_id__in=fit_ids)
        .select_related("type_fk")
        .order_by("fit_id", "type_fk__name", "id")
    )

    fit_items_by_fit: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in default_fit_item_rows:
        fit_id = int(row.fit_id)
        type_id = int(row.type_id)
        bucket = fit_items_by_fit[fit_id].get(type_id)
        if bucket is None:
            bucket = {
                "type_id": type_id,
                "type_obj": getattr(row, "type_fk", None),
                "total_qty": 0,
            }
            fit_items_by_fit[fit_id][type_id] = bucket
        bucket["total_qty"] += int(getattr(row, "quantity", 0) or 0)

    rules_by_fit: dict[int, list[ItemRuleData]] = defaultdict(list)
    type_info_by_fit: dict[int, dict[int, TypeInfo]] = defaultdict(dict)

    for fit in fit_rows:
        ship_type = getattr(fit, "ship_type", None)
        ship_name = getattr(ship_type, "name", None) or f"Hull {fit.ship_type_type_id}"
        type_info_by_fit[int(fit.pk)][int(fit.ship_type_type_id)] = TypeInfo(
            type_id=int(fit.ship_type_type_id),
            name=ship_name,
        )
        if fit.pk not in profile_map:
            profile_map[int(fit.pk)] = None
        rules_by_fit[int(fit.pk)].append(
            ItemRuleData(
                expected_type_id=int(fit.ship_type_type_id),
                expected_type_name=ship_name,
                rule_kind="required",
                quantity_mode="exact",
                expected_quantity=1,
                category="hull",
                sort_order=-1000,
                is_hull=True,
            )
        )

    for fit_id, items in fit_items_by_fit.items():
        for row in items.values():
            type_id = int(row["type_id"])
            type_obj = row.get("type_obj")
            name = getattr(type_obj, "name", None) or str(type_id)
            rules_by_fit[fit_id].append(
                ItemRuleData(
                    expected_type_id=type_id,
                    expected_type_name=name,
                    rule_kind="required",
                    quantity_mode="exact",
                    expected_quantity=int(row["total_qty"] or 0),
                    category="module",
                    sort_order=0,
                )
            )
            type_info_by_fit[fit_id][type_id] = _type_info_from_obj(type_obj) if type_obj is not None else TypeInfo(type_id=type_id, name=name)

    for rule in explicit_rules:
        fit_id = int(rule.profile.fitting_id)
        explicit_rule = ItemRuleData(
            expected_type_id=int(rule.eve_type_id),
            expected_type_name=getattr(rule.eve_type, "name", str(rule.eve_type_id)),
            rule_kind=rule.rule_kind,
            quantity_mode=rule.quantity_mode,
            expected_quantity=int(rule.expected_quantity or 0),
            min_quantity=int(rule.min_quantity or 0),
            max_quantity=int(rule.max_quantity or 0),
            category=rule.category,
            slot_label=rule.slot_label,
            sort_order=int(rule.sort_order or 0),
        )
        replaced = False
        for index, existing in enumerate(rules_by_fit[fit_id]):
            if existing.is_hull:
                continue
            if existing.expected_type_id == explicit_rule.expected_type_id:
                rules_by_fit[fit_id][index] = explicit_rule
                replaced = True
                break
        if not replaced:
            rules_by_fit[fit_id].append(explicit_rule)
        type_info_by_fit[fit_id][int(rule.eve_type_id)] = _type_info_from_obj(rule.eve_type)

    substitutions_by_fit: dict[int, list[SubstitutionRuleData]] = defaultdict(list)
    for rule in substitutions:
        fit_id = int(rule.profile.fitting_id)
        substitutions_by_fit[fit_id].append(
            SubstitutionRuleData(
                expected_type_id=int(rule.expected_type_id),
                rule_type=rule.rule_type,
                allowed_type_id=int(rule.allowed_type_id) if rule.allowed_type_id else None,
                max_meta_level_delta=int(rule.max_meta_level_delta or 0),
                same_slot_only=bool(rule.same_slot_only),
                same_group_only=bool(rule.same_group_only),
                penalty_points=_decimal(rule.penalty_points),
                notes=rule.notes,
            )
        )
        type_info_by_fit[fit_id][int(rule.expected_type_id)] = _type_info_from_obj(rule.expected_type)
        if rule.allowed_type_id:
            type_info_by_fit[fit_id][int(rule.allowed_type_id)] = _type_info_from_obj(rule.allowed_type)

    tolerances_by_fit: dict[int, dict[int, list[QuantityToleranceData]]] = defaultdict(lambda: defaultdict(list))
    for tolerance in tolerances:
        fit_id = int(tolerance.profile.fitting_id)
        tolerances_by_fit[fit_id][int(tolerance.eve_type_id)].append(
            QuantityToleranceData(
                eve_type_id=int(tolerance.eve_type_id),
                mode=tolerance.mode,
                lower_bound=int(tolerance.lower_bound or 0),
                upper_bound=int(tolerance.upper_bound or 0),
                penalty_points=_decimal(tolerance.penalty_points),
            )
        )
        type_info_by_fit[fit_id][int(tolerance.eve_type_id)] = _type_info_from_obj(tolerance.eve_type)

    fit_definitions: dict[int, FittingDefinition] = {}
    for fit in fit_rows:
        profile = profile_map.get(int(fit.pk))
        profile_data = MatchProfileData(
            fitting_id=int(fit.pk),
            enabled=bool(getattr(profile, "enabled", True)),
            auto_match_threshold=_decimal(getattr(profile, "auto_match_threshold", Decimal("95.00"))),
            review_threshold=_decimal(getattr(profile, "review_threshold", Decimal("80.00"))),
            allow_extra_items=bool(getattr(profile, "allow_extra_items", True)),
            allow_meta_variants=bool(getattr(profile, "allow_meta_variants", False)),
            allow_faction_variants=bool(getattr(profile, "allow_faction_variants", False)),
            notes=getattr(profile, "notes", "") or "",
        )
        fit_definitions[int(fit.pk)] = FittingDefinition(
            fitting_id=int(fit.pk),
            fitting_name=fit.name,
            ship_type_id=int(fit.ship_type_type_id),
            ship_type_name=getattr(getattr(fit, "ship_type", None), "name", None) or f"Hull {fit.ship_type_type_id}",
            profile=profile_data,
            item_rules=rules_by_fit.get(int(fit.pk), []),
            substitutions=substitutions_by_fit.get(int(fit.pk), []),
            quantity_tolerances=dict(tolerances_by_fit.get(int(fit.pk), {})),
            type_info=type_info_by_fit.get(int(fit.pk), {}),
        )
    return fit_definitions


def _persist_results(results: list[MatchResultData]) -> None:
    if not results:
        return
    refs = _model_refs()
    DoctrineMatchResult = refs["DoctrineMatchResult"]
    timezone = refs["timezone"]
    now = timezone.now()

    contract_ids = [int(result.contract_id) for result in results]
    db_contract_ids = [str(contract_id) for contract_id in contract_ids]
    existing = DoctrineMatchResult.objects.in_bulk(db_contract_ids, field_name="contract_id")
    to_create = []
    to_update = []
    for result in results:
        evidence = dict(result.evidence or {})
        evidence["engine_version"] = MATCH_ENGINE_VERSION
        payload = {
            "matched_fitting_id": result.matched_fitting_id,
            "match_source": result.match_source,
            "match_status": result.match_status,
            "score": result.score,
            "hard_failures_json": json.dumps(result.hard_failures),
            "warnings_json": json.dumps(result.warnings),
            "evidence_json": json.dumps(evidence),
            "updated_at": now,
        }
        current = existing.get(str(result.contract_id))
        if current is None:
            to_create.append(DoctrineMatchResult(contract_id=str(result.contract_id), **payload))
            continue
        for field_name, value in payload.items():
            setattr(current, field_name, value)
        to_update.append(current)
    if to_create:
        DoctrineMatchResult.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        DoctrineMatchResult.objects.bulk_update(
            to_update,
            [
                "matched_fitting_id",
                "match_source",
                "match_status",
                "score",
                "hard_failures_json",
                "warnings_json",
                "evidence_json",
                "updated_at",
            ],
        )


def _result_from_record(record) -> MatchResultData:
    evidence = record.evidence or {}
    matched_fitting_name = None
    if getattr(record, "matched_fitting_id", None):
        matched_fitting = getattr(record, "matched_fitting", None)
        matched_fitting_name = getattr(matched_fitting, "name", None) or evidence.get("selected_fit_name")
    return MatchResultData(
        contract_id=int(record.contract_id),
        matched_fitting_id=int(record.matched_fitting_id) if record.matched_fitting_id else None,
        matched_fitting_name=matched_fitting_name,
        match_source=record.match_source,
        match_status=record.match_status,
        score=_decimal(record.score),
        hard_failures=record.hard_failures,
        warnings=record.warnings,
        evidence=evidence,
    )


def _record_matches_current_engine(record) -> bool:
    evidence = record.evidence or {}
    try:
        return int(evidence.get("engine_version") or 0) == MATCH_ENGINE_VERSION
    except (TypeError, ValueError):
        return False


def get_or_match_contracts(
    contract_ids: Iterable[int],
    *,
    persist: bool = True,
    refresh: bool = False,
) -> dict[int, MatchResultData]:
    refs = _model_refs()
    DoctrineMatchResult = refs["DoctrineMatchResult"]

    contract_ids = [int(contract_id) for contract_id in contract_ids if contract_id]
    if not contract_ids:
        return {}
    db_contract_ids = [str(contract_id) for contract_id in contract_ids]

    if refresh:
        return match_contracts(contract_ids, persist=persist)

    existing_rows = DoctrineMatchResult.objects.filter(contract_id__in=db_contract_ids).select_related("matched_fitting")
    results: dict[int, MatchResultData] = {}
    for row in existing_rows:
        if _record_matches_current_engine(row):
            results[int(row.contract_id)] = _result_from_record(row)

    missing_or_stale_ids = [contract_id for contract_id in contract_ids if contract_id not in results]
    if missing_or_stale_ids:
        results.update(match_contracts(missing_or_stale_ids, persist=persist))

    return {contract_id: results[contract_id] for contract_id in contract_ids if contract_id in results}


def get_or_match_contract(
    contract_id: int,
    *,
    persist: bool = True,
    refresh: bool = False,
) -> MatchResultData:
    return get_or_match_contracts([int(contract_id)], persist=persist, refresh=refresh)[int(contract_id)]


def match_contracts(
    contract_ids: Iterable[int],
    *,
    forced_fit_ids: dict[int, int | None] | None = None,
    preview_fit_id: int | None = None,
    persist: bool = True,
) -> dict[int, MatchResultData]:
    refs = _model_refs()
    CorporateContractItem = refs["CorporateContractItem"]
    CorporateContractSubsidy = refs["CorporateContractSubsidy"]
    DoctrineContractDecision = refs["DoctrineContractDecision"]
    Fitting = refs["Fitting"]
    SubsidyConfig = refs["SubsidyConfig"]

    contract_ids = [int(contract_id) for contract_id in contract_ids if contract_id]
    if not contract_ids:
        return {}
    db_contract_ids = [str(contract_id) for contract_id in contract_ids]

    cfg = SubsidyConfig.active()
    close_match_threshold = Decimal(str(cfg.close_match_threshold))

    if forced_fit_ids is None:
        forced_fit_ids = {
            int(row["contract_id"]): int(row["forced_fitting_id"]) if row["forced_fitting_id"] else None
            for row in CorporateContractSubsidy.objects.filter(contract_id__in=db_contract_ids)
            .values("contract_id", "forced_fitting_id")
        }

    latest_decisions: dict[int, dict[str, Any]] = {}
    for decision in DoctrineContractDecision.objects.filter(contract_id__in=db_contract_ids).order_by("contract_id", "-created_at", "-id"):
        if decision.contract_id in latest_decisions:
            continue
        latest_decisions[int(decision.contract_id)] = {
            "decision": decision.decision,
            "fitting_id": int(decision.fitting_id) if decision.fitting_id else None,
            "summary": decision.summary,
            "details": decision.details,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
        }

    contract_items_map: dict[int, dict[int, ContractItemData]] = defaultdict(dict)
    hull_type_ids: set[int] = set()
    item_rows = CorporateContractItem.objects.filter(contract_id__in=db_contract_ids).select_related("type_name")
    for row in item_rows:
        contract_id = int(row.contract_id)
        type_id = int(row.type_name_id)
        item = contract_items_map[contract_id].get(type_id)
        eve_type = getattr(row, "type_name", None)
        if item is None:
            faction_hint = str(getattr(eve_type, "name", "") or "").lower()
            item = ContractItemData(
                type_id=type_id,
                name=getattr(eve_type, "name", None) or str(type_id),
                group_id=_attr(eve_type, ("group_id", "eve_group_id", "group_fk_id")),
                market_group_id=_attr(eve_type, ("market_group_id", "eve_market_group_id")),
                meta_level=_attr(eve_type, ("meta_level",)),
                meta_group_id=_attr(eve_type, ("meta_group_id",)),
                faction=("faction" in faction_hint or "navy" in faction_hint),
            )
            contract_items_map[contract_id][type_id] = item
        qty = int(getattr(row, "quantity", 0) or 0)
        if row.is_included:
            item.included_qty += qty
            if item.included_qty > 0:
                hull_type_ids.add(type_id)
        else:
            item.excluded_qty += qty

    fit_ids = set(
        Fitting.objects.filter(ship_type_type_id__in=hull_type_ids).values_list("id", flat=True)
    )
    fit_ids.update(int(fit_id) for fit_id in forced_fit_ids.values() if fit_id)
    fit_ids.update(
        int(decision["fitting_id"])
        for decision in latest_decisions.values()
        if decision.get("fitting_id")
    )
    if preview_fit_id:
        fit_ids.add(int(preview_fit_id))
    fit_definitions = _load_fit_definitions(fit_ids)

    results: dict[int, MatchResultData] = {}
    for contract_id in contract_ids:
        contract_items = contract_items_map.get(contract_id, {})
        candidate_fit_ids = {
            fit_id
            for fit_id, definition in fit_definitions.items()
            if definition.ship_type_id in contract_items
        }
        forced_fit_id = forced_fit_ids.get(contract_id)
        if forced_fit_id:
            candidate_fit_ids.add(int(forced_fit_id))
        manual_decision = latest_decisions.get(contract_id)
        if manual_decision and manual_decision.get("fitting_id"):
            candidate_fit_ids.add(int(manual_decision["fitting_id"]))
        if preview_fit_id:
            candidate_fit_ids.add(int(preview_fit_id))

        manual_fit_id = int(manual_decision["fitting_id"]) if manual_decision and manual_decision.get("fitting_id") else None
        candidates = [
            evaluate_contract_against_definition(contract_items, fit_definitions[fit_id])
            for fit_id in candidate_fit_ids
            if fit_id in fit_definitions
            and (
                fit_definitions[fit_id].profile.enabled
                or fit_id == forced_fit_id
                or fit_id == manual_fit_id
            )
        ]
        results[contract_id] = _select_result(
            contract_id=contract_id,
            candidates=candidates,
            forced_fit_id=forced_fit_id,
            forced_fit_name=fit_definitions[forced_fit_id].fitting_name if forced_fit_id in fit_definitions else None,
            manual_decision=manual_decision,
            close_match_threshold=close_match_threshold,
        )

    selected_fit_ids = {
        int(result.matched_fitting_id or (result.evidence or {}).get("selected_fit_id") or 0)
        for result in results.values()
        if result.matched_fitting_id or (result.evidence or {}).get("selected_fit_id")
    }
    if selected_fit_ids:
        from .pricing import get_fitting_pricing_map

        pricing_map = get_fitting_pricing_map(selected_fit_ids)
    else:
        pricing_map = {}

    for result in results.values():
        evidence = dict(result.evidence or {})
        selected_fit_id = int(result.matched_fitting_id or evidence.get("selected_fit_id") or 0) or None
        pricing = pricing_map.get(selected_fit_id or 0)
        evidence["pricing"] = {
            "fit_id": selected_fit_id,
            "basis_isk": float(pricing["basis_total"] or 0) if pricing else 0.0,
            "total_volume_m3": float(pricing["total_vol"] or 0) if pricing else 0.0,
            "suggested_subsidy": float(pricing["suggested"] or 0) if pricing else 0.0,
        }
        result.evidence = evidence

    if persist:
        _persist_results(list(results.values()))
    return results


_MATCH_CONTRACT_RELOAD = object()


def match_contract(
    contract_id: int,
    *,
    forced_fit_id: int | None = _MATCH_CONTRACT_RELOAD,
    persist: bool = True,
) -> MatchResultData:
    forced_fit_ids = None
    if forced_fit_id is not _MATCH_CONTRACT_RELOAD:
        forced_fit_ids = {int(contract_id): forced_fit_id}

    results = match_contracts(
        [int(contract_id)],
        forced_fit_ids=forced_fit_ids,
        persist=persist,
    )
    return results[int(contract_id)]
