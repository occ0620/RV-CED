"""Validate two v4 human reviews and freeze the prediction-independent selection."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
FIELDS = ["reviewer_id", "item_id", "target_modality", "target_polarity", "target_confidence", "target_clarity", "speech_words_understood", "speech_text_match", "subtitles_present", "subtitles_readable", "sync_issue", "processing_error", "notes"]
ALLOWED = {
    "target_polarity": {"-1", "0", "1", "unknown"}, "target_confidence": {"1", "2", "3"},
    "target_clarity": {"1", "2", "3", "unknown"}, "speech_words_understood": {"0", "1", "unknown"},
    "speech_text_match": {"0", "1", "unknown"}, "subtitles_present": {"0", "1", "unknown"},
    "subtitles_readable": {"0", "1", "unknown", "na"}, "sync_issue": {"0", "1", "unknown"},
    "processing_error": {"0", "1", "unknown"},
}
SEED, CAP, MINIMUM = 20261016, 12, 10


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))


def validate(path: Path, reviewer: str, expected: dict[str, dict]) -> list[dict[str, str]]:
    rows = read_csv(path)
    if len(rows) != len(expected): raise ValueError(f"{reviewer}: expected {len(expected)} rows, got {len(rows)}")
    if len({r.get("item_id") for r in rows}) != len(rows): raise ValueError(f"{reviewer}: duplicate item_id")
    for r in rows:
        if set(FIELDS) - set(r): raise ValueError(f"{reviewer}: missing columns")
        if r["reviewer_id"] != reviewer: raise ValueError(f"{reviewer}: reviewer_id mismatch")
        if r["item_id"] not in expected or r["target_modality"] != expected[r["item_id"]]["target_modality"]: raise ValueError(f"{reviewer}: item or modality mismatch")
        for field, allowed in ALLOWED.items():
            if r[field] not in allowed: raise ValueError(f"{reviewer}: invalid or blank {field} at {r['item_id']}: {r[field]!r}")
        if r["subtitles_present"] == "0" and r["subtitles_readable"] != "na": raise ValueError(f"{reviewer}: no subtitle requires readable=na at {r['item_id']}")
        if r["subtitles_present"] == "1" and r["subtitles_readable"] == "na": raise ValueError(f"{reviewer}: subtitle present cannot use readable=na at {r['item_id']}")
    return rows


def agreement(a: list[str], b: list[str]) -> dict[str, float | None]:
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    labels = sorted(set(a) | set(b))
    ca, cb = Counter(a), Counter(b)
    expected = sum((ca[x] / n) * (cb[x] / n) for x in labels)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return {"raw_agreement": observed, "cohen_kappa": kappa}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer-1", required=True, type=Path)
    ap.add_argument("--reviewer-2", required=True, type=Path)
    ap.add_argument("--output", type=Path, default=HERE / "merged_review")
    args = ap.parse_args()
    if args.output.exists(): raise SystemExit("Output exists; refusing to overwrite independent reviews")
    private = {r["item_id"]: r for r in read_csv(HERE / "private/candidate_mapping.csv")}
    r1 = validate(args.reviewer_1, "reviewer_01", private)
    r2 = validate(args.reviewer_2, "reviewer_02", private)
    one, two = ({r["item_id"]: r for r in rows} for rows in (r1, r2))
    args.output.mkdir(parents=True)
    for source, name in ((args.reviewer_1, "reviewer_01.original.csv"), (args.reviewer_2, "reviewer_02.original.csv")):
        (args.output / name).write_bytes(source.read_bytes())

    audit, disagreement_rows, eligible = [], [], {"T": [], "V": []}
    for item_id, meta in private.items():
        a, b = one[item_id], two[item_id]
        reasons = []
        expected = meta["official_target_polarity"]
        for label, r in (("reviewer_01", a), ("reviewer_02", b)):
            if r["target_polarity"] != expected: reasons.append(label + ":target_polarity_not_official")
            if r["target_confidence"] not in {"2", "3"}: reasons.append(label + ":confidence_below_2")
            if r["target_clarity"] not in {"2", "3"}: reasons.append(label + ":clarity_below_2")
            for f, wanted in (("speech_words_understood", "1"), ("speech_text_match", "1"), ("subtitles_present", "0"), ("subtitles_readable", "na"), ("sync_issue", "0"), ("processing_error", "0")):
                if r[f] != wanted: reasons.append(label + ":" + f + "_not_" + wanted)
        passed = not reasons
        if passed: eligible[meta["target_modality"]].append(item_id)
        disagreements = [f for f in ALLOWED if a[f] != b[f]]
        for field in disagreements:
            disagreement_rows.append({"item_id": item_id, "target_modality": meta["target_modality"], "field": field, "reviewer_01": a[field], "reviewer_02": b[field]})
        audit.append({"item_id": item_id, "target_modality": meta["target_modality"], "passed": str(passed), "failure_reasons": "|".join(reasons), "disagreement_fields": "|".join(disagreements)})

    rng = random.Random(SEED)
    selected = {}
    for modality in ("T", "V"):
        ids = sorted(eligible[modality]); rng.shuffle(ids); selected[modality] = sorted(ids[:CAP])
    proceed = all(len(eligible[m]) >= MINIMUM for m in ("T", "V"))
    status = "READY_FOR_V4_INFERENCE" if proceed else "PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES"
    fields = list(audit[0])
    with (args.output / "candidate_decisions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(audit)
    with (args.output / "disagreements.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item_id", "target_modality", "field", "reviewer_01", "reviewer_02"]); w.writeheader(); w.writerows(disagreement_rows)
    frozen = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "status": status, "protocol": "v4-natural-conflict-paired-target-removal",
        "selection_seed": SEED, "selection_cap_per_modality": CAP, "minimum_to_proceed_per_modality": MINIMUM,
        "passing_counts": {m: len(eligible[m]) for m in ("T", "V")}, "selected_item_ids": selected,
        "selected": [{k: meta[k] for k in ("item_id", "clip_id", "target_modality", "official_target_polarity", "media_sha256")} for m in ("T", "V") for item_id in selected[m] for meta in [private[item_id]]],
        "review_files_sha256": {"reviewer_01": sha(args.reviewer_1), "reviewer_02": sha(args.reviewer_2)},
        "selection_used_model_predictions": False, "thresholds_changed_after_review": False,
    }
    (args.output / "v4_selection.frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    field_agreement = {f: agreement([one[i][f] for i in private], [two[i][f] for i in private]) for f in ALLOWED}
    relation_pass = {m: sum(one[i]["target_polarity"] == two[i]["target_polarity"] == private[i]["official_target_polarity"] for i in private if private[i]["target_modality"] == m) / sum(private[i]["target_modality"] == m for i in private) for m in ("T", "V")}
    issue_rates = {}
    for field, bad in (("speech_words_understood", "0"), ("speech_text_match", "0"), ("subtitles_present", "1"), ("sync_issue", "1"), ("processing_error", "1")):
        issue_rates[field] = {
            "reviewer_01": sum(one[i][field] == bad for i in private) / len(private),
            "reviewer_02": sum(two[i][field] == bad for i in private) / len(private),
            "either_reviewer": sum(one[i][field] == bad or two[i][field] == bad for i in private) / len(private),
        }
    summary = {
        "status": status, "total": len(private), "passing_counts": frozen["passing_counts"],
        "selected_counts": {m: len(selected[m]) for m in ("T", "V")},
        "target_polarity_agreement": sum(one[i]["target_polarity"] == two[i]["target_polarity"] for i in private) / len(private),
        "any_field_disagreement_items": sum(bool(r["disagreement_fields"]) for r in audit),
        "field_agreement": field_agreement, "relation_pass_rate": relation_pass, "issue_rates": issue_rates,
        "failure_reason_counts": Counter(x for r in audit for x in r["failure_reasons"].split("|") if x),
    }
    (args.output / "review_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
