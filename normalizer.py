from __future__ import annotations

import re


DRUG_SYNONYM_MAP: dict[str, str] = {
    "paracetamol": "acetaminophen",
    "tylenol": "acetaminophen",
    "crocin": "acetaminophen",
    "panadol": "acetaminophen",
    "metrogyl": "metronidazole",
    "augmentin": "co-amoxiclav",
}


def normalize_free_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip())
    cleaned = re.sub(r"[^a-zA-Z0-9\-\s]", "", cleaned)
    return cleaned


def normalize_drug_name(value: str) -> str:
    cleaned = normalize_free_text(value)
    lowered = cleaned.lower()
    canonical = DRUG_SYNONYM_MAP.get(lowered, lowered)
    return canonical.title()
