"""Verify privacy, media integrity, schemas, and v3 immutability for v4 preparation."""
from __future__ import annotations
import csv, hashlib, json, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
PILOT=HERE.parents[1]
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def rows(p):
 with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 manifest=json.loads((HERE/'private/preparation_manifest.json').read_text(encoding='utf-8'))
 items=json.loads((HERE/'reviewer/items.json').read_text(encoding='utf-8'))
 mapping=rows(HERE/'private/candidate_mapping.csv'); hashes=rows(HERE/'private/media_hashes.csv')
 assert len(items)==len(mapping)==35
 assert {x['target_modality'] for x in items}=={'T','V'}
 assert sum(x['target_modality']=='T' for x in items)==18 and sum(x['target_modality']=='V' for x in items)==17
 public=(HERE/'reviewer/打开盲审.html').read_text(encoding='utf-8')+(HERE/'reviewer/items.json').read_text(encoding='utf-8')
 for forbidden in ('official_target_polarity','y_t','y_a','y_v','predictions.jsonl','expected_direction'):
  assert forbidden not in public, forbidden
 for r in hashes: assert sha(HERE/'reviewer'/r['relative_path'])==r['sha256']
 for rid in ('reviewer_01','reviewer_02'):
  blank=rows(HERE/f'reviewer/{rid}.blank.csv');assert len(blank)==35 and all(r['reviewer_id']==rid for r in blank)
 archive=HERE/'sharing/rvced_v4_review_offline.zip';assert sha(archive)==manifest['archive_sha256']
 with zipfile.ZipFile(archive) as z: assert z.testzip() is None and not any('/private/' in n for n in z.namelist())
 frozen={
  'pairs.jsonl':'bc740995d0300c52e7847bdebd8067bee5b209d3ae2720d6c7b0877a3c612320',
  'predictions.jsonl':'ec7626f2e95809e530fd8b9b95ff0559b8400a5ca5f924f99fca6687a2a426b8'}
 assert sha(PILOT/'runs/main-a800-v3/manifests/pairs.jsonl')==frozen['pairs.jsonl']
 assert sha(PILOT/'runs/main-a800-v3/results/predictions.jsonl')==frozen['predictions.jsonl']
 out={'passed':True,'candidates':len(items),'T':18,'V':17,'media_files':len(hashes),'archive_sha256':manifest['archive_sha256'],'predictions_read':False,'v3_frozen_hashes_unchanged':True}
 (HERE/'verification.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
