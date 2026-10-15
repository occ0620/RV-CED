from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import html
import json
import random

from .common import (Blocked, MODALITIES, STATUS, digest, file_hash, read_csv, read_json,
                     read_jsonl, write_csv, write_json)

ISSUES = ['speech_content_conflict', 'subtitle_leakage', 'identity_discontinuity',
          'scene_discontinuity', 'timing_discontinuity', 'processing_error']
REVIEW_FIELDS = ['reviewer_id', 'item_id', 'stage', 'polarity', 'intensity', 'confidence', *ISSUES, 'notes']


def export_audit(run, c):
    run = Path(run)
    meta_path = run / 'audit/export.json'
    if meta_path.exists():
        meta = read_json(meta_path)
        for rel, sha in meta['files'].items():
            if file_hash(run / rel) != sha:
                raise Blocked('Existing audit materials changed; preserve them and export a new version')
        return meta
    if read_jsonl(run / 'results/predictions.jsonl'):
        raise Blocked('Audit selection must precede predictions; use a new preregistered run')
    pairs = read_jsonl(run / 'manifests/pairs.jsonl') or read_jsonl(run / 'manifests/pairs.proposed.jsonl')
    inputs = read_json(run / 'manifests/inputs.json', {})
    pairs = [p for p in pairs if p.get('input_hash_original') in inputs]
    rng = random.Random(c['seed'])
    units = defaultdict(list)
    for p in pairs:
        units[(p['source_id'], p['modality'])].append(p)
    chosen = []
    for m in MODALITIES:
        pool = sorted(k for k in units if k[1] == m)
        rng.shuffle(pool)
        chosen.extend(pool[:c['audit']['units_per_modality']])
    key, public, tables = {}, [], []
    for unit in chosen:
        unit_code = digest(['blind-unit', c['seed'], unit])[:16]
        original_seen = False
        for p in units[unit]:
            for cond in ('original', 'a', 'b'):
                if cond == 'original' and original_seen:
                    continue
                if cond == 'original':
                    original_seen = True
                ch = inputs[p['input_hash_' + cond]]
                for stage in ('channel', 'combination'):
                    item_id = digest(['blind-item', c['seed'], p['triplet_id'], cond, stage])[:20]
                    key[item_id] = dict(source_id=p['source_id'], modality=p['modality'],
                                        donor_group=p['donor_group'], triplet_id=p['triplet_id'],
                                        condition=cond, stage=stage, unit_code=unit_code)
                    shown = {p['modality']: ch[p['modality']]} if stage == 'channel' else ch
                    safe = {}
                    for m, desc in shown.items():
                        if m == 'T':
                            safe[m] = dict(text=desc['text'])
                        elif m == 'A':
                            safe[m] = dict(audio=Path(desc['wav_path']).relative_to(run.resolve()).as_posix(),
                                           speech_type='完整语音；无语词模糊处理')
                        else:
                            safe[m] = dict(frames=[Path(x).relative_to(run.resolve()).as_posix() for x in desc['frames']],
                                           duration=desc['duration'], frame_count=len(desc['frames']))
                    public.append(dict(item_id=item_id, stage=stage, unit_code=unit_code, channels=safe))
                    tables.append(dict(item_id=item_id, stage=stage))
    rng.shuffle(public)
    order = {p['item_id']: i for i, p in enumerate(public)}
    tables.sort(key=lambda x: order[x['item_id']])
    write_csv(run / 'audit/review_items.csv', tables, REVIEW_FIELDS)
    write_json(run / 'audit/key.private.json', key)
    write_json(run / 'audit/items.blinded.json', public)
    # Public JSON contains no A/B flags, labels, donor IDs, expected directions, or model predictions.
    encoded = json.dumps(public, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>RV-CED 独立人工审核</title>
<style>body{max-width:1080px;margin:32px auto;font:16px system-ui;line-height:1.6}article{border:1px solid #bbb;padding:20px;margin:20px 0}img{width:23%;height:auto;margin:3px}pre{white-space:pre-wrap}small{color:#555}button{padding:8px;margin:5px}</style>
<h1>RV-CED 独立人工审核</h1><p>关系审核待完成。先完成通道评分并保存，再看组合输入。不要查看私有映射文件或模型结果。评分写入 review_items.csv 的独立副本。</p>
<p id="status"></p><button onclick="show('channel')">第一步：独立通道</button><button onclick="show('combination')">第二步：组合输入</button><main id="items"></main>
<script>const data=__DATA__;
function elem(tag,text){let x=document.createElement(tag); if(text!==undefined)x.textContent=text;return x;}
function show(stage){const host=document.getElementById('items');host.replaceChildren();let items=data.filter(x=>x.stage===stage);document.getElementById('status').textContent=`${stage}: N=${items.length}`;
for(const item of items){let box=elem('article');box.append(elem('h3',item.item_id));box.append(elem('small',`阶段 ${item.stage} · 组合关联 ${item.unit_code}`));for(const [m,c] of Object.entries(item.channels)){box.append(elem('h4',m));if(m==='T')box.append(elem('pre',c.text));if(m==='A'){box.append(elem('p',c.speech_type));let a=elem('audio');a.controls=true;a.preload='none';a.src='../'+c.audio;box.append(a);}if(m==='V'){box.append(elem('p',`无声 ${c.frame_count} 个均匀帧；自然时长 ${c.duration.toFixed(3)}s`));for(const f of c.frames){let im=elem('img');im.src='../'+f;im.loading='lazy';box.append(im);}}}host.append(box);}}
show('channel');</script></html>'''.replace('__DATA__', encoded)
    (run / 'audit/preview.html').write_text(page, encoding='utf-8')
    instructions = '''# 人工审核说明

状态：标签规则构造，关系审核待完成。Codex 或其他模型不能代替人工填写。

在 run 目录运行 `python -m http.server 8000 --bind 127.0.0.1`，打开
`http://127.0.0.1:8000/audit/preview.html`。页面仅供本地审核，不公开上传原始媒体。
两名审核者各复制一份 review_items.csv，填写自己的 reviewer_id，独立完成，不查看
key.private.json、标签表或 predictions.jsonl。先评分 channel，再评分 combination。

channel：polarity=-1/0/1（负/中性/正）；intensity=0..4（无、弱、较弱、较强、强）；
confidence=1..3（低、中、高）。根据实际完整语音判断，不能假装听的是语词模糊音频。
combination：填写六种问题标记 0/1/unknown。channel 中可留问题字段空白；
combination 不填写极性、强度。notes 自由文字。字幕看不清或同步无法判断填 unknown，
不要用 0 代替未知。视频为八帧，不能全面验证唇音同步，需在 notes 记录限制。

处理程序要求两人完整评分、confidence>=2；两人均判定 source/A 同非零极性、source/B
反极性，且强度差不超过 1；A/B 均无处理错误，身份和场景变化标记相同且已知，
才进入已审核高质量子集。六种问题比例分别报告实际已知评分分母。
未评分/缺评/unknown 不当作通过。分歧保留原始记录，讨论裁定应写入另一版本，
不能覆盖原评分。脚本报告一致率、Cohen kappa、强度绝对差和关系通过率。

合并两份表为一个 UTF-8 CSV（只保留一个表头），执行
`python pilot.py analyze --reviews audit/completed_reviews.csv` 即可重新统计，无需重新推理。
'''
    (run / 'audit/README.md').write_text(instructions, encoding='utf-8')
    files = ['audit/review_items.csv', 'audit/key.private.json', 'audit/items.blinded.json',
             'audit/preview.html', 'audit/README.md']
    meta = dict(status=STATUS, exported_before_predictions=True, selected_units=len(chosen), items=len(public),
                unit_counts={m: sum(x[1] == m for x in chosen) for m in MODALITIES},
                files={f: file_hash(run / f) for f in files})
    write_json(meta_path, meta)
    return meta


def audit_results(run, reviews_path, c):
    keys = read_json(Path(run) / 'audit/key.private.json', {})
    raw = read_csv(reviews_path) if reviews_path else []
    channel, combined = defaultdict(dict), defaultdict(dict)
    seen = set()
    for row in raw:
        rid, iid = row.get('reviewer_id', '').strip(), row.get('item_id', '').strip()
        if not rid:
            continue
        if iid not in keys:
            raise Blocked('Unknown human review item: ' + iid)
        if (rid, iid) in seen:
            raise Blocked('Duplicate reviewer/item; preserve disagreements across reviewers, not duplicate rows')
        seen.add((rid, iid))
        k = keys[iid]
        if k['stage'] == 'channel':
            try:
                pol, intensity, confidence = int(row['polarity']), int(row['intensity']), int(row['confidence'])
            except (ValueError, KeyError):
                continue
            if pol not in (-1, 0, 1) or intensity not in c['audit']['intensity_scale'] or confidence not in c['audit']['confidence_scale']:
                raise Blocked('Invalid human polarity/intensity/confidence scale')
            channel[iid][rid] = dict(polarity=pol, intensity=intensity, confidence=confidence)
        else:
            issues = {field: row.get(field, '').strip() for field in ISSUES}
            if any(x not in ('0', '1', 'unknown', '') for x in issues.values()):
                raise Blocked('Invalid issue flag; use 0/1/unknown')
            combined[iid][rid] = issues
    pairs = read_jsonl(Path(run) / 'manifests/pairs.jsonl') or read_jsonl(Path(run) / 'manifests/pairs.proposed.jsonl')
    mapping = defaultdict(dict)
    original = {}
    for iid, k in keys.items():
        if k['condition'] == 'original':
            original[(k['source_id'], k['modality'], k['stage'])] = iid
        else:
            mapping[k['triplet_id']][(k['condition'], k['stage'])] = iid
    relation, high_quality, rows = {}, {}, []
    for p in pairs:
        tid = p['triplet_id']
        ids = [original.get((p['source_id'], p['modality'], 'channel'))] + [mapping[tid].get((cond, 'channel')) for cond in ('a', 'b')]
        rids = set.intersection(*[set(channel.get(i, {})) for i in ids])
        relation[tid] = None
        high_quality[tid] = False
        if len(rids) >= c['audit']['reviewers_required']:
            passed, quality = [], []
            for rid in sorted(rids):
                src, aa, bb = [channel[i][rid] for i in ids]
                relation_ok = (src['polarity'] != 0 and src['polarity'] == aa['polarity'] == -bb['polarity'] and
                       abs(src['intensity'] - aa['intensity']) <= c['audit']['max_intensity_difference'] and
                       abs(src['intensity'] - bb['intensity']) <= c['audit']['max_intensity_difference'] and
                       abs(aa['intensity'] - bb['intensity']) <= c['audit']['max_intensity_difference'] and
                       min(x['confidence'] for x in (src, aa, bb)) >= c['audit']['min_confidence'])
                passed.append(relation_ok)
                ia, ib = [mapping[tid].get((cond, 'combination')) for cond in ('a', 'b')]
                fa, fb = combined.get(ia, {}).get(rid, {}), combined.get(ib, {}).get(rid, {})
                good = fa.get('processing_error') == fb.get('processing_error') == '0'
                for field in ('identity_discontinuity', 'scene_discontinuity'):
                    good &= fa.get(field) in ('0', '1') and fa.get(field) == fb.get(field)
                quality.append(relation_ok and good)
            relation[tid], high_quality[tid] = all(passed), all(quality)
        issues = {}
        for field in ISSUES:
            ids_combo = [mapping[tid].get((cond, 'combination')) for cond in ('a', 'b')]
            vals = [v.get(field) for iid in ids_combo for v in combined.get(iid, {}).values()]
            issues[field] = '1' if '1' in vals else '0' if vals and all(v == '0' for v in vals) else 'unknown'
        rows.append(dict(triplet_id=tid, relation_pass=relation[tid], high_quality=high_quality[tid],
                         complete_independent_reviewers=len(rids), **issues))
    agreements, diffs, label1, label2 = [], [], [], []
    for scores in channel.values():
        rids = sorted(scores)
        if len(rids) >= 2:
            aa, bb = [scores[r] for r in rids[:2]]
            label1.append(aa['polarity'])
            label2.append(bb['polarity'])
            agreements.append(aa['polarity'] == bb['polarity'])
            diffs.append(abs(aa['intensity'] - bb['intensity']))
    n = len(agreements)
    observed = sum(agreements) / n if n else None
    expected = sum(label1.count(x) * label2.count(x) for x in (-1, 0, 1)) / n ** 2 if n else None
    kappa = (observed - expected) / (1 - expected) if n and expected < 1 else None
    issues = {}
    for field in ISSUES:
        vals = [v[field] for scores in combined.values() for v in scores.values() if v[field] in ('0', '1')]
        issues[field] = dict(positive=sum(x == '1' for x in vals), known_denominator=len(vals),
                             fraction=sum(x == '1' for x in vals) / len(vals) if vals else None)
    assessed = [v for v in relation.values() if v is not None]
    summary = dict(status=STATUS if not assessed else '人工审核仅覆盖已完成项目；其余关系审核待完成',
                   imported_review_rows=len(seen), paired_channel_ratings=n, polarity_agreement=observed,
                   cohen_kappa=kappa, kappa_note='Undefined for degenerate marginals; first two sorted reviewer IDs per item',
                   mean_absolute_intensity_difference=sum(diffs) / len(diffs) if diffs else None,
                   relation_assessed_triplets=len(assessed), relation_passed=sum(assessed),
                   relation_pass_rate=sum(assessed) / len(assessed) if assessed else None,
                   high_quality_triplets=sum(high_quality.values()), issue_rates=issues)
    return rows, summary, relation, high_quality
