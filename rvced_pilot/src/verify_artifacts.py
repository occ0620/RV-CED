"""Independent final integrity check for a prepared or frozen real-data run."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from src.common import (code_fingerprint, digest, file_hash, load_config, now, read_csv,
                        read_json, read_jsonl, write_json)
from src.data import compatible, load_inventory
from src.media import validate_pair_hashes, verify_channels

p = argparse.ArgumentParser()
p.add_argument('--run-dir', default=str(root))
p.add_argument('--config', default=str(root / 'configs/pilot.yaml'))
args = p.parse_args()
run, c = Path(args.run_dir).resolve(), load_config(args.config)
rows = load_inventory(run)
lookup = {r['clip_id']: r for r in rows}
assert len(lookup) == len(rows)
freeze = read_json(run / 'manifests/freeze.json')
pairs = read_jsonl(run / 'manifests/pairs.jsonl') if freeze['status'] == 'frozen' else read_jsonl(run / 'manifests/pairs.proposed.jsonl')
inputs = read_json(run / 'manifests/inputs.json')
units, reuse = defaultdict(list), Counter()
source_ids = {p['source_id'] for p in pairs}
for pair in pairs:
    source, a, b = [lookup[pair[k]] for k in ('source_id', 'donor_a_id', 'donor_b_id')]
    m, tol = pair['modality'], c['selection']['label_tolerance'] + 1e-10
    key = 'y_' + m.lower()
    assert all(r['eligible'] and r['split'] == c['selection']['split'] for r in (source, a, b))
    assert abs(source[key]) + 1e-10 >= c['selection']['min_source_strength']
    assert source[key] * a[key] > 0 and source[key] * b[key] < 0
    assert abs(a[key] - source[key]) <= tol and abs(abs(b[key]) - abs(source[key])) <= tol
    assert pair['direction'] == (-1 if source[key] > 0 else 1)
    assert compatible(source, a, b, m, c) is not None
    measure = (lambda r: len(r['text'])) if m == 'T' else (lambda r: r['audio_duration' if m == 'A' else 'duration'])
    bounds = c['selection']['text_length_ratio'] if m == 'T' else c['selection']['duration_ratio']
    assert all(bounds[0] - 1e-10 <= measure(d) / measure(source) <= bounds[1] + 1e-10 for d in (a, b))
    assert pair['source_target_score'] == source[key] and pair['donor_a_target_score'] == a[key] and pair['donor_b_target_score'] == b[key]
    validate_pair_hashes(pair, inputs)
    units[pair['source_id'], m].append(pair)
    reuse.update([a['clip_id'], b['clip_id']])
assert not source_ids & set(reuse)
assert max(reuse.values(), default=0) <= c['selection']['donor_max_reuse']
groups = c['selection']['smoke_groups_per_source'] if freeze.get('phase') == 'smoke' else c['selection']['groups_per_source']
for group in units.values():
    assert len(group) == groups
    assert len({p[k] for p in group for k in ('donor_a_id', 'donor_b_id')}) == 2 * groups
verified = set()
for channels in inputs.values():
    for m, desc in channels.items():
        if (m, desc['hash']) not in verified:
            verify_channels({m: desc})
            verified.add((m, desc['hash']))
audit = read_json(run / 'audit/export.json')
for rel, sha in audit['files'].items():
    assert file_hash(run / rel) == sha
public = read_json(run / 'audit/items.blinded.json')
private = read_json(run / 'audit/key.private.json')
assert {item['item_id'] for item in public} == set(private)
assert {r['item_id'] for r in read_csv(run / 'audit/review_items.csv')} == set(private)
assert all(set(item) == {'item_id', 'stage', 'unit_code', 'channels'} for item in public)
result = dict(time_utc=now(), passed=True, real_inventory_rows=len(rows), eligible_media=sum(r['eligible'] for r in rows),
    strict_triplets=len(pairs), unique_sources=len(source_ids), source_modality_units=len(units),
    distinct_donors=len(reuse), donor_reuse_histogram=dict(Counter(reuse.values())),
    content_input_hashes=len(inputs), verified_channel_payloads=len(verified), audit_items=len(public),
    real_prediction_records=len(read_jsonl(run / 'results/predictions.jsonl')),
    code_sha256=code_fingerprint(), configuration_sha256=digest(c),
    pairs_sha256=file_hash(run / ('manifests/pairs.jsonl' if freeze['status'] == 'frozen' else 'manifests/pairs.proposed.jsonl')),
    inference_freeze_status=freeze['status'], relation_audit_status='关系审核待完成')
write_json(run / 'evidence/artifact_verification.json', result)
print(result)
