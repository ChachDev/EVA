"""Pure scoring: keep raw Decimal values until each published result is rounded."""

from decimal import Decimal, ROUND_HALF_UP

from .contracts import validate_result_semantics


ZERO = Decimal(0)
HUNDRED = Decimal(100)


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def score(request: dict, result: dict) -> dict:
    """Inputs must pass Contracts.validate_result; semantic checks are also defensive."""
    validate_result_semantics(request, result)
    judgments = {item["criterion_id"]: item for item in result["criteria"]}
    entries = []
    sections = {}
    has_pending = False
    pending_flag = False
    gross_error = False
    applicable_weight = ZERO
    weighted_sum = ZERO

    for criterion in request["checklist"]["criteria"]:
        item = judgments[criterion["id"]]
        pending = item["status"] == "needs_review"
        has_pending |= pending
        is_flag = criterion["type"] == "flag"
        section_id = criterion["section_id"]
        if section_id is not None:
            section = sections.setdefault(section_id, {"weight": ZERO, "sum": ZERO, "pending": False})
            section["pending"] |= pending
        if is_flag:
            gross_error |= item["value"] is True
            pending_flag |= pending
            entries.append({"criterion_id": criterion["id"], "flag": True, "weight": ZERO, "weighted": ZERO})
            continue
        if pending or item["value"] is None:
            entries.append({"criterion_id": criterion["id"], "flag": False, "weight": None, "weighted": None})
            continue
        weight = _decimal(criterion["weight"])
        ratio = (Decimal(int(item["value"])) if criterion["type"] == "boolean"
                 else _decimal(item["value"]) / _decimal(max(criterion["scale"])))
        weighted = ratio * weight
        applicable_weight += weight
        weighted_sum += weighted
        if section_id is not None:
            sections[section_id]["weight"] += weight
            sections[section_id]["sum"] += weighted
        entries.append({"criterion_id": criterion["id"], "flag": False, "weight": weight, "weighted": weighted})

    status = "needs_review" if has_pending else ("complete" if applicable_weight > 0 else "not_calculable")
    complete = status == "complete"
    return {
        "status": status,
        "total": _rounded(weighted_sum * HUNDRED / applicable_weight) if complete else None,
        "applicable_weight": float(applicable_weight),
        "has_gross_error": True if gross_error else (None if pending_flag else False),
        "sections": [
            {"section_id": name, "score": _rounded(section["sum"] * HUNDRED / section["weight"])
             if section["weight"] > 0 and not section["pending"] else None}
            for name, section in sections.items()
        ],
        "criteria": [
            {"criterion_id": entry["criterion_id"],
             "points": 0 if entry["flag"] else (
                 _rounded(entry["weighted"] * HUNDRED / applicable_weight)
                 if complete and entry["weighted"] is not None else None),
             "effective_weight": 0 if entry["flag"] else (
                 _rounded(entry["weight"] * HUNDRED / applicable_weight)
                 if complete and entry["weight"] is not None else None)}
            for entry in entries
        ],
    }

