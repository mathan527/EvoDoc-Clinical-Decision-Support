# 🚀 EvoDoc Clinical Drug Safety Engine

**AI-assisted, safety-governed prescription analysis for real-world clinical decision support.**

EvoDoc is a FastAPI-based backend system with a modern frontend interface that evaluates medication plans for drug interactions, allergy risks, and contraindications using a **hybrid AI + rule-based safety architecture**.

---

## 🧠 Problem Statement

Medication errors remain one of the most preventable sources of patient harm in healthcare. In real prescribing workflows, clinicians often need to evaluate:

- multi-drug interaction risk,
- patient-specific conditions and allergy history,
- age and physiological vulnerability,
- and urgency under time pressure.

A generic drug checker is not enough. A clinically useful system must be **patient-aware**, **deterministic under failure**, and **explicit about confidence and review boundaries**.

EvoDoc addresses this by prioritizing **patient safety over model convenience**.

---

## 💡 Solution Overview

EvoDoc combines:

1. **FastAPI backend APIs** for structured clinical analysis,
2. **Rule engines** for deterministic safety checks,
3. **LLM-assisted reasoning** for richer interaction interpretation,
4. **Strict validation and fallback logic** to prevent unsafe AI output propagation,
5. **Risk + confidence scoring** for transparent decision support,
6. **Doctor-review gating** when confidence or risk demands escalation.

The system is designed so that if the LLM fails, times out, or returns poor output, the analysis still returns a safe, clinically meaningful response.

---

## 🏗️ Architecture

EvoDoc is organized into layered components with explicit boundaries:

- **API Layer (`main.py`)**
  - Request handling
  - Middleware (request ID, guardrails)
  - Error taxonomy
  - Health + analyze endpoints
- **Engine Layer (`engine.py`)**
  - Core orchestration logic
  - Rules, LLM, fallback merge
  - Confidence and risk computation
- **Cache Layer (`cache.py`)**
  - Deterministic keying
  - TTL-based response caching
- **LLM + Validation Pipeline (`llm_client.py`, `validator.py`)**
  - Meditron inference
  - Strict output sanitization/validation
- **Frontend Layer (`frontend/`)**
  - Doctor-focused dashboard for interpretable output

### Text-based flow diagram

```text
User/UI
  ↓
FastAPI API (main.py)
  ↓
Engine Orchestrator (engine.py)
  ├── Rule Engines (allergy/contraindication/risk)
  ├── LLM Client (Ollama Meditron)
  │      ↓
  │   Validator (strict JSON + semantic checks)
  ├── Fallback Dataset (deterministic safety backstop)
  └── Cache (order-independent deterministic key)
  ↓
Structured Clinical Response
```

A compact representation:

```text
User → API → Engine → LLM → Validator → Cache → Response
```

---

## ⚙️ Core Features

- **FastAPI backend with typed contracts**
  - Strong request/response models using Pydantic.
- **Clinical interaction analysis**
  - Detects high/medium/low severity interaction pairs.
- **Patient-aware risk evaluation**
  - Uses age, weight, conditions, allergies, and medication context.
- **Allergy class detection**
  - Captures cross-reactive classes, not only exact string matches.
- **Contraindication engine**
  - Flags unsafe drug-condition combinations.
- **Risk score (0–100)**
  - Unified severity and patient-context-aware score.
- **Hybrid AI + rules validation path**
  - AI adds reasoning; rules enforce guardrails.
- **Deterministic fallback mode**
  - Safe output even when AI is unavailable.
- **Deterministic caching**
  - Order-independent hashing for stable cache behavior.
- **Processing time visibility**
  - Returns `processing_time_ms` per response.
- **Modern clinical UI**
  - Structured, prioritized display for actionable decisions.

---

## 🧠 AI + Safety Design (Very Important)

### Why the LLM is never trusted blindly

LLMs can generate plausible but unsafe outputs. In a clinical context, this is unacceptable. EvoDoc treats model output as **untrusted input** unless it passes strict checks.

### Safety controls

- **Strict parser + schema validation**
  - Rejects malformed payloads.
- **Medication alignment checks**
  - Ensures model-reported drugs map to request context.
- **Severity conflict handling**
  - Deterministic conflict policy (`max_severity_wins`).
- **Low-confidence recommendation penalties**
  - Unsafe language patterns are downgraded.
- **Fallback activation**
  - Triggered on timeout/unavailable/invalid model output.

### Confidence scoring

EvoDoc returns both:

- `confidence_score` (single final score), and
- `confidence_breakdown`:
  - `model_confidence`
  - `rule_confidence`
  - `data_completeness_score`
  - `final_confidence`

### `requires_doctor_review` logic

Escalation is explicitly triggered when:

- confidence is below threshold,
- critical allergy/contraindication exists,
- fallback/degraded path is used,
- or low source-confidence evidence appears.

This creates a clear human-in-the-loop safety boundary.

---

## ⚡ Performance

EvoDoc is optimized for fast clinical feedback:

- Processing time captured in every response (`processing_time_ms`)
- Cached paths return near-instant responses
- Fallback index built at startup for fast pair lookup
- LLM calls bounded by request-time timeout budget

### Optimization strategy

- Keep critical rules deterministic and local
- Make AI path optional, never mandatory
- Cache stable request patterns aggressively
- Fail safe, not slow

---

## 🧩 Caching Strategy

Cache is intentionally deterministic to avoid redundant analysis.

### Key design

- Inputs normalized and sorted:
  - proposed medicines
  - current medications
- Raw normalized key:

```text
sorted(proposed_lower) + "|" + sorted(current_lower)
```

- Final key = `SHA256(raw_key)`

### Why this matters

- **Order-independent behavior**
  - `[Aspirin, Warfarin]` and `[Warfarin, Aspirin]` produce same key.
- **Predictable cache reuse**
  - Reduces repeated expensive analysis.
- **Clinical consistency**
  - Same logical input → same cached output.

### TTL reasoning

- Current TTL: **3600 seconds**
- Rationale:
  - Practical balance between freshness and speed for repeated evaluations
  - Easy migration path to Redis for distributed deployments

---

## 🧪 Test Coverage

The suite validates safety, reliability, and deterministic behavior. Key test categories include:

- allergy critical alert detection
- known interaction presence in fallback data
- order-independent cache hit behavior
- invalid input rejection (`422` path)
- health endpoint runtime/circuit metrics
- contraindication detection
- risk score critical thresholds
- timeout fallback speed behavior
- audit trail metadata presence
- chaos path for invalid LLM output
- conflict resolver correctness
- idempotency replay/mismatch behavior
- structured error taxonomy
- confidence calibration with richer patient context
- rate limiter boundary behavior

Current status in this prototype: **all tests passing** (`pytest`).

---

## 🧬 Risk Scoring System

EvoDoc outputs `patient_risk_score` in range **0–100** with transparent `risk_breakdown`.

### Inputs used

- interaction severity penalties
- allergy penalties
- contraindication penalties
- age modifier

### Output semantics

- **0–25**: low
- **26–50**: medium
- **51–75**: high
- **76–100**: critical

(Threshold mapping is policy-defined and can be tuned by governance.)

The goal is not a black-box score, but a score with an interpretable explanation string.

---

## 🛡️ Clinical Safety Features

### 1) Allergy class detection

- Detects class-level risk (e.g., cross-reactivity patterns)
- Not limited to exact token matches

### 2) Drug-condition contraindication engine

- Uses patient condition list to flag unsafe prescriptions
- Can force non-prescribable decision paths

### 3) Interaction severity handling

- Categorizes findings into high/medium/low
- Applies deterministic conflict resolution
- Generates actionable recommendations from severity + mechanism

### 4) Non-empty safeguard

- Even in degraded mode, system returns structured safety guidance
- Prevents silent failure or empty clinical response

---

## 🖥️ UI/UX Design Philosophy

The frontend is designed for **clinical clarity, not visual noise**.

### Principles

- **Clean clinical interface**
  - Decision-first cards and structured sections
- **Priority-based alerts**
  - Critical/high risks surfaced before secondary findings
- **Motion-assisted readability**
  - Smooth micro-interactions and transitions aligned with modern motion design practices
  - Framer Motion-style animation philosophy is applied for progressive disclosure and cognitive flow
- **Doctor-focused UX**
  - Emphasizes actionability (`recommended_actions`, review gates, confidence explainability)

The frontend remains contract-driven: it renders structured backend outputs, not raw model text.

---

## 📦 Installation Guide

> The commands below are copy-paste ready for local setup.

### 1) Clone repository

```bash
git clone <your-repo-url>
cd evodoc-safety-engine
```

### 2) Create virtual environment

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 3) Install backend dependencies

```bash
pip install -r requirements.txt
```

### 4) Configure environment

```bash
copy .env.example .env
```

(Use `cp .env.example .env` on macOS/Linux.)

### 5) Pull local LLM model

```bash
ollama pull meditron
```

### 6) Run backend API

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

If 8000 is occupied:

```bash
uvicorn main:app --host 127.0.0.1 --port 8001
```

### 7) Run frontend

Frontend is served by FastAPI static hosting from `/` in this prototype.

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/docs`

---

## ▶️ Usage Example

### Sample input (request)

```json
{
  "proposed_medicines": ["Warfarin", "Aspirin"],
  "patient_history": {
    "current_medications": ["Omeprazole"],
    "known_allergies": ["Penicillin"],
    "conditions": ["Atrial fibrillation"],
    "age": 67,
    "weight_kg": 72,
    "renal_function_egfr": 78,
    "hepatic_impairment": "none",
    "pregnancy_status": "not_pregnant",
    "latest_inr": 2.3,
    "creatinine_mg_dl": 1.0
  }
}
```

### Sample output (response excerpt)

```json
{
  "interactions": [
    {
      "drug_a": "Warfarin",
      "drug_b": "Aspirin",
      "severity": "high",
      "mechanism": "Additive anticoagulation and antiplatelet effects increase bleeding risk",
      "clinical_recommendation": "Avoid combination unless clinically essential; monitor INR and bleeding signs",
      "source_confidence": "high",
      "evidence_level": "A",
      "guideline_source": "FDA label + BNF severe interaction guidance"
    }
  ],
  "safe_to_prescribe": false,
  "overall_risk_level": "high",
  "patient_risk_score": 78,
  "confidence_score": 0.80,
  "requires_doctor_review": true,
  "analysis_mode": "normal",
  "audit_trail": {
    "decision_path": ["cache_lookup", "cache_miss", "rules_executed", "llm_attempted", "llm_validated", "llm_used"],
    "llm_status": "used"
  },
  "governance": {
    "ruleset_id": "evodoc-clinical-safety-core",
    "ruleset_version": "2026.04.19.2"
  },
  "processing_time_ms": 9
}
```

---

## 📁 Project Structure

```text
evodoc-safety-engine/
├─ main.py
├─ engine.py
├─ cache.py
├─ models.py
├─ validator.py
├─ llm_client.py
├─ idempotency.py
├─ rate_limiter.py
├─ error_taxonomy.py
├─ audit_sink.py
├─ normalizer.py
├─ data/
│  └─ fallback_interactions.json
├─ rules/
│  ├─ allergy_classes.py
│  ├─ contraindications.py
│  └─ risk_scorer.py
├─ frontend/
│  ├─ index.html
│  └─ static/
│     ├─ app.js
│     └─ styles.css
├─ prompts/
│  └─ system_prompt.txt
├─ tests/
│  └─ test_engine.py
├─ .env.example
├─ requirements.txt
└─ README.md
```

---

## 🔧 Tech Stack

### Backend

- **Python 3.11+**
- **FastAPI**
- **Pydantic v2**
- **Uvicorn**

### AI / Safety

- **Ollama (local inference runtime)**
- **Meditron (medical-domain model)**
- **Custom LLM validation/sanitization pipeline**
- **Deterministic clinical fallback dataset**

### Frontend

- **HTML/CSS/JavaScript (modular static app)**
- Motion-design friendly UI interactions (Framer Motion-style UX principles)

### Quality / Tooling

- **Pytest**
- **Typed response contracts**
- **Structured error taxonomy**

---

## 🚧 Future Improvements

1. **Expanded medical knowledge base**
   - Incorporate broader evidence sources and periodic updates.
2. **EHR integration**
   - Pull patient data context securely from hospital systems in real time.
3. **Model calibration and fine-tuning**
   - Improve confidence calibration for local population-specific data.
4. **Distributed cache + queue architecture**
   - Move from in-memory cache to Redis for horizontal scale.
5. **Policy-as-code governance**
   - Versioned review policies with approval workflows.
6. **Observability stack**
   - OpenTelemetry traces, metrics dashboards, and alerting.

---

## 🧠 Key Learnings

Building EvoDoc reinforced production-grade engineering principles for AI systems in sensitive domains:

- **Reliability beats novelty** in clinical workflows.
- **AI must be bounded by deterministic safety controls**.
- **Explainability and audit trails are product requirements, not extras**.
- **Confidence without escalation logic is incomplete safety design**.
- **Typed contracts + tests are the backbone of maintainable backend systems**.

---

## Final Note

EvoDoc is designed as a practical blueprint for **backend-first clinical decision support systems**: resilient under uncertainty, transparent in outputs, and aligned with real-world patient safety constraints.
# EvoDoc-Clinical-Decision-Support
