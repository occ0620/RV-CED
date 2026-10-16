"""Read-only feasibility audit for a content-aligned v4; never selects donors or uses predictions."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUN = HERE.parent / "runs" / "main-a800-v3"


def rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def norm(text: str):
    return re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE).lower()


def score(row, modality):
    return float(row["y_" + modality.lower()])


def polarity(value, tau=0.2):
    return 1 if value > tau else -1 if value < -tau else 0


def eligible_relation(source, donor, modality, relation):
    ys, yd = score(source, modality), score(donor, modality)
    if relation == "same":
        return polarity(ys) == polarity(yd) != 0 and abs(yd - ys) <= 0.2 + 1e-10
    return polarity(ys) == -polarity(yd) != 0 and abs(abs(yd) - abs(ys)) <= 0.2 + 1e-10


def duration_ok(source, donor, modality):
    field = "audio_duration" if modality == "A" else "duration"
    a, b = float(source[field]), float(donor[field])
    return 0.8 - 1e-10 <= b / a <= 1.25 + 1e-10


def main():
    inventory = rows(RUN / "manifests" / "inventory.csv")
    pairs = [json.loads(line) for line in (RUN / "manifests" / "pairs.jsonl").read_text(encoding="utf-8").splitlines() if line]
    lookup = {row["clip_id"]: row for row in inventory}
    sources = sorted({pair["source_id"] for pair in pairs})
    eligible = [row for row in inventory if row["eligible"] == "True"]
    text_groups = defaultdict(list)
    for row in eligible:
        text_groups[norm(row["text"])].append(row)

    detail = []
    for source_id in sources:
        source = lookup[source_id]
        exact = [row for row in text_groups[norm(source["text"])] if row["clip_id"] != source_id]
        for modality in ("A", "V"):
            for scope, pool in (("same_test_split", [r for r in exact if r["split"] == source["split"]]),
                                ("all_splits", exact)):
                same = [r for r in pool if duration_ok(source, r, modality) and eligible_relation(source, r, modality, "same")]
                opposite = [r for r in pool if duration_ok(source, r, modality) and eligible_relation(source, r, modality, "opposite")]
                detail.append(dict(source_id=source_id, modality=modality, scope=scope,
                                   exact_text_other_clips=len(pool), same_relation_candidates=len(same),
                                   opposite_relation_candidates=len(opposite),
                                   two_groups_feasible=len(same) >= 2 and len(opposite) >= 2,
                                   source_text=source["text"]))

    summary = []
    for modality in ("A", "V"):
        for scope in ("same_test_split", "all_splits"):
            subset = [row for row in detail if row["modality"] == modality and row["scope"] == scope]
            summary.append(dict(
                modality=modality,
                scope=scope,
                frozen_sources=len(subset),
                sources_with_any_exact_text_alternative=sum(row["exact_text_other_clips"] > 0 for row in subset),
                sources_with_same_candidate=sum(row["same_relation_candidates"] > 0 for row in subset),
                sources_with_opposite_candidate=sum(row["opposite_relation_candidates"] > 0 for row in subset),
                sources_with_two_groups=sum(row["two_groups_feasible"] for row in subset),
                total_same_candidates=sum(row["same_relation_candidates"] for row in subset),
                total_opposite_candidates=sum(row["opposite_relation_candidates"] for row in subset),
            ))

    repeated = Counter(norm(row["text"]) for row in eligible)
    result = dict(
        analysis="metadata-only exact-transcript feasibility; no donor selection and no predictions",
        frozen_sources=len(sources),
        eligible_inventory_rows=len(eligible),
        unique_normalized_texts=len(repeated),
        normalized_texts_with_multiple_clips=sum(count > 1 for count in repeated.values()),
        summary=summary,
        detail=detail,
        interpretation={
            "T": "Replacing only text while retaining source speech cannot preserve lexical identity unless text is unchanged; an unchanged transcript is not a text intervention.",
            "A": "Exact transcript plus duration matching is a minimum lexical control, not word-level alignment or speaker control.",
            "V": "Exact transcript plus duration matching cannot establish identity or lip synchronization; continuous aligned recordings or controlled generation are required.",
        },
    )
    out = HERE / "v4_alignment_feasibility"
    out.mkdir(exist_ok=False)
    (out / "feasibility.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = list(detail[0])
    with (out / "exact_text_candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(detail)
    lines = [
        "# v4 内容对齐可行性核查", "",
        "本核查只读取冻结清单和标签元数据，不使用模型预测、不选择新供体、不修改阈值。精确同句按去除标点和空白后的文字相等定义。", "",
        "| 模态 | 候选范围 | 30源中有其他同句片段 | 有同向候选 | 有反向候选 | 可提供两组同/反候选 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(f"| {row['modality']} | {row['scope']} | {row['sources_with_any_exact_text_alternative']} | {row['sources_with_same_candidate']} | {row['sources_with_opposite_candidate']} | {row['sources_with_two_groups']} |")
    lines += [
        "", "这些数量只回答是否存在最低限度的同句候选。音频仍没有词级时间对齐或说话人控制；视频仍没有身份或口型同步保证。",
        "", "文本 T 若只替换文字但保留原语音，要保持语词一致只能使用相同文字，这不再构成文本情感干预。视频 V 即使文字相同、时长相近，也不能证明嘴型逐时刻对应。",
        "", "因此，只加强现有供体筛选不能完整修复 T/A/V 同步。需要改变研究问题，或创建具有同一内容、同一身份和时间对齐的受控材料，并作为新 v4 重新推理。",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
