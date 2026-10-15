"""Prepare an immutable metadata snapshot and label-only donor IDs BEFORE GPU predictions."""
from pathlib import Path
import csv
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.common import canonical, digest, file_hash, load_config, read_json, write_json, write_csv
from src.data import load_inventory, select_pairs

stage = ROOT / '.remote/staging'
stage.mkdir(parents=True, exist_ok=True)
c = load_config(ROOT / 'configs/pilot.yaml')
rows = load_inventory(ROOT)
roles, selected = {}, set()
role_fields = ['triplet_id', 'source_id', 'modality', 'donor_group', 'donor_a_id', 'donor_b_id',
               'source_target_score', 'donor_a_target_score', 'donor_b_target_score', 'direction', 'selection_seed', 'split']
for phase in ('smoke', 'main'):
    pairs, _, _ = select_pairs(rows, c, phase)
    role_rows = [{k: p[k] for k in role_fields} for p in pairs]
    roles[phase] = digest(role_rows)
    write_json(stage / ('roles.' + phase + '.json'), role_rows)
    selected.update(p[k] for p in pairs for k in ('source_id', 'donor_a_id', 'donor_b_id'))
for row in rows:
    row['origin_media_path'] = row['media_path']
    row['media_path'] = '/root/autodl-tmp/RV-CED/data/CH-SIMS-v2/Raw/' + row['clip_id'] + '.mp4'
    row['media_verification_origin'] = 'Local Windows full decode and SHA256, before any Qwen prediction'
    row['remote_transfer_scope'] = 'selected_for_smoke_or_main' if row['clip_id'] in selected else 'unused_local_reference'
write_csv(stage / 'inventory.csv', rows, list(rows[0]))
write_json(stage / 'inventory_stats.json', read_json(ROOT / 'results/inventory_stats.json'))
write_json(stage / 'selected_clip_ids.json', sorted(selected))
provenance = dict(inventory_sha256=file_hash(stage / 'inventory.csv'),
    original_inventory_sha256=file_hash(ROOT / 'manifests/inventory.csv'),
    labels_sha256=file_hash(c['dataset']['labels_path']), role_fields=role_fields,
    expected_role_sha256=roles, selected_clips=len(selected),
    selected_media_bytes=sum(Path(r['origin_media_path']).stat().st_size for r in rows if r['clip_id'] in selected),
    media_verification_origin='All 4403 local original clips inventoried before any Qwen inference; 135 strict frame-count exclusions unchanged',
    remote_scope='Only preselected smoke/main raw clips are required remotely. Full metadata remains the locally verified population; non-transferred media are not claimed to exist remotely.',
    predictions_used_for_selection=False)
write_json(stage / 'provenance.json', provenance)
print(json.dumps(provenance, ensure_ascii=False, indent=2))
