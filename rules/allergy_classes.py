from __future__ import annotations

from difflib import get_close_matches

from models import AllergyAlert


ALLERGY_CLASS_MAP: dict[str, list[str]] = {
    "PENICILLINS": ["amoxicillin", "ampicillin", "piperacillin", "flucloxacillin", "co-amoxiclav"],
    "CEPHALOSPORINS": ["cephalexin", "cefuroxime", "ceftriaxone", "cefixime", "cefazolin"],
    "SULFONAMIDES": ["sulfamethoxazole", "trimethoprim-sulfamethoxazole", "dapsone"],
    "NSAIDS": ["ibuprofen", "naproxen", "diclofenac", "ketorolac", "indomethacin", "celecoxib"],
    "STATINS": ["atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin"],
    "FLUOROQUINOLONES": ["ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin"],
    "ACE_INHIBITORS": ["enalapril", "lisinopril", "ramipril", "perindopril", "captopril"],
    "OPIOIDS": ["morphine", "codeine", "tramadol", "oxycodone", "fentanyl", "hydrocodone"],
    "BENZODIAZEPINES": ["diazepam", "lorazepam", "alprazolam", "clonazepam", "midazolam"],
    "TETRACYCLINES": ["doxycycline", "tetracycline", "minocycline", "tigecycline"],
}


def _normalize(values: list[str]) -> list[str]:
    return [" ".join(v.strip().lower().split()) for v in values if v and v.strip()]


def _find_class_for_drug(drug: str) -> str | None:
    lowered = drug.lower()
    for class_name, members in ALLERGY_CLASS_MAP.items():
        if lowered in members:
            return class_name
    return None


def check_allergy_alerts(proposed: list[str], allergies: list[str]) -> list[AllergyAlert]:
    proposed_norm = _normalize(proposed)
    allergies_norm = _normalize(allergies)
    alerts: list[AllergyAlert] = []
    seen: set[tuple[str, str]] = set()

    all_member_drugs = [d for members in ALLERGY_CLASS_MAP.values() for d in members]

    for med in proposed_norm:
        # Direct name match to known allergy
        if med in allergies_norm:
            key = (med, "direct")
            if key not in seen:
                seen.add(key)
                alerts.append(
                    AllergyAlert(
                        medicine=med.title(),
                        reason=f"Direct allergy match with {med}",
                        severity="critical",
                    )
                )

        med_class = _find_class_for_drug(med)
        if not med_class:
            continue

        for allergen in allergies_norm:
            # Allergen can be class name directly
            if allergen.upper() == med_class:
                key = (med, allergen)
                if key not in seen:
                    seen.add(key)
                    alerts.append(
                        AllergyAlert(
                            medicine=med.title(),
                            reason=f"{allergen} class ({med_class})",
                            severity="critical",
                        )
                    )
                continue

            # Allergen can be another member of the same class
            allergen_class = _find_class_for_drug(allergen)
            if allergen_class == med_class:
                key = (med, allergen)
                if key not in seen:
                    seen.add(key)
                    alerts.append(
                        AllergyAlert(
                            medicine=med.title(),
                            reason=f"{allergen} class ({med_class})",
                            severity="critical",
                        )
                    )
                continue

            # Handle misspellings gracefully for allergy input
            close = get_close_matches(allergen, all_member_drugs, n=1, cutoff=0.8)
            if close and _find_class_for_drug(close[0]) == med_class:
                key = (med, allergen)
                if key not in seen:
                    seen.add(key)
                    alerts.append(
                        AllergyAlert(
                            medicine=med.title(),
                            reason=f"{allergen} class ({med_class})",
                            severity="critical",
                        )
                    )

    return alerts
