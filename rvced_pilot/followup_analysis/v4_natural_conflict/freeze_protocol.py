"""Freeze v4 review, selection, and inference rules before human scores are opened."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
OUT=HERE/'protocol.fixed.json'
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 if OUT.exists():raise SystemExit('protocol.fixed.json already exists; refusing to revise after freeze')
 prep=json.loads((HERE/'private/preparation_manifest.json').read_text(encoding='utf-8'))
 protocol={
  'frozen_utc':datetime.now(timezone.utc).isoformat(),
  'protocol':'v4-natural-conflict-paired-target-removal',
  'candidate_rule':prep['candidate_rule'],'tau':0.2,'candidate_counts':prep['candidate_counts'],
  'reviewers_required':2,
  'human_pass_rule':{
   'both_target_polarity_equals_official_direction':True,'both_target_confidence_min':2,'both_target_clarity_min':2,
   'both_speech_words_understood':1,'both_speech_text_match':1,'both_subtitles_present':0,
   'both_subtitles_readable':'na','both_sync_issue':0,'both_processing_error':0,
   'unknown_policy':'preserve in raw review; candidate fails quality screen'},
  'selection':{'seed':20261016,'cap_per_modality':12,'minimum_to_proceed_per_modality':10,
               'if_insufficient':'pause inference; do not relax or replace based on model output'},
  'conditions':{'T':['full_TAV','AV_only_without_T'],'V':['full_TAV','TA_only_without_V'],'A':'not run; zero strict candidates'},
  'main_metric':'E_m = sign(y_m) * (s(full_TAV) - s(without_target_m))',
  'estimand':'target-channel incremental association under naturally conflicting synchronized clips',
  'claims_excluded':['cross-clip donor causal effect','emotion counterfactual','modality-dependence ranking'],
  'inference_budget':{'maximum_sources':24,'maximum_unique_main_inputs':48,'training':False,'lora':False},
  'model_output_used_for_candidate_or_quality_selection':False,
  'artifact_hashes':{
   'items.json':sha(HERE/'reviewer/items.json'),'candidate_mapping.csv':sha(HERE/'private/candidate_mapping.csv'),
   'guide.md':sha(HERE/'reviewer/guide.md'),'offline_archive':sha(HERE/'sharing/rvced_v4_review_offline.zip'),
   'prepare_review.py':sha(HERE/'prepare_review.py'),'merge_reviews.py':sha(HERE/'merge_reviews.py')},
 }
 OUT.write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(protocol,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
