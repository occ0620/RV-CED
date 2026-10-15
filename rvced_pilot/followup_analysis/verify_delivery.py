"""Verify real outputs, blank-form rejection, schema edge cases and the private HTTP boundary."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from http.server import ThreadingHTTPServer
import json
import shutil
import subprocess
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
import numpy as np
from common import *
import merge_reviews
import serve_review

def main():
    checks={};items=read_json(HERE/'reviewer/items.json');fixed=read_json(HERE/'evidence/review_protocol.sha256.json')
    assert file_hash(HERE/'evidence/review_protocol.fixed.json')==fixed['sha256']
    protocol=read_json(HERE/'evidence/review_protocol.fixed.json')
    for name,sha in protocol['code_files_sha256'].items():assert file_hash(HERE/name)==sha,name
    for name,sha in fixed['reviewer_files'].items():assert file_hash(HERE/'reviewer'/name)==sha,name
    old=read_json(RUN/'audit/items.blinded.json')
    assert {(x['item_id'],x['stage']) for x in old}=={(x['item_id'],x['stage']) for x in items}
    assert all(set(x)=={'item_id','stage','channels'} for x in items)
    assert not any(k in (HERE/'reviewer/items.json').read_text(encoding='utf-8') for k in ['"condition"','"prediction"','"unit_code"','"direction"','"source_id"','"triplet_id"'])
    checks['blind_selection_unchanged']=True
    # Check that deduplication by rendered descriptors matches actual text/media bytes.
    media=read_json(HERE/'private/media_map.json');by_descriptor={};by_bytes={}
    for x in items:
        normalized={}
        for m,d in x['channels'].items():
            if m=='T':normalized[m]=dict(text=d['text'])
            elif m=='A':normalized[m]=dict(audio_sha256=media[d['audio'].split('/')[-1]]['sha256'])
            else:normalized[m]=dict(frames_sha256=[media[p.split('/')[-1]]['sha256'] for p in d['frames']],duration=d['duration'])
        by_descriptor.setdefault((x['stage'],digest(x['channels'])),set()).add(x['item_id'])
        by_bytes.setdefault((x['stage'],digest(normalized)),set()).add(x['item_id'])
    assert {frozenset(v) for v in by_descriptor.values()}=={frozenset(v) for v in by_bytes.values()}
    checks['display_duplicate_groups_match_media_bytes']=True
    updated=readcsv(HERE/'tables/effect_summary.csv')
    original_rows=readcsv(RUN/'results/summary.csv')
    for r in updated:
        if r['statistic']=='mean':
            old=next(x for x in original_rows if x['modality']==r['modality'] and x['metric']==r['metric'])
            for k in ('value','ci_low','ci_high'):assert abs(float(r[k])-float(old[k]))<1e-12
    checks['original_means_and_intervals_reproduced']=True
    for name in ('reviewer_01','reviewer_02'):
        rows,errors,rid=merge_reviews.validate(HERE/f'reviewer/{name}.blank.csv',items)
        assert errors and len(rows)==360 and rid==name
    checks['actual_blank_forms_rejected_as_incomplete']=True
    # Schema fixtures exist IN MEMORY ONLY; they are neither human ratings nor experimental outcomes.
    fixture=[]
    for x in items:
        r={k:'' for k in FIELDS};r.update(reviewer_id='SCHEMA_FIXTURE_ONLY',item_id=x['item_id'],stage=x['stage'])
        if x['stage']=='channel':
            r.update(polarity='0',intensity='0',confidence='1',clarity='unknown',
                speech_words_understood='unknown' if 'A' in x['channels'] else 'na',
                subtitles_present='unknown' if 'V' in x['channels'] else 'na',subtitles_readable='unknown' if 'V' in x['channels'] else 'na')
        else:r.update({k:'na' for k in EXTRA});r.update({k:'unknown' for k in ISSUES})
        fixture.append(r)
    with patch.object(merge_reviews,'readcsv',return_value=fixture):assert not merge_reviews.validate(Path('IN_MEMORY_FIXTURE'),items)[1]
    bad=deepcopy(fixture);next(r for r in bad if r['stage']=='channel')['polarity']='2'
    with patch.object(merge_reviews,'readcsv',return_value=bad):assert merge_reviews.validate(Path('IN_MEMORY_INVALID_SCALE'),items)[1]
    with patch.object(merge_reviews,'readcsv',return_value=fixture[:-1]):assert merge_reviews.validate(Path('IN_MEMORY_MISSING_ROW'),items)[1]
    duplicate=fixture[:-1]+[fixture[0]]
    with patch.object(merge_reviews,'readcsv',return_value=duplicate):assert merge_reviews.validate(Path('IN_MEMORY_DUPLICATE'),items)[1]
    checks['schema_valid_invalid_missing_duplicate_cases']=True
    rejected=HERE/'checks'/('blank_rejection_'+__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S'))
    result=subprocess.run([sys.executable,str(HERE/'merge_reviews.py'),'--reviewer-1',str(HERE/'reviewer/reviewer_01.blank.csv'),
        '--reviewer-2',str(HERE/'reviewer/reviewer_02.blank.csv'),'--out',str(rejected),'--validate-only'],capture_output=True,text=True)
    assert result.returncode==2 and not read_json(rejected/'validation.json')['valid']
    checks['real_merge_command_rejects_blank_forms']=True
    source,stats=merge_reviews.subset_stats(readcsv(HERE/'tables/triplet_effects.csv'),
        {p['triplet_id'] for p in read_jsonl(RUN/'manifests/pairs.jsonl')},read_json(RUN/'manifests/config.frozen.json'))
    for m in MODS:
        actual=next(r for r in stats if r['modality']==m and r['metric']=='D')
        expected=next(r for r in readcsv(RUN/'results/summary.csv') if r['modality']==m and r['metric']=='D')
        assert abs(actual['value']-float(expected['value']))<1e-12 and actual['two_group_sources']==30
    checks['cache_recomputation_matches_original_source_weighting']=True
    from src import audit
    keys=read_json(RUN/'audit/key.private.json');pairs=read_jsonl(RUN/'manifests/pairs.jsonl')
    chosen=next(p for p in pairs if any(k['triplet_id']==p['triplet_id'] for k in keys.values()))
    # Demonstrate frozen quality is purely human/construction based; model files are never read.
    form1=deepcopy(fixture);form2=deepcopy(fixture)
    for form,rid in ((form1,'FIXTURE_R1'),(form2,'FIXTURE_R2')):
        for row in form:
            row['reviewer_id']=rid;k=keys[row['item_id']]
            is_source=k['source_id']==chosen['source_id'] and k['modality']==chosen['modality'] and k['condition']=='original'
            is_edit=k['triplet_id']==chosen['triplet_id'] and k['condition']!='original'
            if k['stage']=='channel' and (is_source or is_edit):
                row.update(polarity='1' if k['condition']=='b' else '-1',intensity='2',confidence='3')
            if k['stage']=='combination' and is_edit:
                row.update(processing_error='0',identity_discontinuity='1',scene_discontinuity='1')
    real_read=audit.read_jsonl
    def restricted_read(path):
        assert 'predictions' not in str(path) and 'single_modality' not in str(path)
        return real_read(path)
    with patch.object(audit,'read_csv',return_value=form1+form2),patch.object(audit,'read_jsonl',side_effect=restricted_read):
        _,_,relations,high=audit.audit_results(RUN,Path('IN_MEMORY_FIXTURE'),read_json(RUN/'manifests/config.frozen.json'))
        assert relations[chosen['triplet_id']] and high[chosen['triplet_id']]
    checks['quality_rule_requires_no_model_correctness']=True
    # Exercise actual HTTP endpoints; private files and arbitrary paths must fail.
    server=ThreadingHTTPServer(('127.0.0.1',0),serve_review.handler());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        for path in ('/','/items.json','/guide.md','/reviewer_01.blank.csv','/reviewer_02.blank.csv'):
            with urlopen(base+path,timeout=10) as r:assert r.status==200 and r.read()
        media=read_json(HERE/'private/media_map.json');key=next(iter(media))
        with urlopen(base+'/media/'+key,timeout=10) as r:assert r.read()==Path(media[key]['path']).read_bytes()
        for path in ('/private/media_map.json','/audit/key.private.json','/report.md','/../private/media_map.json','/results/predictions.jsonl','/media/../../private/media_map.json'):
            try:urlopen(base+path,timeout=10);raise AssertionError('Private route exposed '+path)
            except HTTPError as e:assert e.code==404
        checks['http_public_routes_and_private_404']=True
    finally:server.shutdown();server.server_close();thread.join()
    node=shutil.which('node')
    if node:
        page=serve_review.PAGE.replace('__FIELDS__',json.dumps(FIELDS)).replace('__ISSUES__',json.dumps(ISSUES))
        script=page.split('<script>')[1].split('</script>')[0];dest=HERE/'checks/review_ui.js';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(script,encoding='utf-8')
        result=subprocess.run([node,'--check',str(dest)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        checks['review_ui_javascript_syntax']=True
    else:checks['review_ui_javascript_syntax']='Node unavailable; not checked'
    original=read_json(HERE/'evidence/integrity.json')['original_file_hashes'];assert frozen_hashes()==original
    checks['original_artifacts_unchanged']=True
    writejson(HERE/'evidence/delivery_verification.json',dict(time_utc=now(),passed=True,checks=checks,
        human_review_imported=False,fixture_notice='Synthetic schema/quality fixtures existed only in memory for tests; no fixture rating files or experimental results were saved.'))
    print('DELIVERY_VERIFIED',json.dumps(checks))

if __name__=='__main__':main()
