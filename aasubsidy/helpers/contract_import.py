from __future__ import annotations


def claim_clearance_completed(quantity: int | None) -> bool:
    return int(quantity or 0) > 0


def plan_claim_clearance(existing_clearance_quantity: int | None, claim_quantity: int | None) -> dict[str, object]:
    if claim_clearance_completed(existing_clearance_quantity):
        return {
            "status": "already_cleared",
            "delete_claim": False,
            "remaining_claim_quantity": max(int(claim_quantity or 0), 0),
        }

    normalized_claim_quantity = max(int(claim_quantity or 0), 0)
    if normalized_claim_quantity <= 0:
        return {
            "status": "retry_later",
            "delete_claim": False,
            "remaining_claim_quantity": 0,
        }

    remaining_claim_quantity = normalized_claim_quantity - 1
    return {
        "status": "clear",
        "delete_claim": remaining_claim_quantity == 0,
        "remaining_claim_quantity": remaining_claim_quantity,
    }
