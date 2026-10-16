"""Archive and audit received v4 review CSVs without imputing missing ratings.

This intake step is deliberately separate from ``merge_reviews.py``.  It accepts
partial exports, preserves the exact input bytes, lists missing cells, and only
computes diagnostics that are supported by the received ratings.  It never
copies one reviewer's answers into the other reviewer's record.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
FIELDS = [
    "reviewer_id", "item_id", "target_modality", "target_polarity",
    "target_confidence", "target_clarity", "speech_words_understood",
    "speech_text_match", "subtitles_present", "subtitles_readable",
    "sync_issue", "processing_error", "notes",
]
STEP1 = ["target_polarity", "target_confidence", "target_clarity"]
STEP2 = [
    "speech_words_understood", "speech_text_match", "subtitles_present",
    "subtitles_readable", "sync_issue", "processing_error",
]
ALLOWED = {
    "target_polarity": {"-1", "0", "1", "unknown"},
    "target_confidence": {"1", "2", "3"},
    "target_clarity": {"1", "2", "3", "unknown"},
    "speech_words_understood": {"0", "1", "unknown"},
    "speech_text_match": {"0", "1", "unknown"},
    "subtitles_present": {"0", "1", "unknown"},
    "subtitles_readable": {"0", "1", "unknown", "na"},
    "sync_issue": {"0", "1", "unknown"},
    "processing_error": {"0", "1", "unknown"},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def agreement(a: list[str], b: list[str]) -> dict[str, float | None]:
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    labels = sorted(set(a) | set(b))
    ca, cb = Counter(a), Counter(b)
    expected = sum((ca[x] / n) * (cb[x] / n) for x in labels)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return {"n": n, "raw_agreement": observed, "cohen_kappa": kappa}


def inspect_review(
    path: Path, reviewer_id: str, expected: dict[str, dict[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    rows = read_csv(path)
    structural_errors: list[str] = []
    missing: list[dict[str, str]] = []
    if len(rows) != len(expected):
        structural_errors.append(f"expected {len(expected)} rows, got {len(rows)}")
    item_ids = [row.get("item_id", "") for row in rows]
    if len(set(item_ids)) != len(item_ids):
        structural_errors.append("duplicate item_id")
    for row in rows:
        item_id = row.get("item_id", "")
        absent_columns = sorted(set(FIELDS) - set(row))
        if absent_columns:
            structural_errors.append(
                f"{item_id or '<blank>'}: missing columns {absent_columns}"
            )
            continue
        if row["reviewer_id"] != reviewer_id:
            structural_errors.append(f"{item_id}: reviewer_id mismatch")
        if item_id not in expected:
            structural_errors.append(f"{item_id}: unexpected item_id")
        elif row["target_modality"] != expected[item_id]["target_modality"]:
            structural_errors.append(f"{item_id}: target_modality mismatch")
        for field, allowed in ALLOWED.items():
            value = row[field].strip()
            if not value:
                missing.append(
                    {
                        "reviewer_id": reviewer_id,
                        "item_id": item_id,
                        "target_modality": row.get("target_modality", ""),
                        "stage": "step1" if field in STEP1 else "step2",
                        "field": field,
                    }
                )
            elif value not in allowed:
                structural_errors.append(
                    f"{item_id}: invalid {field}={value!r}"
                )
        present = row.get("subtitles_present", "").strip()
        readable = row.get("subtitles_readable", "").strip()
        if present == "0" and readable and readable != "na":
            structural_errors.append(
                f"{item_id}: subtitles_present=0 requires subtitles_readable=na"
            )
        if present == "1" and readable == "na":
            structural_errors.append(
                f"{item_id}: subtitles_present=1 cannot use subtitles_readable=na"
            )
    return rows, missing, structural_errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewer-1", required=True, type=Path)
    parser.add_argument("--reviewer-2", required=True, type=Path)
    parser.add_argument("--legacy-v3", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--reviewer-2-step2-not-independent",
        action="store_true",
        help="Record that reviewer 2 did not independently perform step 2.",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output exists; refusing to overwrite received reviews")

    expected_rows = read_csv(HERE / "private" / "candidate_mapping.csv")
    expected = {row["item_id"]: row for row in expected_rows}
    rows1, missing1, errors1 = inspect_review(
        args.reviewer_1, "reviewer_01", expected
    )
    rows2, missing2, errors2 = inspect_review(
        args.reviewer_2, "reviewer_02", expected
    )

    args.output.mkdir(parents=True)
    originals = args.output / "originals"
    originals.mkdir()
    sources = [
        (args.reviewer_1, originals / "reviewer_01.v4.received.csv", "v4-reviewer-1"),
        (args.reviewer_2, originals / "reviewer_02.v4.received.csv", "v4-reviewer-2"),
    ]
    if args.legacy_v3:
        sources.append(
            (args.legacy_v3, originals / "reviewer_01.v3.received.csv", "legacy-v3-excluded")
        )
    hashes = {}
    for source, destination, label in sources:
        shutil.copyfile(source, destination)
        hashes[label] = {
            "source_name": source.name,
            "archived_as": destination.relative_to(args.output).as_posix(),
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }

    missing = missing1 + missing2
    with (args.output / "missing_fields.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["reviewer_id", "item_id", "target_modality", "stage", "field"],
        )
        writer.writeheader()
        writer.writerows(missing)

    by1 = {row["item_id"]: row for row in rows1}
    by2 = {row["item_id"]: row for row in rows2}
    common = [item_id for item_id in expected if item_id in by1 and item_id in by2]
    step1_complete = all(
        by1[item_id][field].strip() and by2[item_id][field].strip()
        for item_id in common
        for field in STEP1
    ) and len(common) == len(expected)
    step1_agreement = {
        field: agreement(
            [by1[item_id][field] for item_id in common],
            [by2[item_id][field] for item_id in common],
        )
        for field in STEP1
    } if step1_complete else {}
    protocol = json.loads((HERE / "protocol.fixed.json").read_text(encoding="utf-8"))
    minimum = int(protocol["selection"]["minimum_to_proceed_per_modality"])
    relation_rates = {}
    if step1_complete:
        for modality in ("T", "V"):
            ids = [
                item_id for item_id in common
                if expected[item_id]["target_modality"] == modality
            ]
            relation_rates[modality] = {
                "n": len(ids),
                "both_match_official": sum(
                    by1[item_id]["target_polarity"]
                    == by2[item_id]["target_polarity"]
                    == expected[item_id]["official_target_polarity"]
                    for item_id in ids
                ),
            }
            relation_rates[modality]["rate"] = (
                relation_rates[modality]["both_match_official"] / len(ids)
            )

    step2_complete_1 = sum(
        all(row[field].strip() for field in STEP2) for row in rows1
    )
    step2_complete_2 = sum(
        all(row[field].strip() for field in STEP2) for row in rows2
    )
    maximum_passing = {
        modality: details["both_match_official"]
        for modality, details in relation_rates.items()
    }
    early_stop = step1_complete and any(
        maximum_passing[modality] < minimum for modality in ("T", "V")
    )
    can_merge = not (
        errors1 or errors2 or missing or args.reviewer_2_step2_not_independent
    )
    if early_stop:
        status = "PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES"
    elif can_merge:
        status = "READY_TO_RUN_MERGE_REVIEWS"
    else:
        status = "WAITING_FOR_INDEPENDENT_COMPLETION"
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "merge_executed": False,
        "model_inference_executed": False,
        "legacy_v3_excluded_from_v4": bool(args.legacy_v3),
        "reviewer_2_step2_declared_independent": not args.reviewer_2_step2_not_independent,
        "v4_rows": {"reviewer_01": len(rows1), "reviewer_02": len(rows2)},
        "step1_complete": step1_complete,
        "step1_agreement": step1_agreement,
        "relation_match_official": relation_rates,
        "maximum_possible_passing_counts": maximum_passing,
        "minimum_to_proceed_per_modality": minimum,
        "step2_can_only_exclude_candidates": True,
        "independent_step2_completion_needed_for_current_gate": not early_stop,
        "step2_complete_items": {
            "reviewer_01": step2_complete_1,
            "reviewer_02": step2_complete_2,
        },
        "missing_cells": {
            "reviewer_01": len(missing1),
            "reviewer_02": len(missing2),
            "total": len(missing),
        },
        "structural_errors": {
            "reviewer_01": errors1,
            "reviewer_02": errors2,
        },
        "input_files": hashes,
    }
    (args.output / "intake_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if early_stop:
        frozen_gate = {
            "created_utc": summary["created_utc"],
            "status": status,
            "protocol": protocol["protocol"],
            "decision_basis": (
                "The frozen pass rule is conjunctive. Independent step-1 ratings "
                "bound the final passing count above by the number for which both "
                "reviewers match the official target polarity. Step 2 can only "
                "exclude candidates, so incomplete step-2 ratings cannot reverse "
                "this stop decision."
            ),
            "candidate_counts": protocol["candidate_counts"],
            "maximum_possible_passing_counts": maximum_passing,
            "minimum_to_proceed_per_modality": minimum,
            "selected_item_ids": {"T": [], "V": []},
            "reviewer_2_step2_declared_independent": (
                not args.reviewer_2_step2_not_independent
            ),
            "quality_review_complete": not missing,
            "relation_review_complete": step1_complete,
            "review_files_sha256": {
                "reviewer_01": hashes["v4-reviewer-1"]["sha256"],
                "reviewer_02": hashes["v4-reviewer-2"]["sha256"],
            },
            "protocol_sha256": sha256(HERE / "protocol.fixed.json"),
            "model_predictions_used_for_decision": False,
            "model_inference_executed": False,
            "thresholds_changed_after_review": False,
        }
        (args.output / "review_gate.frozen.json").write_text(
            json.dumps(frozen_gate, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    missing_r1_items = sorted({row["item_id"] for row in missing1})
    missing_r2_items = sorted({row["item_id"] for row in missing2})
    report = f"""# v4 审核结果接收检查

状态：**{summary['status']}**

- 两份 v4 文件均为 35 行，步骤一完整：{step1_complete}。
- 审核者 1 步骤二完整条目：{step2_complete_1}/35；缺项条目：{', '.join(missing_r1_items) or '无'}。
- 审核者 2 步骤二完整条目：{step2_complete_2}/35；缺项条目数：{len(missing_r2_items)}。
- 审核者 2 步骤二是否声明为独立完成：{not args.reviewer_2_step2_not_independent}。
- 旧版 360 项表仅归档，未进入 v4 合并：{bool(args.legacy_v3)}。
- 按冻结规则，最终通过数的上限为 T={maximum_passing.get('T', 0)}、V={maximum_passing.get('V', 0)}；继续推理门槛为每模态 {minimum}。

现阶段只保留步骤一的双人描述性统计。步骤二用于原片同步、转写、字幕和处理质量筛选；若直接复制另一位审核者的答案，它不构成第二份独立证据，不能据此生成双人高质量子集。由于步骤二只能排除候选，不能增加候选，本批数据已由完整的独立步骤一评分确定触发暂停条件，不要求为本次停止决定补完步骤二。

## 续填方法

若未来需要得到完整的双人质量审计，而不只是当前停止决定：审核者 1 补 `{', '.join(missing_r1_items)}` 的空字段；审核者 2 独立播放原片并完成其余 {len(missing_r2_items)} 个候选的步骤二。已填写的步骤一可以通过导入 CSV 保留。完成后再运行 `merge_reviews.py`。
"""
    (args.output / "README.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
