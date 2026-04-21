from __future__ import annotations

from difflib import SequenceMatcher, get_close_matches
from typing import Any

from models import ContraindicationAlert
from rules.allergy_classes import ALLERGY_CLASS_MAP


DRUG_GROUPS: dict[str, list[str]] = {
    "NSAIDs": ALLERGY_CLASS_MAP["NSAIDS"],
    "METFORMIN": ["metformin"],
    "ACE_INHIBITORS": ALLERGY_CLASS_MAP["ACE_INHIBITORS"],
    "WARFARIN": ["warfarin"],
    "FLUOROQUINOLONES": ALLERGY_CLASS_MAP["FLUOROQUINOLONES"],
    "STATINS": ALLERGY_CLASS_MAP["STATINS"],
    "BETA_BLOCKERS": ["atenolol", "metoprolol", "propranolol", "bisoprolol", "carvedilol"],
    "TRAMADOL": ["tramadol"],
    "DIGOXIN": ["digoxin"],
    "SSRIs": ["sertraline", "fluoxetine", "escitalopram", "paroxetine", "citalopram"],
    "METHOTREXATE": ["methotrexate"],
    "AMINOGLYCOSIDES": ["gentamicin", "amikacin", "tobramycin", "streptomycin"],
    "LITHIUM": ["lithium"],
    "THIAZOLIDINEDIONES": ["pioglitazone", "rosiglitazone"],
}


CONTRAINDICATION_RULES: list[dict[str, Any]] = [
    {
        "drug_pattern": "NSAIDs",
        "condition_pattern": "kidney disease",
        "reason": "Risk of acute kidney injury and GFR decline",
        "severity": "critical",
    },
    {
        "drug_pattern": "NSAIDs",
        "condition_pattern": "peptic ulcer",
        "reason": "High risk of GI bleeding",
        "severity": "high",
    },
    {
        "drug_pattern": "metformin",
        "condition_pattern": "kidney disease",
        "reason": "Risk of lactic acidosis",
        "severity": "critical",
    },
    {
        "drug_pattern": "ACE_INHIBITORS",
        "condition_pattern": "pregnancy",
        "reason": "Fetal renal toxicity",
        "severity": "critical",
    },
    {
        "drug_pattern": "warfarin",
        "condition_pattern": "liver disease",
        "reason": "Impaired synthesis and bleeding risk",
        "severity": "high",
    },
    {
        "drug_pattern": "warfarin",
        "condition_pattern": "bleeding disorder",
        "reason": "Baseline hemostatic defect plus anticoagulation increases major bleeding risk",
        "severity": "high",
    },
    {
        "drug_pattern": "FLUOROQUINOLONES",
        "condition_pattern": "epilepsy",
        "reason": "Lowers seizure threshold",
        "severity": "high",
    },
    {
        "drug_pattern": "STATINS",
        "condition_pattern": "liver disease",
        "reason": "Hepatotoxicity risk",
        "severity": "high",
    },
    {
        "drug_pattern": "BETA_BLOCKERS",
        "condition_pattern": "asthma",
        "reason": "Bronchospasm risk",
        "severity": "high",
    },
    {
        "drug_pattern": "tramadol",
        "condition_pattern": "epilepsy",
        "reason": "Seizure threshold reduction",
        "severity": "high",
    },
    {
        "drug_pattern": "digoxin",
        "condition_pattern": "kidney disease",
        "reason": "Toxicity from reduced clearance",
        "severity": "high",
    },
    {
        "drug_pattern": "SSRIs",
        "condition_pattern": "bleeding disorder",
        "reason": "Platelet aggregation impairment",
        "severity": "medium",
    },
    {
        "drug_pattern": "methotrexate",
        "condition_pattern": "kidney disease",
        "reason": "Accumulation and toxicity",
        "severity": "critical",
    },
    {
        "drug_pattern": "AMINOGLYCOSIDES",
        "condition_pattern": "kidney disease",
        "reason": "Nephrotoxicity amplified",
        "severity": "critical",
    },
    {
        "drug_pattern": "lithium",
        "condition_pattern": "kidney disease",
        "reason": "Toxicity due to reduced excretion",
        "severity": "critical",
    },
    {
        "drug_pattern": "THIAZOLIDINEDIONES",
        "condition_pattern": "heart failure",
        "reason": "Fluid retention and decompensation",
        "severity": "high",
    },
]


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _normalize(values: list[str]) -> list[str]:
    return [" ".join(v.strip().lower().split()) for v in values if v and v.strip()]


def _drug_matches_rule(drug: str, pattern: str, cutoff: float = 0.8) -> bool:
    pattern_upper = pattern.upper()
    expanded = DRUG_GROUPS.get(pattern_upper) or DRUG_GROUPS.get(pattern) or [pattern.lower()]
    if drug in expanded:
        return True
    close = get_close_matches(drug, expanded, n=1, cutoff=cutoff)
    return bool(close)


def _condition_matches_rule(condition: str, pattern: str, cutoff: float = 0.8) -> bool:
    pattern_norm = pattern.lower().strip()
    if pattern_norm in condition or condition in pattern_norm:
        return True
    return _ratio(condition, pattern_norm) >= cutoff


def check_contraindications(proposed: list[str], conditions: list[str]) -> list[ContraindicationAlert]:
    proposed_norm = _normalize(proposed)
    conditions_norm = _normalize(conditions)

    alerts: list[ContraindicationAlert] = []
    seen: set[tuple[str, str, str]] = set()

    for med in proposed_norm:
        for condition in conditions_norm:
            for rule in CONTRAINDICATION_RULES:
                if not _drug_matches_rule(med, str(rule["drug_pattern"])):
                    continue
                if not _condition_matches_rule(condition, str(rule["condition_pattern"])):
                    continue

                key = (med, condition, str(rule["reason"]))
                if key in seen:
                    continue

                seen.add(key)
                alerts.append(
                    ContraindicationAlert(
                        medicine=med.title(),
                        condition=condition,
                        reason=str(rule["reason"]),
                        severity=str(rule["severity"]),
                    )
                )

    return alerts
