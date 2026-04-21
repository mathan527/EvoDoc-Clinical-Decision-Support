from __future__ import annotations

import json
import re
from difflib import get_close_matches
from typing import Any


VALID_SEVERITIES = {"high", "medium", "low"}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
SAFE_WORDS_IN_RECOMMENDATION = {
    "safe",
    "no action",
    "no significant interaction",
    "continue without change",
}


def _strip_markdown_fences(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _fuzzy_in_list(value: str, candidates: list[str], cutoff: float = 0.8) -> str | None:
    lowered = value.strip().lower()
    if lowered in candidates:
        return lowered
    close = get_close_matches(lowered, candidates, n=1, cutoff=cutoff)
    return close[0] if close else None


def parse_llm_response(
    raw: str,
    proposed_medicines: list[str] | None = None,
    current_medications: list[str] | None = None,
) -> dict[str, Any] | None:
    cleaned = _strip_markdown_fences(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    interactions = data.get("interactions")
    if interactions is None or not isinstance(interactions, list):
        return None

    allowed_names = [
        *[m.strip().lower() for m in (proposed_medicines or []) if m and m.strip()],
        *[m.strip().lower() for m in (current_medications or []) if m and m.strip()],
    ]
    allowed_names = sorted(set(allowed_names))

    sanitized_interactions: list[dict[str, Any]] = []
    low_confidence_found = False
    missing_counter = 0
    total_expected_fields = 0
    validation_failures_count = 0
    pair_map: dict[tuple[str, str], dict[str, Any]] = {}

    for item in interactions:
        if not isinstance(item, dict):
            continue

        required_fields = [
            "drug_a",
            "drug_b",
            "severity",
            "mechanism",
            "clinical_recommendation",
            "source_confidence",
        ]
        total_expected_fields += len(required_fields)

        for field in required_fields:
            if item.get(field) in (None, ""):
                missing_counter += 1

        drug_a_raw = str(item.get("drug_a", "")).strip()
        drug_b_raw = str(item.get("drug_b", "")).strip()
        mechanism = str(item.get("mechanism", "")).strip()
        recommendation = str(item.get("clinical_recommendation", "")).strip()

        # Strict validation: required fields must be present.
        if not drug_a_raw or not drug_b_raw or not mechanism or not recommendation:
            validation_failures_count += 1
            continue

        # Discard interactions that do not map back to request meds.
        if allowed_names:
            match_a = _fuzzy_in_list(drug_a_raw, allowed_names, cutoff=0.8)
            match_b = _fuzzy_in_list(drug_b_raw, allowed_names, cutoff=0.8)
            if not match_a or not match_b:
                validation_failures_count += 1
                continue
            drug_a = match_a.title()
            drug_b = match_b.title()
        else:
            drug_a = drug_a_raw.title()
            drug_b = drug_b_raw.title()

        severity = str(item.get("severity", "low")).strip().lower()
        if severity not in VALID_SEVERITIES:
            severity = "low"

        source_confidence = str(item.get("source_confidence", "low")).strip().lower()
        if source_confidence not in VALID_SEVERITIES:
            source_confidence = "low"

        recommendation_lower = recommendation.lower()
        if severity in {"high", "medium"} and any(word in recommendation_lower for word in SAFE_WORDS_IN_RECOMMENDATION):
            source_confidence = "low"
            low_confidence_found = True
            validation_failures_count += 1

        if source_confidence == "low":
            low_confidence_found = True

        normalized_item = {
            "drug_a": drug_a,
            "drug_b": drug_b,
            "severity": severity,
            "mechanism": mechanism,
            "clinical_recommendation": recommendation,
            "source_confidence": source_confidence,
        }

        pair_key = tuple(sorted((drug_a.lower(), drug_b.lower())))
        existing = pair_map.get(pair_key)
        if existing is None:
            pair_map[pair_key] = normalized_item
            continue

        if existing["severity"] != severity:
            validation_failures_count += 1

        if SEVERITY_RANK[severity] > SEVERITY_RANK[existing["severity"]]:
            pair_map[pair_key] = normalized_item

    if total_expected_fields == 0:
        return None

    if (missing_counter / total_expected_fields) > 0.5:
        return None

    sanitized_interactions = list(pair_map.values())

    if not sanitized_interactions:
        return None

    return {
        "interactions": sanitized_interactions,
        "requires_doctor_review": low_confidence_found,
        "validation_failures_count": validation_failures_count,
    }
