const byId = (id) => document.getElementById(id);

const form = byId("analyze-form");
const submitBtn = byId("submit-btn");
const sampleBtn = byId("sample-btn");
const formError = byId("form-error");
const loading = byId("loading");
const presetSelect = byId("preset-select");
const exportJsonBtn = byId("export-json-btn");
const printReportBtn = byId("print-report-btn");
const refreshHealthBtn = byId("refresh-health-btn");

const patientCard = byId("patient-card");
const patientSummary = byId("patient-summary");
const historyFlags = byId("history-flags");
const decisionBanner = byId("decision-banner");
const timelineCard = byId("timeline-card");
const decisionTimeline = byId("decision-timeline");
const recommendationsCard = byId("recommendations-card");
const recommendationsList = byId("recommendations");
const summaryCard = byId("summary-card");
const confidenceCard = byId("confidence-card");
const confidenceBreakdown = byId("confidence-breakdown");
const riskMatrixCard = byId("risk-matrix-card");
const severityHeatmap = byId("severity-heatmap");
const riskMatrixBody = byId("risk-matrix-body");
const criticalAlertsCard = byId("critical-alerts-card");
const highRiskCard = byId("high-risk-card");
const interactionsCard = byId("interactions-card");
const riskFactorsCard = byId("risk-factors-card");
const reconciliationCard = byId("reconciliation-card");
const reconciliationContent = byId("reconciliation-content");
const compareResult = byId("compare-result");
const runCompareBtn = byId("run-compare-btn");
const healthWidget = byId("health-widget");
const validationCard = byId("validation-card");

const summary = byId("summary");
const criticalAlerts = byId("critical-alerts");
const highRiskInteractions = byId("high-risk-interactions");
const otherFindings = byId("other-findings");
const riskFactors = byId("risk-factors");
const analysisMeta = byId("analysis-meta");

const appState = {
  lastRequestPayload: null,
  lastResponse: null,
  compareResponse: null,
  matrixRows: [],
  activeFilter: "all",
  completedActionKeys: new Set(),
};

const tagStores = {
  proposed: [],
  current: [],
  allergies: [],
  conditions: [],
  compare: [],
};

const PRESETS = {
  polypharmacy: {
    proposed: ["warfarin", "aspirin", "ibuprofen"],
    current: ["omeprazole"],
    allergies: ["penicillin"],
    conditions: ["chronic kidney disease", "bleeding disorder"],
    age: 67,
    weight: 72,
  },
  allergy: {
    proposed: ["amoxicillin", "cephalexin"],
    current: ["cetirizine"],
    allergies: ["penicillin"],
    conditions: ["sinusitis"],
    age: 34,
    weight: 65,
  },
  renal: {
    proposed: ["metformin", "ibuprofen"],
    current: ["lisinopril"],
    allergies: [],
    conditions: ["chronic kidney disease", "hypertension"],
    age: 61,
    weight: 78,
  },
};

function normalizeToken(input) {
  return input.replace(/\s+/g, " ").trim().toLowerCase();
}

function titleCase(input) {
  return String(input)
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function badge(level) {
  return `<span class="badge ${level}">${level}</span>`;
}

function safeText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function showError(message) {
  formError.classList.remove("hidden");
  formError.textContent = message;
}

function clearError() {
  formError.classList.add("hidden");
  formError.textContent = "";
}

function toListChips(items) {
  if (!items.length) return '<span class="muted">None reported</span>';
  return `<div class="chip-list">${items.map((x) => `<span class="chip">${titleCase(x)}</span>`).join("")}</div>`;
}

function addTag(storeKey, raw) {
  const normalized = normalizeToken(raw);
  if (!normalized || tagStores[storeKey].includes(normalized)) return;
  tagStores[storeKey].push(normalized);
  renderTags(storeKey);
}

function removeTag(storeKey, value) {
  tagStores[storeKey] = tagStores[storeKey].filter((x) => x !== value);
  renderTags(storeKey);
}

function renderTags(storeKey) {
  const container = byId(`${storeKey}-tags`);
  const values = tagStores[storeKey];
  container.classList.toggle("empty", values.length === 0);

  container.innerHTML = values
    .map(
      (value) => `
      <span class="chip">
        ${titleCase(value)}
        <button type="button" data-remove="${safeText(value)}" data-store="${storeKey}" aria-label="Remove ${titleCase(value)}">×</button>
      </span>
    `
    )
    .join("");

  container.querySelectorAll("button[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => removeTag(storeKey, btn.dataset.remove));
  });
}

function wireTagEntry(storeKey) {
  const entry = byId(`${storeKey}-entry`);
  entry.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addTag(storeKey, entry.value);
      entry.value = "";
    }
  });
  entry.addEventListener("blur", () => {
    if (entry.value.trim()) {
      addTag(storeKey, entry.value);
      entry.value = "";
    }
  });
}

function initTagInputs() {
  ["proposed", "current", "allergies", "conditions", "compare"].forEach((key) => {
    renderTags(key);
    wireTagEntry(key);
  });
}

function classifyDecision(data) {
  if (data.overall_risk_level === "critical" || !data.safe_to_prescribe) {
    return {
      style: "critical",
      title: "🚨 CRITICAL RISK DETECTED",
      line1: "This prescription is NOT SAFE for this patient.",
      line2: "Immediate doctor review required.",
      state: "❌ NOT SAFE TO PRESCRIBE",
    };
  }
  if (data.overall_risk_level === "high" || data.overall_risk_level === "medium") {
    return {
      style: "warning",
      title: "⚠️ USE WITH CAUTION",
      line1: "Potential risks detected for this medication plan.",
      line2: "Clinical monitoring and review are advised.",
      state: "⚠️ USE WITH CAUTION",
    };
  }
  return {
    style: "safe",
    title: "✅ SAFE TO PRESCRIBE",
    line1: "No major safety conflicts identified.",
    line2: "Proceed with routine clinical monitoring.",
    state: "✅ SAFE",
  };
}

function buildPayload(overrideProposed = null) {
  return {
    proposed_medicines: (overrideProposed || tagStores.proposed).map(titleCase),
    patient_history: {
      current_medications: tagStores.current.map(titleCase),
      known_allergies: tagStores.allergies.map(titleCase),
      conditions: tagStores.conditions.map(titleCase),
      age: Number(byId("age").value),
      weight_kg: Number(byId("weight").value),
    },
  };
}

async function analyzePayload(payload) {
  const response = await fetch("/api/v1/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data?.message || data?.detail || "Request failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function renderPatientSummary(data, requestPayload) {
  const history = requestPayload.patient_history || {};
  patientSummary.innerHTML = `
    <article class="patient-item"><strong>Age</strong><br/>${history.age ?? "-"} years</article>
    <article class="patient-item"><strong>Weight</strong><br/>${history.weight_kg ?? "-"} kg</article>
    <article class="patient-item"><strong>Proposed Medicines</strong><br/>${toListChips(requestPayload.proposed_medicines || [])}</article>
    <article class="patient-item"><strong>Current Medications</strong><br/>${toListChips(history.current_medications || [])}</article>
    <article class="patient-item"><strong>Conditions</strong><br/>${toListChips(history.conditions || [])}</article>
    <article class="patient-item"><strong>Allergies</strong><br/>${toListChips(history.known_allergies || [])}</article>
  `;

  const flags = data.history_risk_flags || [];
  historyFlags.innerHTML = flags.length
    ? flags.map((f) => `<span class="flag-pill">${safeText(f.replaceAll("_", " "))}</span>`).join("")
    : "<span class='muted'>No elevated history flags.</span>";
}

function renderTimeline(data) {
  const path = data.audit_trail?.decision_path || [];
  const llmStatus = data.audit_trail?.llm_status || "not_attempted";

  const stepMap = {
    cache_lookup: "Prescription input received",
    cache_hit: "Previous safety result reused",
    cache_miss: "Fresh analysis initiated",
    rules_executed: "Clinical safety rules checked",
    llm_attempted: "AI clinical review attempted",
    llm_validated: "AI output clinically validated",
    llm_used: "AI-derived interaction findings used",
    llm_unavailable_or_timeout: "AI unavailable, fallback logic engaged",
    llm_circuit_open: "AI temporarily bypassed for stability",
    fallback_used: "Validated fallback dataset applied",
    non_empty_guard_applied: "Safety baseline recommendation added",
    policy_block_critical_allergy: "Critical allergy safety block enforced",
  };

  const friendly = [];
  const pushUnique = (label) => {
    if (!label || friendly.includes(label)) return;
    friendly.push(label);
  };

  path.forEach((step) => {
    if (stepMap[step]) pushUnique(stepMap[step]);
  });

  if (llmStatus === "used") {
    pushUnique("AI review completed successfully");
  } else if (llmStatus === "timeout" || llmStatus === "unavailable" || llmStatus === "circuit_open") {
    pushUnique("System safely switched to deterministic fallback");
  }

  pushUnique("Final clinical recommendation generated");

  const rows = friendly.slice(0, 6);
  decisionTimeline.innerHTML = rows
    .map((step) => `<li><span class="timeline-dot"></span><span>${safeText(step)}</span></li>`)
    .join("");
  timelineCard.classList.remove("hidden");
}

function renderActionCenter(data) {
  const actions = (data.recommended_actions || []).length
    ? data.recommended_actions
    : [{ code: "MONITOR_PATIENT", text: "Continue routine monitoring.", priority: "low" }];

  recommendationsList.innerHTML = actions
    .map((a, idx) => {
      const key = `${a.code}:${a.text}:${idx}`;
      const checked = appState.completedActionKeys.has(key) ? "checked" : "";
      return `
        <article class="action-card ${a.priority}">
          <div class="action-top">
            <div>
              <strong>${safeText(a.code.replaceAll("_", " "))}</strong>
              ${badge(a.priority)}
            </div>
            <label class="check-wrap">
              <input type="checkbox" data-action-key="${safeText(key)}" ${checked}/>
              Done
            </label>
          </div>
          <p>${safeText(a.text)}</p>
        </article>
      `;
    })
    .join("");

  recommendationsList.querySelectorAll("input[data-action-key]").forEach((input) => {
    input.addEventListener("change", () => {
      const key = input.dataset.actionKey;
      if (!key) return;
      if (input.checked) appState.completedActionKeys.add(key);
      else appState.completedActionKeys.delete(key);
    });
  });

  recommendationsCard.classList.remove("hidden");
}

function buildMatrixRows(data) {
  const rows = [];

  (data.interactions || []).forEach((i) => {
    rows.push({
      pair: `${i.drug_a} + ${i.drug_b}`,
      severity: i.severity,
      mechanism: i.mechanism,
      recommendation: i.clinical_recommendation,
      isHistoryConflict: false,
    });
  });

  (data.allergy_alerts || []).forEach((a) => {
    rows.push({
      pair: `${a.medicine} + Allergy profile`,
      severity: a.severity,
      mechanism: a.reason,
      recommendation: "Avoid cross-reactive class medications.",
      isHistoryConflict: true,
    });
  });

  (data.contraindication_alerts || []).forEach((c) => {
    rows.push({
      pair: `${c.medicine} + ${c.condition}`,
      severity: c.severity,
      mechanism: c.reason,
      recommendation: "Use safer alternative and review contraindication.",
      isHistoryConflict: true,
    });
  });

  (data.reconciliation_alerts || []).forEach((r) => {
    rows.push({
      pair: `${r.medicines.join(" + ")} (${r.medicine_class})`,
      severity: r.severity,
      mechanism: r.reason,
      recommendation: "Reconcile duplicate therapy before prescribing.",
      isHistoryConflict: true,
    });
  });

  return rows;
}

function severityRank(severity) {
  return { critical: 4, high: 3, medium: 2, low: 1 }[severity] || 1;
}

function renderHeatmap(rows) {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  rows.forEach((r) => {
    if (counts[r.severity] !== undefined) counts[r.severity] += 1;
  });
  severityHeatmap.innerHTML = Object.keys(counts)
    .map((level) => `<div class="heat-cell ${level}"><span>${level}</span><strong>${counts[level]}</strong></div>`)
    .join("");
}

function filteredRows() {
  const rows = [...appState.matrixRows].sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  if (appState.activeFilter === "all") return rows;
  if (appState.activeFilter === "history") return rows.filter((r) => r.isHistoryConflict);
  return rows.filter((r) => r.severity === appState.activeFilter);
}

function renderMatrixTable() {
  const rows = filteredRows();
  riskMatrixBody.innerHTML = rows.length
    ? rows
        .map(
          (r) => `
      <tr>
        <td>${safeText(r.pair)}</td>
        <td>${badge(r.severity)}</td>
        <td>${safeText(r.mechanism)}</td>
        <td>${safeText(r.recommendation)}</td>
      </tr>
    `
        )
        .join("")
    : `<tr><td colspan="4" class="muted">No findings for selected filter.</td></tr>`;
}

function renderConfidence(data) {
  const c = data.confidence_breakdown || {};
  const metrics = [
    { key: "model_confidence", label: "Model confidence", value: c.model_confidence ?? data.confidence_score ?? 0 },
    { key: "rule_confidence", label: "Rule confidence", value: c.rule_confidence ?? 0 },
    { key: "data_completeness_score", label: "Data completeness", value: c.data_completeness_score ?? 0 },
    { key: "final_confidence", label: "Final confidence", value: c.final_confidence ?? data.confidence_score ?? 0 },
  ];

  confidenceBreakdown.innerHTML = metrics
    .map((m) => {
      const pct = Math.max(0, Math.min(100, Math.round((m.value || 0) * 100)));
      return `
      <article class="confidence-item">
        <div class="confidence-head"><span>${safeText(m.label)}</span><strong>${pct}%</strong></div>
        <div class="progress"><span style="width:${pct}%"></span></div>
      </article>
    `;
    })
    .join("");
  confidenceCard.classList.remove("hidden");
}

function renderAlerts(data) {
  const criticalItems = [];

  (data.allergy_alerts || []).forEach((a) => {
    if (a.severity === "critical") {
      criticalItems.push(`
        <article class="item">
          <div><strong>${safeText(a.medicine)}</strong> ${badge(a.severity)}</div>
          <div>${safeText(a.reason)}</div>
          <div><strong>Recommendation:</strong> Avoid prescribing and choose a non-cross-reactive alternative.</div>
        </article>
      `);
    }
  });

  (data.contraindication_alerts || []).forEach((c) => {
    if (c.severity === "critical") {
      criticalItems.push(`
        <article class="item">
          <div><strong>${safeText(c.medicine)}</strong> with <strong>${safeText(titleCase(c.condition))}</strong> ${badge(c.severity)}</div>
          <div>${safeText(c.reason)}</div>
          <div><strong>Recommendation:</strong> Contraindicated in this patient context; review immediate alternatives.</div>
        </article>
      `);
    }
  });

  criticalAlerts.innerHTML = criticalItems.length ? criticalItems.join("") : "<p class='muted'>No critical alerts found.</p>";
  criticalAlertsCard.classList.remove("hidden");

  const highRisk = (data.interactions || []).filter((i) => i.severity === "high");
  highRiskInteractions.innerHTML = highRisk.length
    ? highRisk
        .map(
          (item) => `
      <details class="interaction-details">
        <summary>
          <span>${safeText(item.drug_a)} + ${safeText(item.drug_b)}</span>
          ${badge(item.severity)}
        </summary>
        <div class="interaction-body">
          <p><strong>Mechanism:</strong> ${safeText(item.mechanism)}</p>
          <p><strong>Recommendation:</strong> ${safeText(item.clinical_recommendation)}</p>
        </div>
      </details>`
        )
        .join("")
    : "<p class='muted'>No high-risk interactions identified.</p>";
  highRiskCard.classList.remove("hidden");

  const others = (data.interactions || []).filter((i) => i.severity !== "high");
  otherFindings.innerHTML = others.length
    ? others
        .map(
          (item) => `
      <details class="interaction-details">
        <summary>
          <span>${safeText(item.drug_a)} + ${safeText(item.drug_b)}</span>
          ${badge(item.severity)}
        </summary>
        <div class="interaction-body">
          <p><strong>Mechanism:</strong> ${safeText(item.mechanism)}</p>
          <p><strong>Recommendation:</strong> ${safeText(item.clinical_recommendation)}</p>
        </div>
      </details>`
        )
        .join("")
    : "<p class='muted'>No additional findings.</p>";
  interactionsCard.classList.remove("hidden");
}

function renderRiskFactors(data, requestPayload) {
  const items = [];
  if ((data.interactions || []).some((i) => i.severity === "high" || i.severity === "critical")) {
    items.push("High-severity interaction detected");
  }
  if ((data.contraindication_alerts || []).length) {
    items.push("Contraindication present");
  }
  const age = requestPayload.patient_history?.age;
  if (typeof age === "number" && (age < 12 || age > 65)) {
    items.push("Age-related risk");
  }
  if ((data.allergy_alerts || []).length) {
    items.push("Allergy or class cross-reactivity risk");
  }
  if ((data.reconciliation_alerts || []).length) {
    items.push("Medication reconciliation needed");
  }

  if (!items.length) items.push("No major risk amplifiers detected beyond baseline assessment.");
  riskFactors.innerHTML = items.map((i) => `<li>${safeText(i)}</li>`).join("");
  riskFactorsCard.classList.remove("hidden");
}

function renderReconciliation(data, requestPayload) {
  const alerts = data.reconciliation_alerts || [];
  const medsNow = requestPayload.patient_history?.current_medications || [];
  const medsProposed = requestPayload.proposed_medicines || [];

  const alertHtml = alerts.length
    ? alerts
        .map(
          (r) => `
      <article class="recon-item">
        <h4>${safeText(r.medicine_class)} ${badge(r.severity)}</h4>
        <p><strong>Medicines:</strong> ${safeText(r.medicines.join(", "))}</p>
        <p>${safeText(r.reason)}</p>
      </article>`
        )
        .join("")
    : "<p class='muted'>No duplicate therapy class alerts found.</p>";

  reconciliationContent.innerHTML = `
    <article class="recon-pane">
      <h4>Current Medications</h4>
      ${toListChips(medsNow)}
    </article>
    <article class="recon-pane">
      <h4>Proposed Medications</h4>
      ${toListChips(medsProposed)}
    </article>
    <article class="recon-pane recon-alerts">
      <h4>Reconciliation Alerts</h4>
      ${alertHtml}
    </article>
  `;
  reconciliationCard.classList.remove("hidden");
}

function renderSummary(data, decision) {
  summary.innerHTML = `
    <div class="summary-grid">
      <div class="metric"><strong>Risk score</strong><br/>${data.patient_risk_score}/100</div>
      <div class="metric"><strong>Risk level</strong><br/>${badge(data.overall_risk_level)}</div>
      <div class="metric"><strong>Confidence</strong><br/>${Math.round((data.confidence_score || 0) * 100)}%</div>
      <div class="metric"><strong>Safety status</strong><br/><span class="safety-state ${decision.style}">${decision.state}</span></div>
      <div class="metric"><strong>Doctor review</strong><br/>${data.requires_doctor_review ? "Required" : "Not required"}</div>
      <div class="metric"><strong>Analysis mode</strong><br/>${safeText(data.analysis_mode || "normal")}</div>
      <div class="metric"><strong>Rules version</strong><br/>${safeText(data.rules_version || "-")}</div>
      <div class="metric"><strong>Fallback dataset</strong><br/>${safeText(data.fallback_dataset_version || "-")}</div>
      <div class="metric"><strong>Processing time</strong><br/>${safeText(data.processing_time_ms)} ms</div>
    </div>
    <p class="muted">Designed for clinical review: highlight safety risks first, then recommendations.</p>
  `;
}

function renderRiskMatrix(data) {
  appState.matrixRows = buildMatrixRows(data);
  renderHeatmap(appState.matrixRows);
  renderMatrixTable();
  riskMatrixCard.classList.remove("hidden");
}

function showResults(data, requestPayload) {
  appState.lastRequestPayload = requestPayload;
  appState.lastResponse = data;
  exportJsonBtn.disabled = false;
  printReportBtn.disabled = false;

  patientCard.classList.remove("hidden");
  summaryCard.classList.remove("hidden");
  validationCard.classList.remove("hidden");

  renderPatientSummary(data, requestPayload);
  renderTimeline(data);
  renderActionCenter(data);

  const decision = classifyDecision(data);
  decisionBanner.className = `decision-banner ${decision.style}`;
  decisionBanner.innerHTML = `
    <h2>${decision.title}</h2>
    <p>${decision.line1}</p>
    <p>${decision.line2}</p>
  `;
  decisionBanner.classList.remove("hidden");

  renderSummary(data, decision);
  renderConfidence(data);
  renderRiskMatrix(data);
  renderAlerts(data);
  renderRiskFactors(data, requestPayload);
  renderReconciliation(data, requestPayload);

  const freshness = data.cache_hit ? "Cache Hit" : "Fresh Analysis";
  analysisMeta.textContent = `${freshness} • ${data.processing_time_ms} ms • Confidence ${Math.round((data.confidence_score || 0) * 100)}% • LLM ${data.audit_trail?.llm_status || "unknown"}`;
}

async function refreshHealthWidget() {
  healthWidget.innerHTML = "<p class='muted'>Loading health metrics...</p>";
  try {
    const response = await fetch("/api/v1/health");
    const data = await response.json();
    if (!response.ok) throw new Error(data?.message || "Health request failed");

    const cache = data.cache || {};
    const circuit = data.llm_circuit_breaker || {};
    const runtime = data.engine_runtime || {};
    const llm = data.llm || {};

    healthWidget.innerHTML = `
      <article class="health-item"><strong>Status</strong><br/>${safeText(data.status)}</article>
      <article class="health-item"><strong>Uptime</strong><br/>${safeText(data.uptime_seconds)}s</article>
      <article class="health-item"><strong>LLM</strong><br/>${safeText(llm.status || "unknown")}</article>
      <article class="health-item"><strong>Circuit</strong><br/>${safeText(circuit.state || "unknown")}</article>
      <article class="health-item"><strong>Cache hit ratio</strong><br/>${Math.round((cache.hit_ratio || 0) * 100)}%</article>
      <article class="health-item"><strong>Engine avg</strong><br/>${safeText(runtime.average_processing_ms || 0)} ms</article>
    `;
  } catch (error) {
    healthWidget.innerHTML = `<p class='error'>Failed to load health metrics: ${safeText(error.message)}</p>`;
  }
}

function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset) return;
  tagStores.proposed = [...preset.proposed];
  tagStores.current = [...preset.current];
  tagStores.allergies = [...preset.allergies];
  tagStores.conditions = [...preset.conditions];
  tagStores.compare = [];
  renderTags("proposed");
  renderTags("current");
  renderTags("allergies");
  renderTags("conditions");
  renderTags("compare");
  byId("age").value = String(preset.age);
  byId("weight").value = String(preset.weight);
}

function initFilterButtons() {
  document.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      appState.activeFilter = btn.dataset.filter || "all";
      document.querySelectorAll(".filter-btn").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      renderMatrixTable();
    });
  });
}

async function runScenarioComparison() {
  clearError();
  if (!appState.lastRequestPayload || !appState.lastResponse) {
    compareResult.className = "compare-result error";
    compareResult.textContent = "Run the primary analysis first before comparing scenarios.";
    return;
  }
  if (!tagStores.compare.length) {
    compareResult.className = "compare-result error";
    compareResult.textContent = "Add at least one alternative proposed medicine to compare.";
    return;
  }

  runCompareBtn.disabled = true;
  runCompareBtn.textContent = "Comparing...";
  compareResult.className = "compare-result muted";
  compareResult.textContent = "Running comparison...";

  try {
    const comparePayload = buildPayload(tagStores.compare);
    const alt = await analyzePayload(comparePayload);
    appState.compareResponse = alt;

    const base = appState.lastResponse;
    const riskDelta = alt.patient_risk_score - base.patient_risk_score;
    const baseCritical = (base.interactions || []).filter((i) => i.severity === "high").length;
    const altCritical = (alt.interactions || []).filter((i) => i.severity === "high").length;

    compareResult.className = "compare-result";
    compareResult.innerHTML = `
      <div class="compare-grid">
        <article>
          <h4>Primary plan</h4>
          <p><strong>Risk:</strong> ${base.patient_risk_score}</p>
          <p><strong>Level:</strong> ${safeText(base.overall_risk_level)}</p>
          <p><strong>High interactions:</strong> ${baseCritical}</p>
        </article>
        <article>
          <h4>Alternative plan</h4>
          <p><strong>Risk:</strong> ${alt.patient_risk_score}</p>
          <p><strong>Level:</strong> ${safeText(alt.overall_risk_level)}</p>
          <p><strong>High interactions:</strong> ${altCritical}</p>
        </article>
      </div>
      <p><strong>Risk delta:</strong> ${riskDelta >= 0 ? "+" : ""}${riskDelta}</p>
      <p><strong>Recommendation:</strong> ${riskDelta <= 0 ? "Alternative appears safer or equal by score." : "Primary plan currently appears safer."}</p>
    `;
  } catch (error) {
    compareResult.className = "compare-result error";
    compareResult.textContent = `Comparison failed: ${error.message}`;
  } finally {
    runCompareBtn.disabled = false;
    runCompareBtn.textContent = "Compare Scenarios";
  }
}

function exportCurrentJson() {
  if (!appState.lastResponse || !appState.lastRequestPayload) return;
  const report = {
    generated_at: new Date().toISOString(),
    request: appState.lastRequestPayload,
    response: appState.lastResponse,
    compare_response: appState.compareResponse,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `evodoc-report-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

sampleBtn.addEventListener("click", () => {
  applyPreset("polypharmacy");
  presetSelect.value = "polypharmacy";
});

presetSelect.addEventListener("change", () => {
  if (presetSelect.value === "none") return;
  applyPreset(presetSelect.value);
});

refreshHealthBtn.addEventListener("click", refreshHealthWidget);
runCompareBtn.addEventListener("click", runScenarioComparison);
exportJsonBtn.addEventListener("click", exportCurrentJson);
printReportBtn.addEventListener("click", () => window.print());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  submitBtn.disabled = true;
  submitBtn.textContent = "Analyzing...";
  loading.classList.remove("hidden");

  try {
    const payload = buildPayload();
    if (!payload.proposed_medicines.length) {
      showError("Please provide at least one proposed medicine.");
      return;
    }

    const data = await analyzePayload(payload);
    showResults(data, payload);
  } catch (error) {
    showError(`Network or server error: ${error.message}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Run Safety Analysis";
    loading.classList.add("hidden");
  }
});

initTagInputs();
initFilterButtons();
refreshHealthWidget();
