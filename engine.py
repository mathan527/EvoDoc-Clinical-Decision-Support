from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from difflib import get_close_matches
from itertools import combinations
from pathlib import Path
from typing import Any

from cache import TTLCache
from llm_client import LLMUnavailableError, OllamaClient
from models import (
    AuditTrail,
    ConfidenceBreakdown,
    DrugSafetyRequest,
    DrugSafetyResponse,
    GovernanceMetadata,
    InteractionResult,
    ReconciliationAlert,
    RecommendedAction,
)
from rules.allergy_classes import ALLERGY_CLASS_MAP, check_allergy_alerts
from rules.contraindications import check_contraindications
from rules.risk_scorer import calculate_risk_score, risk_level_from_score, safe_to_prescribe
from validator import parse_llm_response


RULES_VERSION = os.getenv("RULES_VERSION", "2026.04.19.2")
FALLBACK_DATASET_VERSION = os.getenv("FALLBACK_DATASET_VERSION", "2026.04.19.2")
RULESET_ID = os.getenv("RULESET_ID", "evodoc-clinical-safety-core")
RULESET_APPROVED_BY = os.getenv("RULESET_APPROVED_BY", "EvoDoc Clinical Governance Team")
RULESET_APPROVED_ON = os.getenv("RULESET_APPROVED_ON", str(date.today()))
_REQUEST_LLM_TIMEOUT_S = float(os.getenv("REQUEST_LLM_TIMEOUT_SECONDS", "1.2"))

_SPECIAL_FALLBACK_GROUPS: dict[str, list[str]] = {
    "contrast dye": ["contrast dye", "iodinated contrast", "contrast"],
    "oral contraceptives": ["oral contraceptives", "ethinyl estradiol", "levonorgestrel"],
    "nitrates": ["nitroglycerin", "isosorbide mononitrate", "isosorbide dinitrate", "nitrates"],
    "antacids": ["antacid", "aluminum hydroxide", "magnesium hydroxide", "calcium carbonate"],
}

_RECOMMENDATION_CODE_MAP = {
    "avoid": "AVOID_COMBINATION",
    "contraindicated": "AVOID_COMBINATION",
    "alternative": "CONSIDER_ALTERNATIVE",
    "switch": "CONSIDER_ALTERNATIVE",
    "reduce": "DOSE_ADJUST",
    "dose": "DOSE_ADJUST",
    "inr": "MONITOR_LABS",
    "cbc": "MONITOR_LABS",
    "ecg": "MONITOR_LABS",
    "monitor": "MONITOR_PATIENT",
    "review": "DOCTOR_REVIEW",
}

_EVIDENCE_MAP = {
    "high": ("A", "Clinically validated fallback interaction table (FDA/BNF aligned)"),
    "medium": ("B", "Clinically validated fallback interaction table"),
    "low": ("C", "Safety advisory from validated fallback rules"),
}

_GUIDELINE_SOURCE_MAP = {
    "high": "FDA label + BNF severe interaction guidance",
    "medium": "BNF and institutional pharmacology protocol",
    "low": "Institutional medication safety protocol",
}

_ENGINE_RUNTIME: dict[str, float] = {
    "request_count": 0,
    "total_processing_ms": 0,
    "last_processing_ms": 0,
}


def get_engine_runtime_stats() -> dict[str, float]:
    count = int(_ENGINE_RUNTIME["request_count"])
    average = (_ENGINE_RUNTIME["total_processing_ms"] / count) if count else 0.0
    return {
        "request_count": count,
        "average_processing_ms": round(average, 2),
        "last_processing_ms": round(_ENGINE_RUNTIME["last_processing_ms"], 2),
    }


class _LLMCircuitBreaker:
    def __init__(self, failure_threshold: int = 2, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.open_until = 0.0

    def allow_request(self) -> bool:
        return time.monotonic() >= self.open_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.open_until = time.monotonic() + self.cooldown_seconds

    def status(self) -> dict[str, Any]:
        return {
            "state": "open" if not self.allow_request() else "closed",
            "consecutive_failures": self.consecutive_failures,
            "cooldown_seconds": self.cooldown_seconds,
        }


_LLM_CIRCUIT = _LLMCircuitBreaker()


def get_llm_circuit_status() -> dict[str, Any]:
    return _LLM_CIRCUIT.status()


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _elapsed_ms(started: float) -> int:
    return max(1, round((time.perf_counter() - started) * 1000))


def _record_processing(ms: int) -> None:
    _ENGINE_RUNTIME["request_count"] += 1
    _ENGINE_RUNTIME["total_processing_ms"] += ms
    _ENGINE_RUNTIME["last_processing_ms"] = ms


def _pair_key(a: str, b: str) -> tuple[str, str]:
    x, y = _normalize(a), _normalize(b)
    return (x, y) if x <= y else (y, x)


def _build_class_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for class_name, members in ALLERGY_CLASS_MAP.items():
        for member in members:
            lookup[member.lower()] = class_name
    return lookup


_DRUG_CLASS_LOOKUP = _build_class_lookup()


def _match_token_to_meds(token: str, meds: list[str], cutoff: float = 0.8) -> bool:
    token_norm = _normalize(token)
    meds_norm = [_normalize(m) for m in meds]
    meds_norm_set = set(meds_norm)

    if token_norm in meds_norm_set:
        return True

    class_members = _SPECIAL_FALLBACK_GROUPS.get(token_norm, [])
    if class_members and any(member in meds_norm_set for member in class_members):
        return True

    token_upper = token_norm.upper().replace(" ", "_")
    class_drugs = ALLERGY_CLASS_MAP.get(token_upper)
    if class_drugs and any(drug in meds_norm_set for drug in class_drugs):
        return True

    close = get_close_matches(token_norm, meds_norm, n=1, cutoff=cutoff)
    return bool(close)


def _build_prompt(request: DrugSafetyRequest) -> str:
    payload = {
        "proposed_medicines": request.proposed_medicines,
        "current_medications": request.patient_history.current_medications,
        "known_allergies": request.patient_history.known_allergies,
        "conditions": request.patient_history.conditions,
        "age": request.patient_history.age,
        "weight_kg": request.patient_history.weight_kg,
        "renal_function_egfr": request.patient_history.renal_function_egfr,
        "hepatic_impairment": request.patient_history.hepatic_impairment,
        "pregnancy_status": request.patient_history.pregnancy_status,
        "latest_inr": request.patient_history.latest_inr,
        "creatinine_mg_dl": request.patient_history.creatinine_mg_dl,
    }
    return json.dumps(payload, ensure_ascii=False)


def _validate_interaction_rows(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        drug_a = str(row.get("drug_a", "")).strip()
        drug_b = str(row.get("drug_b", "")).strip()
        severity = str(row.get("severity", "")).strip().lower()
        mechanism = str(row.get("mechanism", "")).strip()
        recommendation = str(row.get("clinical_recommendation", "")).strip()

        if not drug_a or not drug_b:
            return False
        if severity not in {"high", "medium", "low"}:
            return False
        if not mechanism or not recommendation:
            return False
    return True


def load_fallback_interactions(file_path: str | Path) -> list[dict[str, Any]]:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("fallback_interactions.json must be a list")
    return data


def build_fallback_index(fallback_data: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in fallback_data:
        if not isinstance(row, dict):
            continue
        a = str(row.get("drug_a", "")).strip()
        b = str(row.get("drug_b", "")).strip()
        if not a or not b:
            continue
        key = _pair_key(a, b)
        index.setdefault(key, []).append(row)
    return index


def _filter_fallback_interactions(
    fallback_data: list[dict[str, Any]],
    proposed: list[str],
    current: list[str],
    fallback_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    meds = [*proposed, *current]
    meds_norm = sorted(set(_normalize(m) for m in meds if m and m.strip()))
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    if fallback_index:
        for a, b in combinations(meds_norm, 2):
            key = (a, b) if a <= b else (b, a)
            for row in fallback_index.get(key, []):
                pair = _pair_key(str(row.get("drug_a", "")), str(row.get("drug_b", "")))
                if pair in seen:
                    continue
                seen.add(pair)
                selected.append(row)

    for row in fallback_data:
        if not isinstance(row, dict):
            continue
        drug_a = str(row.get("drug_a", "")).strip()
        drug_b = str(row.get("drug_b", "")).strip()
        if not drug_a or not drug_b:
            continue

        if _match_token_to_meds(drug_a, meds) and _match_token_to_meds(drug_b, meds):
            key = _pair_key(drug_a, drug_b)
            if key in seen:
                continue
            seen.add(key)
            selected.append(row)

    return selected


def _recommendation_code_from_text(text: str) -> str:
    lowered = text.lower()
    for token, code in _RECOMMENDATION_CODE_MAP.items():
        if token in lowered:
            return code
    return "MONITOR_PATIENT"


def _priority_for_severity(severity: str) -> str:
    return {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }.get(severity, "medium")


def _to_interaction_models(items: list[dict[str, Any]]) -> list[InteractionResult]:
    results: list[InteractionResult] = []
    for row in items:
        severity = str(row.get("severity", "low")).lower()
        if severity not in {"high", "medium", "low"}:
            severity = "low"

        source_confidence = str(row.get("source_confidence", "high")).lower()
        if source_confidence not in {"high", "medium", "low"}:
            source_confidence = "low"

        recommendation = str(
            row.get("clinical_recommendation", "Use clinical judgement and monitor patient closely")
        ).strip() or "Use clinical judgement and monitor patient closely"
        evidence_level, ref_source = _EVIDENCE_MAP.get(severity, ("B", "Fallback clinical dataset"))
        guideline_source = _GUIDELINE_SOURCE_MAP.get(severity, "Institutional medication safety protocol")
        evidence_quote = str(
            row.get(
                "evidence_quote",
                "Potential interaction identified by validated safety rules and should be clinically reviewed",
            )
        )

        results.append(
            InteractionResult(
                drug_a=str(row.get("drug_a", "")).title(),
                drug_b=str(row.get("drug_b", "")).title(),
                severity=severity,
                mechanism=str(row.get("mechanism", "Mechanism unspecified")).strip() or "Mechanism unspecified",
                clinical_recommendation=recommendation,
                source_confidence=source_confidence,
                evidence_level=str(row.get("evidence_level", evidence_level)),
                reference_source=str(row.get("reference_source", ref_source)),
                guideline_source=str(row.get("guideline_source", guideline_source)),
                evidence_quote=evidence_quote,
                reviewed_at=str(row.get("reviewed_at", RULESET_APPROVED_ON)),
                reason_code=str(row.get("reason_code", f"INTERACTION_{severity.upper()}")),
                recommendation_action_code=str(
                    row.get("recommendation_action_code", _recommendation_code_from_text(recommendation))
                ),
            )
        )
    return results


def _resolve_interaction_conflicts(
    interactions: list[InteractionResult],
    policy: str = "max_severity_wins",
) -> tuple[list[InteractionResult], int]:
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    deduped: dict[tuple[str, str], InteractionResult] = {}
    conflicts = 0

    for interaction in interactions:
        key = _pair_key(interaction.drug_a, interaction.drug_b)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = interaction
            continue

        if existing.severity != interaction.severity:
            conflicts += 1

        if policy == "max_severity_wins":
            if severity_rank[interaction.severity] > severity_rank[existing.severity]:
                deduped[key] = interaction

    return list(deduped.values()), conflicts


def _build_reconciliation_alerts(proposed: list[str], current: list[str]) -> list[ReconciliationAlert]:
    grouped: dict[str, set[str]] = {}
    for med in [*proposed, *current]:
        class_name = _DRUG_CLASS_LOOKUP.get(_normalize(med))
        if not class_name:
            continue
        grouped.setdefault(class_name, set()).add(med.title())

    alerts: list[ReconciliationAlert] = []
    for class_name, meds in grouped.items():
        if len(meds) < 2:
            continue
        alerts.append(
            ReconciliationAlert(
                medicine_class=class_name,
                medicines=sorted(meds),
                reason=f"Potential duplicate therapy within class {class_name}",
                severity="high" if class_name in {"NSAIDS", "ACE_INHIBITORS", "OPIOIDS"} else "medium",
            )
        )
    return alerts


def _history_risk_flags(request: DrugSafetyRequest, reconciliation_alerts: list[ReconciliationAlert]) -> list[str]:
    flags: list[str] = []
    age = request.patient_history.age
    if age < 12:
        flags.append("pediatric_risk")
    if age > 65:
        flags.append("geriatric_risk")
    if request.patient_history.known_allergies:
        flags.append("allergy_history_present")
    if request.patient_history.conditions:
        flags.append("comorbidity_present")
    if reconciliation_alerts:
        flags.append("duplicate_therapy_class")
    return flags


def _build_recommended_actions(
    interactions: list[InteractionResult],
    requires_doctor_review: bool,
) -> list[RecommendedAction]:
    actions: dict[tuple[str, str], RecommendedAction] = {}

    for interaction in interactions:
        code = interaction.recommendation_action_code
        text = interaction.clinical_recommendation
        key = (code, text)
        actions[key] = RecommendedAction(
            code=code if code in {
                "AVOID_COMBINATION",
                "CONSIDER_ALTERNATIVE",
                "DOSE_ADJUST",
                "MONITOR_LABS",
                "MONITOR_PATIENT",
                "DOCTOR_REVIEW",
            } else "MONITOR_PATIENT",
            text=text,
            priority=_priority_for_severity(interaction.severity),
        )

    if requires_doctor_review:
        key = ("DOCTOR_REVIEW", "Mandatory physician review required before final prescription.")
        actions[key] = RecommendedAction(
            code="DOCTOR_REVIEW",
            text="Mandatory physician review required before final prescription.",
            priority="critical",
        )

    return list(actions.values())


def _calculate_confidence_breakdown(
    interactions: list[InteractionResult],
    source: str,
    history_flags: list[str],
    request: DrugSafetyRequest,
    validation_failures_count: int,
    llm_status: str,
    critical_alerts_present: bool,
) -> ConfidenceBreakdown:
    if not interactions:
        return ConfidenceBreakdown(
            model_confidence=0.35,
            rule_confidence=0.82,
            data_completeness_score=0.65,
            final_confidence=0.61,
        )

    evidence_score_map = {"A": 0.95, "B": 0.82, "C": 0.68}
    source_conf_map = {"high": 0.9, "medium": 0.72, "low": 0.48}

    avg_evidence = sum(evidence_score_map.get(i.evidence_level, 0.75) for i in interactions) / max(1, len(interactions))
    avg_source_quality = sum(source_conf_map.get(i.source_confidence, 0.6) for i in interactions) / max(1, len(interactions))

    base_model_conf = 0.72 if source == "llm" else 0.56
    model_conf = (base_model_conf * 0.60) + (avg_evidence * 0.25) + (avg_source_quality * 0.15)
    if any(i.source_confidence == "low" for i in interactions):
        model_conf -= 0.08
    if llm_status in {"timeout", "unavailable", "circuit_open"}:
        model_conf -= 0.05

    rule_conf = 0.86 if source == "fallback" else 0.80
    if validation_failures_count > 0:
        rule_conf -= min(0.18, 0.03 * validation_failures_count)
    else:
        rule_conf += 0.04

    if critical_alerts_present:
        rule_conf -= 0.03

    optional_fields = [
        request.patient_history.renal_function_egfr,
        request.patient_history.hepatic_impairment,
        request.patient_history.pregnancy_status,
        request.patient_history.latest_inr,
        request.patient_history.creatinine_mg_dl,
    ]
    optional_present_fraction = sum(1 for field in optional_fields if field is not None) / len(optional_fields)

    data_completeness = 0.78
    if request.patient_history.current_medications:
        data_completeness += 0.05
    if request.patient_history.known_allergies:
        data_completeness += 0.04
    if request.patient_history.conditions:
        data_completeness += 0.04
    data_completeness += 0.09 * optional_present_fraction
    if history_flags:
        data_completeness += 0.02

    final_conf = (model_conf * 0.40) + (rule_conf * 0.35) + (data_completeness * 0.25)
    final_conf -= min(0.12, 0.02 * validation_failures_count)
    if any(i.source_confidence == "low" for i in interactions):
        final_conf -= 0.04
    if llm_status in {"timeout", "unavailable", "circuit_open"}:
        final_conf -= 0.02

    model_conf = max(0.0, min(1.0, model_conf))
    rule_conf = max(0.0, min(1.0, rule_conf))
    data_completeness = max(0.0, min(1.0, data_completeness))
    final_conf = max(0.0, min(1.0, final_conf))

    return ConfidenceBreakdown(
        model_confidence=round(model_conf, 3),
        rule_confidence=round(rule_conf, 3),
        data_completeness_score=round(data_completeness, 3),
        final_confidence=round(final_conf, 3),
    )


async def analyze_drug_safety(
    request: DrugSafetyRequest,
    cache: TTLCache,
    llm_client: OllamaClient,
    fallback_interactions: list[dict[str, Any]],
    system_prompt: str,
    fallback_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> DrugSafetyResponse:
    started = time.perf_counter()
    decision_path: list[str] = ["cache_lookup"]
    validation_failures_count = 0
    resolved_conflicts_count = 0
    llm_status = "not_attempted"
    fallback_reason = ""

    proposed = request.proposed_medicines
    current = request.patient_history.current_medications

    cache_key = TTLCache.generate_key(proposed, current)
    cached = await cache.get(cache_key)
    if cached is not None:
        decision_path.append("cache_hit")
        processing_time_ms = _elapsed_ms(started)
        payload = dict(cached)
        payload.setdefault("rules_version", RULES_VERSION)
        payload.setdefault("fallback_dataset_version", FALLBACK_DATASET_VERSION)
        payload.setdefault("analysis_mode", "normal")
        payload["cache_hit"] = True
        payload["processing_time_ms"] = processing_time_ms
        trail = dict(payload.get("audit_trail", {}))
        trail_path = list(trail.get("decision_path", []))
        trail_path.extend(decision_path)
        trail["decision_path"] = trail_path
        payload["audit_trail"] = trail
        _record_processing(processing_time_ms)
        return DrugSafetyResponse(**payload)

    decision_path.append("cache_miss")
    allergy_alerts = check_allergy_alerts(proposed, request.patient_history.known_allergies)
    contraindication_alerts = check_contraindications(proposed, request.patient_history.conditions)
    reconciliation_alerts = _build_reconciliation_alerts(proposed, current)
    history_flags = _history_risk_flags(request, reconciliation_alerts)
    decision_path.append("rules_executed")

    interactions: list[InteractionResult] = []
    requires_doctor_review = False
    source: str = "fallback"

    overlap = {_normalize(m) for m in proposed}.intersection({_normalize(m) for m in current})
    for dup in overlap:
        interactions.append(
            InteractionResult(
                drug_a=dup.title(),
                drug_b=dup.title(),
                severity="medium",
                mechanism="Duplicate therapy detected between proposed and current medications",
                clinical_recommendation="Review duplicate therapy and avoid unintentional double dosing",
                source_confidence="high",
                evidence_level="B",
                reference_source="Internal therapy reconciliation rule",
                reason_code="DUPLICATE_THERAPY",
                recommendation_action_code="DOCTOR_REVIEW",
            )
        )

    llm_interactions: list[InteractionResult] = []
    llm_valid = False

    if len(proposed) > 1 and _LLM_CIRCUIT.allow_request():
        try:
            decision_path.append("llm_attempted")
            raw = await asyncio.wait_for(
                llm_client.generate(prompt=_build_prompt(request), system_prompt=system_prompt),
                timeout=_REQUEST_LLM_TIMEOUT_S,
            )
            parsed = parse_llm_response(raw, proposed_medicines=proposed, current_medications=current)
            if parsed is not None and _validate_interaction_rows(parsed.get("interactions", [])):
                llm_valid = True
                llm_interactions = _to_interaction_models(parsed.get("interactions", []))
                requires_doctor_review = bool(parsed.get("requires_doctor_review", False))
                validation_failures_count += int(parsed.get("validation_failures_count", 0))
                source = "llm"
                llm_status = "used"
                decision_path.append("llm_validated")
                _LLM_CIRCUIT.record_success()
            else:
                validation_failures_count += int((parsed or {}).get("validation_failures_count", 1))
                llm_status = "unavailable"
                decision_path.append("llm_validation_failed")
                _LLM_CIRCUIT.record_failure()
        except asyncio.TimeoutError:
            llm_status = "timeout"
            fallback_reason = "llm_timeout"
            decision_path.append("llm_unavailable_or_timeout")
            _LLM_CIRCUIT.record_failure()
        except LLMUnavailableError:
            llm_status = "unavailable"
            fallback_reason = "llm_unavailable"
            decision_path.append("llm_unavailable_or_timeout")
            _LLM_CIRCUIT.record_failure()
    elif len(proposed) > 1:
        llm_status = "circuit_open"
        fallback_reason = "llm_circuit_open"
        decision_path.append("llm_circuit_open")

    fallback_hits = _to_interaction_models(
        _filter_fallback_interactions(fallback_interactions, proposed, current, fallback_index=fallback_index)
    )

    if llm_valid and llm_interactions:
        interactions.extend(llm_interactions)
        source = "llm"
        decision_path.append("llm_used")
    else:
        interactions.extend(fallback_hits)
        source = "fallback"
        requires_doctor_review = True
        decision_path.append("fallback_used")
        if not fallback_reason:
            fallback_reason = "llm_not_used"

    if not interactions:
        anchor = proposed[0] if proposed else (current[0] if current else "Medication")
        interactions.append(
            InteractionResult(
                drug_a=anchor,
                drug_b=anchor,
                severity="low",
                mechanism="No clinically significant drug-drug interaction identified in validated sources for submitted list",
                clinical_recommendation="Continue standard monitoring and reassess on medication changes",
                source_confidence="medium",
                evidence_level="C",
                reference_source="Fallback non-empty safeguard",
                reason_code="NO_MAJOR_DDI_FOUND",
                recommendation_action_code="MONITOR_PATIENT",
            )
        )
        requires_doctor_review = True
        source = "fallback"
        decision_path.append("non_empty_guard_applied")

    interactions, resolved_conflicts_count = _resolve_interaction_conflicts(interactions)

    breakdown = calculate_risk_score(
        interactions=interactions,
        allergy_alerts=allergy_alerts,
        contraindication_alerts=contraindication_alerts,
        age=request.patient_history.age,
        weight=request.patient_history.weight_kg,
    )
    score = breakdown.final_score

    critical_alerts_present = any(a.severity == "critical" for a in allergy_alerts) or any(
        a.severity == "critical" for a in contraindication_alerts
    )

    confidence = _calculate_confidence_breakdown(
        interactions,
        source,
        history_flags,
        request,
        validation_failures_count,
        llm_status,
        critical_alerts_present,
    )
    confidence_score = confidence.final_confidence

    review_reasons: list[str] = []
    confidence_threshold = 0.75 if (risk_level_from_score(score) in {"high", "critical"} or critical_alerts_present) else 0.68
    if confidence_score < confidence_threshold:
        requires_doctor_review = True
        review_reasons.append(f"confidence_below_threshold_{confidence_threshold:.2f}")
    if source == "fallback":
        review_reasons.append("fallback_path")
    if validation_failures_count > 0:
        review_reasons.append("llm_validation_issue")
    if any(i.source_confidence == "low" for i in interactions):
        review_reasons.append("low_source_confidence")
    if any(a.severity == "critical" for a in allergy_alerts):
        review_reasons.append("critical_allergy")

    if any(a.severity == "critical" for a in contraindication_alerts):
        requires_doctor_review = True
        review_reasons.append("critical_contraindication")

    if any(a.severity == "critical" for a in allergy_alerts):
        decision_path.append("policy_block_critical_allergy")

    recommended_actions = _build_recommended_actions(interactions, requires_doctor_review)
    rules_triggered = [
        "allergy_class_detection",
        "condition_contraindication_table",
        "duplicate_therapy_detection",
        "confidence_review_gate",
    ]

    audit_trail = AuditTrail(
        decision_path=decision_path,
        review_reason=", ".join(sorted(set(review_reasons))) if review_reasons else "none",
        validation_failures_count=validation_failures_count,
        rules_triggered=rules_triggered,
        llm_status=llm_status,
        fallback_reason=fallback_reason,
        conflict_resolution_policy="max_severity_wins",
        resolved_conflicts_count=resolved_conflicts_count,
    )

    governance = GovernanceMetadata(
        ruleset_id=RULESET_ID,
        ruleset_version=RULES_VERSION,
        approved_by=RULESET_APPROVED_BY,
        approved_on=RULESET_APPROVED_ON,
        validation_policy="strict_json_with_rule_backstop",
    )

    policy_safe_to_prescribe = safe_to_prescribe(score, allergy_alerts)
    if any(a.severity == "critical" for a in contraindication_alerts):
        policy_safe_to_prescribe = False

    response = DrugSafetyResponse(
        interactions=interactions,
        allergy_alerts=allergy_alerts,
        contraindication_alerts=contraindication_alerts,
        reconciliation_alerts=reconciliation_alerts,
        history_risk_flags=history_flags,
        recommended_actions=recommended_actions,
        safe_to_prescribe=policy_safe_to_prescribe,
        overall_risk_level=risk_level_from_score(score),
        patient_risk_score=score,
        risk_breakdown=breakdown,
        audit_trail=audit_trail,
        confidence_score=confidence_score,
        confidence_breakdown=confidence,
        rules_version=RULES_VERSION,
        fallback_dataset_version=FALLBACK_DATASET_VERSION,
        analysis_mode="degraded_fallback" if source == "fallback" and llm_status in {"timeout", "unavailable", "circuit_open"} else "normal",
        governance=governance,
        requires_doctor_review=requires_doctor_review,
        source=source,
        cache_hit=False,
        processing_time_ms=_elapsed_ms(started),
    )

    await cache.set(cache_key, response.model_dump())
    _record_processing(response.processing_time_ms)
    return response
