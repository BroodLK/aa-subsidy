from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable


MAX_SCORE = Decimal("100.00")
ZERO = Decimal("0.00")
MATCH_ENGINE_VERSION = 2


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


def evaluate_contract_against_definition(
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
            actions.append("optional_item")
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
                    f"Unexpected extra item: {actual_name}.",
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
                    f"Unexpected extra item: {actual_name}.",
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
) -> MatchResultData:
    candidate_by_fit = {candidate.fitting_id: candidate for candidate in candidates}

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
            match_status="rejected",
            score=ZERO,
            hard_failures=[_issue("error", "manual_reject", "Reviewer rejected this contract for doctrine matching.")],
            warnings=[],
            evidence={"decision": manual_decision, "candidates": _candidate_summaries(candidates)},
        )

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

    viable = [candidate for candidate in candidates if candidate.viable]
    viable.sort(key=lambda candidate: (-candidate.score, candidate.fitting_name.lower(), candidate.fitting_id))

    if not viable:
        top_candidate = max(candidates, key=lambda candidate: (candidate.score, candidate.fitting_name.lower()), default=None)
        evidence = {
            "selected_fit_id": getattr(top_candidate, "fitting_id", None),
            "selected_fit_name": getattr(top_candidate, "fitting_name", None),
            "candidates": _candidate_summaries(candidates),
        }
        return MatchResultData(
            contract_id=contract_id,
            matched_fitting_id=None,
            matched_fitting_name=None,
            match_source="auto",
            match_status="rejected",
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
    Sum = refs["Sum"]

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
    default_fit_items = list(
        FittingItem.objects.filter(fit_id__in=fit_ids)
        .values("fit_id", "type_id", "type_fk__name")
        .annotate(total_qty=Sum("quantity"))
        .order_by("fit_id", "type_fk__name")
    )

    fit_items_by_fit: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in default_fit_items:
        fit_items_by_fit[int(row["fit_id"])].append(row)

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
        for row in items:
            type_id = int(row["type_id"])
            name = row["type_fk__name"] or str(type_id)
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
            type_info_by_fit[fit_id][type_id] = TypeInfo(type_id=type_id, name=name)

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

    contract_ids = [result.contract_id for result in results]
    existing = DoctrineMatchResult.objects.in_bulk(contract_ids, field_name="contract_id")
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
        current = existing.get(result.contract_id)
        if current is None:
            to_create.append(DoctrineMatchResult(contract_id=result.contract_id, **payload))
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
                "matched_fitting",
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

    if refresh:
        return match_contracts(contract_ids, persist=persist)

    existing_rows = DoctrineMatchResult.objects.filter(contract_id__in=contract_ids).select_related("matched_fitting")
    results: dict[int, MatchResultData] = {}
    stale_ids: list[int] = []
    for row in existing_rows:
        evidence = row.evidence or {}
        if evidence.get("engine_version") != MATCH_ENGINE_VERSION:
            stale_ids.append(int(row.contract_id))
            continue
        results[int(row.contract_id)] = _result_from_record(row)
    missing_ids = [contract_id for contract_id in contract_ids if contract_id not in results and contract_id not in stale_ids]
    refresh_ids = stale_ids + missing_ids
    if refresh_ids:
        results.update(match_contracts(refresh_ids, persist=persist))
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
    Sum = refs["Sum"]

    contract_ids = [int(contract_id) for contract_id in contract_ids if contract_id]
    if not contract_ids:
        return {}

    if forced_fit_ids is None:
        forced_fit_ids = {
            int(row["contract_id"]): int(row["forced_fitting_id"]) if row["forced_fitting_id"] else None
            for row in CorporateContractSubsidy.objects.filter(contract_id__in=contract_ids)
            .values("contract_id", "forced_fitting_id")
        }

    latest_decisions: dict[int, dict[str, Any]] = {}
    for decision in DoctrineContractDecision.objects.filter(contract_id__in=contract_ids).order_by("contract_id", "-created_at", "-id"):
        if decision.contract_id in latest_decisions:
            continue
        latest_decisions[int(decision.contract_id)] = {
            "decision": decision.decision,
            "fitting_id": int(decision.fitting_id) if decision.fitting_id else None,
            "summary": decision.summary,
            "details": decision.details,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
        }

    item_rows = list(
        CorporateContractItem.objects.filter(contract_id__in=contract_ids)
        .values("contract_id", "type_name_id", "type_name__name", "is_included")
        .annotate(total_qty=Sum("quantity"))
    )
    contract_items_map: dict[int, dict[int, ContractItemData]] = defaultdict(dict)
    hull_type_ids: set[int] = set()
    for row in item_rows:
        contract_id = int(row["contract_id"])
        type_id = int(row["type_name_id"])
        item = contract_items_map[contract_id].get(type_id)
        if item is None:
            item = ContractItemData(
                type_id=type_id,
                name=row["type_name__name"] or str(type_id),
            )
            contract_items_map[contract_id][type_id] = item
        qty = int(row["total_qty"] or 0)
        if row["is_included"]:
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
        )

    if persist:
        _persist_results(list(results.values()))
    return results


def match_contract(contract_id: int, *, forced_fit_id: int | None = None, persist: bool = True) -> MatchResultData:
    results = match_contracts(
        [int(contract_id)],
        forced_fit_ids={int(contract_id): forced_fit_id} if forced_fit_id is not None else None,
        persist=persist,
    )
    return results[int(contract_id)]
