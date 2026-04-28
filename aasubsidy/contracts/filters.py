from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.db.models import Q, QuerySet


def normalize_title_patterns(raw: str | Iterable[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.splitlines()
    else:
        parts = list(raw)
    return [str(part).strip() for part in parts if str(part).strip()]


def wildcard_pattern_to_regex(pattern: str) -> str:
    escaped = re.escape(pattern)
    return "^" + escaped.replace(r"\*", ".*") + "$"


def title_matches_patterns(title: str | None, patterns: Iterable[str]) -> bool:
    normalized_title = (title or "").strip()
    if not normalized_title:
        return False
    for pattern in patterns:
        if re.match(wildcard_pattern_to_regex(pattern), normalized_title, flags=re.IGNORECASE):
            return True
    return False


def should_ignore_contract(
    *,
    title: str | None,
    price,
    title_patterns: str | Iterable[str] | None,
    ignore_zero_isk_contracts: bool,
) -> bool:
    try:
        price_value = Decimal(str(price or 0))
    except (InvalidOperation, TypeError, ValueError):
        price_value = Decimal("0")
    if ignore_zero_isk_contracts and price_value <= 0:
        return True
    return title_matches_patterns(title, normalize_title_patterns(title_patterns))


def apply_contract_exclusions(qs: QuerySet, cfg) -> QuerySet:
    patterns = normalize_title_patterns(getattr(cfg, "ignored_contract_title_patterns", ""))
    ignore_zero_isk = bool(getattr(cfg, "ignore_zero_isk_contracts", False))

    if ignore_zero_isk:
        qs = qs.exclude(price__lte=0)

    if patterns:
        exclude_q = Q()
        for pattern in patterns:
            exclude_q |= Q(title__iregex=wildcard_pattern_to_regex(pattern))
        qs = qs.exclude(exclude_q)

    return qs
