from __future__ import annotations

from typing import Any

from models import RiskBreakdown


INTERACTION_POINTS = {"high": 25, "medium": 15, "low": 8}
ALERT_POINTS = {"critical": 35, "high": 20, "medium": 10}
CONTRA_POINTS = {"critical": 30, "high": 20, "medium": 10}


def _severity_of(item: Any) -> str:
    if hasattr(item, "severity"):
        return str(getattr(item, "severity")).lower()
    if isinstance(item, dict):
        return str(item.get("severity", "")).lower()
    return ""


def calculate_risk_score(
    interactions: list[Any],
    allergy_alerts: list[Any],
    contraindication_alerts: list[Any],
    age: int,
    weight: float,
) -> RiskBreakdown:
    base_score = 0

    interaction_penalty = sum(INTERACTION_POINTS.get(_severity_of(i), 0) for i in interactions)
    allergy_penalty = sum(ALERT_POINTS.get(_severity_of(a), 0) for a in allergy_alerts)
    contraindication_penalty = sum(CONTRA_POINTS.get(_severity_of(c), 0) for c in contraindication_alerts)

    if age < 12:
        age_modifier = 10
    elif age > 65:
        age_modifier = 8
    else:
        age_modifier = 0

    # Reserved for future use; currently no weight penalty requested.
    _ = weight

    total = base_score + interaction_penalty + allergy_penalty + contraindication_penalty + age_modifier
    final_score = min(100, max(0, total))

    explanation = (
        f"base({base_score}) + interactions({interaction_penalty}) + allergies({allergy_penalty}) + "
        f"contraindications({contraindication_penalty}) + age_modifier({age_modifier}) = {final_score}"
    )

    return RiskBreakdown(
        base_score=base_score,
        interaction_penalty=interaction_penalty,
        allergy_penalty=allergy_penalty,
        contraindication_penalty=contraindication_penalty,
        age_modifier=age_modifier,
        final_score=final_score,
        explanation=explanation,
    )


def risk_level_from_score(score: int) -> str:
    if score <= 25:
        return "low"
    if score <= 50:
        return "medium"
    if score <= 75:
        return "high"
    return "critical"


def safe_to_prescribe(score: int, allergy_alerts: list[Any]) -> bool:
    has_critical_allergy = any(_severity_of(alert) == "critical" for alert in allergy_alerts)
    return score < 50 and not has_critical_allergy
