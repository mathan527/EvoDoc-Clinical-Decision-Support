from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from normalizer import normalize_drug_name as _normalize_drug_name


SEVERITY_LEVEL = Literal["high", "medium", "low"]


class PatientHistory(BaseModel):
    current_medications: list[str] = Field(default_factory=list)
    known_allergies: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    age: int = Field(..., gt=0, lt=130)
    weight_kg: float = Field(..., gt=1, lt=300)
    renal_function_egfr: float | None = Field(default=None, gt=1, lt=250)
    hepatic_impairment: Literal["none", "mild", "moderate", "severe"] | None = None
    pregnancy_status: Literal["not_pregnant", "pregnant", "unknown"] | None = None
    latest_inr: float | None = Field(default=None, gt=0.5, lt=20)
    creatinine_mg_dl: float | None = Field(default=None, gt=0.1, lt=30)

    @field_validator("current_medications", "known_allergies", "conditions", mode="before")
    @classmethod
    def _clean_string_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Expected a list of strings")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            normalized = _normalize_drug_name(text)
            key = normalized.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(normalized)
        return deduped


class DrugSafetyRequest(BaseModel):
    proposed_medicines: list[str] = Field(..., min_length=1, max_length=20)
    patient_history: PatientHistory

    @field_validator("proposed_medicines", mode="before")
    @classmethod
    def _normalize_proposed_medicines(cls, value: object) -> list[str]:
        if value is None:
            raise ValueError("proposed_medicines is required")
        if not isinstance(value, list):
            raise TypeError("proposed_medicines must be a list of strings")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            # Handle misspelled/unrecognized names gracefully by normalizing only;
            # downstream validation/fuzzy matching decides verification status.
            normalized = _normalize_drug_name(text)
            key = normalized.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(normalized)

        if not deduped:
            raise ValueError("At least one non-empty proposed medicine is required")
        if len(deduped) > 20:
            raise ValueError("Maximum 20 proposed medicines allowed")
        return deduped


class InteractionResult(BaseModel):
    drug_a: str
    drug_b: str
    severity: SEVERITY_LEVEL
    mechanism: str
    clinical_recommendation: str
    source_confidence: SEVERITY_LEVEL
    evidence_level: Literal["A", "B", "C"] = "B"
    reference_source: str = "Fallback clinical dataset"
    guideline_source: str = "Internal validated rulebook"
    evidence_quote: str = "Potential interaction identified by validated safety rules"
    reviewed_at: str = "2026-04-19"
    reason_code: str = "INTERACTION_RISK"
    recommendation_action_code: str = "MONITOR_PATIENT"


class AllergyAlert(BaseModel):
    medicine: str
    reason: str
    severity: Literal["critical", "high", "medium"]


class ContraindicationAlert(BaseModel):
    medicine: str
    condition: str
    reason: str
    severity: Literal["critical", "high", "medium"]
    reason_code: str = "CONTRAINDICATION_RISK"


class RiskBreakdown(BaseModel):
    base_score: int
    interaction_penalty: int
    allergy_penalty: int
    contraindication_penalty: int
    age_modifier: int
    final_score: int = Field(..., ge=0, le=100)
    explanation: str


class ConfidenceBreakdown(BaseModel):
    model_confidence: float = Field(..., ge=0.0, le=1.0)
    rule_confidence: float = Field(..., ge=0.0, le=1.0)
    data_completeness_score: float = Field(..., ge=0.0, le=1.0)
    final_confidence: float = Field(..., ge=0.0, le=1.0)


class RecommendedAction(BaseModel):
    code: Literal[
        "AVOID_COMBINATION",
        "CONSIDER_ALTERNATIVE",
        "DOSE_ADJUST",
        "MONITOR_LABS",
        "MONITOR_PATIENT",
        "DOCTOR_REVIEW",
    ]
    text: str
    priority: Literal["critical", "high", "medium", "low"]


class ReconciliationAlert(BaseModel):
    medicine_class: str
    medicines: list[str]
    reason: str
    severity: Literal["critical", "high", "medium", "low"]
    reason_code: str = "DUPLICATE_THERAPY_CLASS"


class AuditTrail(BaseModel):
    decision_path: list[str] = Field(default_factory=list)
    review_reason: str = ""
    validation_failures_count: int = Field(default=0, ge=0)
    rules_triggered: list[str] = Field(default_factory=list)
    llm_status: Literal["not_attempted", "used", "timeout", "unavailable", "circuit_open"] = "not_attempted"
    fallback_reason: str = ""
    conflict_resolution_policy: Literal["max_severity_wins"] = "max_severity_wins"
    resolved_conflicts_count: int = Field(default=0, ge=0)


class GovernanceMetadata(BaseModel):
    ruleset_id: str = "evodoc-clinical-safety-core"
    ruleset_version: str = "2026.04.19.2"
    approved_by: str = "EvoDoc Clinical Governance Team"
    approved_on: str = "2026-04-19"
    validation_policy: str = "strict_json_with_rule_backstop"


class DrugSafetyResponse(BaseModel):
    interactions: list[InteractionResult]
    allergy_alerts: list[AllergyAlert]
    contraindication_alerts: list[ContraindicationAlert]
    reconciliation_alerts: list[ReconciliationAlert] = Field(default_factory=list)
    history_risk_flags: list[str] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    safe_to_prescribe: bool
    overall_risk_level: Literal["low", "medium", "high", "critical"]
    patient_risk_score: int = Field(..., ge=0, le=100)
    risk_breakdown: RiskBreakdown
    audit_trail: AuditTrail = Field(default_factory=AuditTrail)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    confidence_breakdown: ConfidenceBreakdown = Field(
        default_factory=lambda: ConfidenceBreakdown(
            model_confidence=0.5,
            rule_confidence=0.7,
            data_completeness_score=0.8,
            final_confidence=0.65,
        )
    )
    rules_version: str = "2026.04.19.1"
    fallback_dataset_version: str = "2026.04.19.1"
    analysis_mode: Literal["normal", "degraded_fallback"] = "normal"
    governance: GovernanceMetadata = Field(default_factory=GovernanceMetadata)
    requires_doctor_review: bool
    source: Literal["llm", "fallback"]
    cache_hit: bool
    processing_time_ms: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_score_consistency(self) -> "DrugSafetyResponse":
        if self.patient_risk_score != self.risk_breakdown.final_score:
            raise ValueError("patient_risk_score must equal risk_breakdown.final_score")
        return self
