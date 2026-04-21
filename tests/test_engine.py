from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cache import TTLCache
from engine import _LLM_CIRCUIT, analyze_drug_safety
from llm_client import LLMUnavailableError
from main import app
from models import DrugSafetyRequest
from rules.allergy_classes import check_allergy_alerts
from rules.contraindications import check_contraindications
from rate_limiter import FixedWindowRateLimiter


class FakeLLMDown:
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        raise LLMUnavailableError("LLM unavailable in test")


class SlowLLM:
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        await asyncio.sleep(2.0)
        return '{"interactions":[]}'


class InvalidJsonLLM:
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return "```json\nnot-valid-json\n```"


class ConflictingSeverityLLM:
    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return json.dumps(
            {
                "interactions": [
                    {
                        "drug_a": "Warfarin",
                        "drug_b": "Aspirin",
                        "severity": "medium",
                        "mechanism": "protein binding",
                        "clinical_recommendation": "Monitor INR closely",
                        "source_confidence": "high",
                    },
                    {
                        "drug_a": "Aspirin",
                        "drug_b": "Warfarin",
                        "severity": "high",
                        "mechanism": "platelet inhibition and anticoagulation",
                        "clinical_recommendation": "Avoid combination unless supervised",
                        "source_confidence": "high",
                    },
                ]
            }
        )


def _load_fallback_data() -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    path = root / "data" / "fallback_interactions.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _request_payload(**overrides) -> dict:
    payload = {
        "proposed_medicines": ["Warfarin", "Aspirin"],
        "patient_history": {
            "current_medications": [],
            "known_allergies": [],
            "conditions": [],
            "age": 45,
            "weight_kg": 70,
        },
    }
    payload.update(overrides)
    return payload


def test_penicillin_allergy_with_amoxicillin_flags_critical():
    alerts = check_allergy_alerts(["Amoxicillin"], ["Penicillin"])
    assert alerts
    assert any(a.severity == "critical" and a.medicine.lower() == "amoxicillin" for a in alerts)


@pytest.mark.asyncio
async def test_warfarin_aspirin_present_in_fallback_with_high_severity():
    request = DrugSafetyRequest(**_request_payload())
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    pairs = {(i.drug_a.lower(), i.drug_b.lower(), i.severity) for i in response.interactions}
    assert ("warfarin", "aspirin", "high") in pairs or ("aspirin", "warfarin", "high") in pairs
    assert 0.0 <= response.confidence_score <= 1.0


@pytest.mark.asyncio
async def test_cache_hit_on_reordered_medicine_list():
    cache = TTLCache()
    fallback_data = _load_fallback_data()

    req1 = DrugSafetyRequest(**_request_payload(proposed_medicines=["Warfarin", "Aspirin"]))
    req2 = DrugSafetyRequest(**_request_payload(proposed_medicines=["Aspirin", "Warfarin"]))

    first = await analyze_drug_safety(req1, cache, FakeLLMDown(), fallback_data, "test")
    second = await analyze_drug_safety(req2, cache, FakeLLMDown(), fallback_data, "test")

    assert first.cache_hit is False
    assert second.cache_hit is True


def test_negative_age_returns_422_validation_error():
    with TestClient(app) as client:
        payload = _request_payload()
        payload["patient_history"]["age"] = -1
        response = client.post("/api/v1/analyze", json=payload)
        assert response.status_code == 422


def test_health_endpoint_contains_runtime_and_circuit_metrics():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "engine_runtime" in data
        assert "llm_circuit_breaker" in data


def test_duplicate_medicines_deduplicated_silently():
    request = DrugSafetyRequest(
        proposed_medicines=["aspirin", "Aspirin", "  ASPIRIN  ", "Warfarin"],
        patient_history={
            "current_medications": [],
            "known_allergies": [],
            "conditions": [],
            "age": 50,
            "weight_kg": 70,
        },
    )
    assert request.proposed_medicines == ["Aspirin", "Warfarin"]


@pytest.mark.asyncio
async def test_llm_unavailable_triggers_fallback_and_doctor_review_true():
    request = DrugSafetyRequest(**_request_payload())
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )
    assert response.source == "fallback"
    assert response.requires_doctor_review is True


def test_nsaids_with_kidney_disease_flags_contraindication():
    alerts = check_contraindications(["Ibuprofen"], ["chronic kidney disease"])
    assert alerts
    assert any("kidney" in a.condition for a in alerts)


@pytest.mark.asyncio
async def test_risk_score_above_75_sets_critical_level():
    request = DrugSafetyRequest(
        proposed_medicines=["Warfarin", "Aspirin", "Ibuprofen", "Lisinopril", "Spironolactone", "Digoxin", "Amiodarone"],
        patient_history={
            "current_medications": [],
            "known_allergies": [],
            "conditions": [],
            "age": 70,
            "weight_kg": 68,
        },
    )

    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    assert response.patient_risk_score > 75
    assert response.overall_risk_level == "critical"


def test_empty_medicine_list_raises_validation_error():
    with pytest.raises(ValidationError):
        DrugSafetyRequest(
            proposed_medicines=[],
            patient_history={
                "current_medications": [],
                "known_allergies": [],
                "conditions": [],
                "age": 25,
                "weight_kg": 60,
            },
        )


def test_all_five_bonus_interactions_present_in_fallback_dataset():
    data = _load_fallback_data()
    pair_set = {
        tuple(sorted((item["drug_a"].lower(), item["drug_b"].lower())))
        for item in data
    }

    expected_bonus_pairs = {
        tuple(sorted(("warfarin", "amiodarone"))),
        tuple(sorted(("tacrolimus", "voriconazole"))),
        tuple(sorted(("clarithromycin", "simvastatin"))),
        tuple(sorted(("spironolactone", "trimethoprim-sulfamethoxazole"))),
        tuple(sorted(("linezolid", "sertraline"))),
    }

    assert expected_bonus_pairs.issubset(pair_set)


@pytest.mark.asyncio
async def test_latency_under_three_seconds_for_five_drugs():
    request = DrugSafetyRequest(
        proposed_medicines=["Warfarin", "Aspirin", "Ibuprofen", "Lisinopril", "Spironolactone"],
        patient_history={
            "current_medications": ["Omeprazole"],
            "known_allergies": [],
            "conditions": ["hypertension"],
            "age": 55,
            "weight_kg": 78,
        },
    )

    started = time.perf_counter()
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0
    assert response.processing_time_ms >= 1


@pytest.mark.asyncio
async def test_llm_timeout_fast_fallback_path():
    request = DrugSafetyRequest(**_request_payload(proposed_medicines=["Warfarin", "Aspirin", "Ibuprofen"]))
    started = time.perf_counter()
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=SlowLLM(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )
    elapsed = time.perf_counter() - started

    assert response.source == "fallback"
    assert elapsed < 2.0
    assert (
        "llm_unavailable_or_timeout" in response.audit_trail.decision_path
        or "llm_circuit_open" in response.audit_trail.decision_path
    )


@pytest.mark.asyncio
async def test_cache_ttl_expiry_invalidation():
    cache = TTLCache(ttl_seconds=0)
    key = TTLCache.generate_key(["Warfarin"], ["Aspirin"])
    await cache.set(key, {"ok": True})
    await asyncio.sleep(0.01)
    value = await cache.get(key)
    assert value is None


@pytest.mark.asyncio
async def test_audit_trail_fields_present():
    request = DrugSafetyRequest(**_request_payload())
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    assert response.audit_trail.decision_path
    assert isinstance(response.audit_trail.validation_failures_count, int)
    assert response.audit_trail.review_reason != ""
    assert response.rules_version
    assert response.fallback_dataset_version
    assert response.recommended_actions


@pytest.mark.asyncio
async def test_invalid_llm_output_chaos_falls_back_safely():
    request = DrugSafetyRequest(**_request_payload(proposed_medicines=["Warfarin", "Aspirin", "Ibuprofen"]))
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=InvalidJsonLLM(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )
    assert response.source == "fallback"
    assert response.interactions
    assert response.requires_doctor_review is True


@pytest.mark.asyncio
async def test_golden_case_response_contains_new_structured_fields():
    request = DrugSafetyRequest(
        proposed_medicines=["Warfarin", "Aspirin"],
        patient_history={
            "current_medications": ["Omeprazole"],
            "known_allergies": ["Penicillin"],
            "conditions": ["Bleeding disorder"],
            "age": 67,
            "weight_kg": 72,
        },
    )
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    assert response.history_risk_flags
    assert response.confidence_breakdown.final_confidence == response.confidence_score
    assert response.analysis_mode in {"normal", "degraded_fallback"}
    assert isinstance(response.reconciliation_alerts, list)


@pytest.mark.asyncio
async def test_confidence_calibration_improves_with_richer_context():
    request = DrugSafetyRequest(
        proposed_medicines=["Warfarin", "Aspirin"],
        patient_history={
            "current_medications": ["Omeprazole"],
            "known_allergies": [],
            "conditions": ["Atrial fibrillation"],
            "age": 67,
            "weight_kg": 72,
            "renal_function_egfr": 78,
            "hepatic_impairment": "none",
            "pregnancy_status": "not_pregnant",
            "latest_inr": 2.3,
            "creatinine_mg_dl": 1.0,
        },
    )
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=FakeLLMDown(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    assert 0.0 <= response.confidence_score <= 1.0
    assert response.confidence_breakdown.data_completeness_score >= 0.85
    assert response.confidence_breakdown.final_confidence >= 0.70


@pytest.mark.asyncio
async def test_conflicting_llm_severity_resolves_to_highest():
    _LLM_CIRCUIT.record_success()
    request = DrugSafetyRequest(**_request_payload())
    response = await analyze_drug_safety(
        request=request,
        cache=TTLCache(),
        llm_client=ConflictingSeverityLLM(),
        fallback_interactions=_load_fallback_data(),
        system_prompt="test",
    )

    assert response.source == "llm"
    warfarin_aspirin = [
        i
        for i in response.interactions
        if {i.drug_a.lower(), i.drug_b.lower()} == {"warfarin", "aspirin"}
    ]
    assert len(warfarin_aspirin) == 1
    assert warfarin_aspirin[0].severity == "high"
    assert response.audit_trail.conflict_resolution_policy == "max_severity_wins"


def test_request_validation_error_uses_structured_error_taxonomy():
    with TestClient(app) as client:
        payload = _request_payload()
        payload["patient_history"]["age"] = -99
        response = client.post("/api/v1/analyze", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["error_code"] == "REQ_VALIDATION_ERROR"
        assert body["error"]["category"] == "validation"


def test_idempotency_key_replays_same_response():
    with TestClient(app) as client:
        payload = _request_payload()
        headers = {"Idempotency-Key": "test-key-001"}
        first = client.post("/api/v1/analyze", json=payload, headers=headers)
        second = client.post("/api/v1/analyze", json=payload, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["cache_hit"] is False
        assert first.json()["processing_time_ms"] == second.json()["processing_time_ms"]


def test_idempotency_key_payload_mismatch_returns_conflict():
    with TestClient(app) as client:
        payload_a = _request_payload()
        payload_b = _request_payload(proposed_medicines=["Warfarin"])
        headers = {"Idempotency-Key": "test-key-002"}

        first = client.post("/api/v1/analyze", json=payload_a, headers=headers)
        second = client.post("/api/v1/analyze", json=payload_b, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["error_code"] == "HTTP_ERROR"


def test_governance_and_evidence_trace_fields_present():
    with TestClient(app) as client:
        response = client.post("/api/v1/analyze", json=_request_payload())
        assert response.status_code == 200
        payload = response.json()
        assert payload["governance"]["ruleset_id"]
        assert payload["governance"]["approved_by"]
        assert payload["interactions"]
        assert payload["interactions"][0]["guideline_source"]
        assert payload["interactions"][0]["evidence_quote"]
        assert payload["interactions"][0]["reviewed_at"]


@pytest.mark.asyncio
async def test_fixed_window_rate_limiter_blocks_after_limit():
    limiter = FixedWindowRateLimiter(limit=2, window_seconds=60)
    allowed1, _ = await limiter.allow("127.0.0.1")
    allowed2, _ = await limiter.allow("127.0.0.1")
    allowed3, _ = await limiter.allow("127.0.0.1")

    assert allowed1 is True
    assert allowed2 is True
    assert allowed3 is False
