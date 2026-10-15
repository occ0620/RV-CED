"""Reuse the preregistered 360 item IDs; prepare a separately served, blinded reviewer surface."""
from collections import Counter,defaultdict
import json
from common import *

def main():
    if any((HERE/'review_imports').rglob('*.original.csv')):
        raise SystemExit('Human forms already imported; preserve this protocol and use a new version')
    public=read_json(RUN/'audit/items.blinded.json');key=read_json(RUN/'audit/key.private.json')
    assert len(public)==360 and set(key)=={x['item_id'] for x in public}
    assert digest(key)=='9ff22f42c5f7317f8028531c5f3592f798ee80607cd241cfcc7d7c35cfee78f1'
    media={};safe=[];dup=[];groups=defaultdict(list)
    for x in public:
        channels={}
        for m,desc in x['channels'].items():
            if m=='T':channels[m]=dict(text=desc['text'])
            elif m=='A':
                local=RUN/desc['audio'];opaque=digest(['blind-media',desc['audio']])[:32]
                assert local.resolve().is_relative_to((RUN/'cache/inputs').resolve()) and local.is_file()
                media[opaque]=dict(path=str(local.resolve()),content_type='audio/wav',sha256=file_hash(local))
                channels[m]=dict(audio='/media/'+opaque,speech_type='完整语音，未模糊语词；请独立判断词义是否听懂。')
            else:
                frames=[]
                for f in desc['frames']:
                    local=RUN/f;opaque=digest(['blind-media',f])[:32]
                    assert local.resolve().is_relative_to((RUN/'cache/inputs').resolve()) and local.is_file()
                    media[opaque]=dict(path=str(local.resolve()),content_type='image/png',sha256=file_hash(local));frames.append('/media/'+opaque)
                channels[m]=dict(frames=frames,frame_count=desc['frame_count'],duration=desc['duration'])
        safe.append(dict(item_id=x['item_id'],stage=x['stage'],channels=channels))
        signature=digest(x['channels']);groups[x['stage'],signature].append(x['item_id'])
        dup.append({**key[x['item_id']],'item_id':x['item_id'],'stage':x['stage'],'display_signature':signature,
            'display_occurrences':None,'unit_code':x['unit_code']})
    for r in dup:r['display_occurrences']=len(groups[r['stage'],r['display_signature']])
    # Never put this mapping in the HTTP document root or public JSON.
    writejson(HERE/'private/media_map.json',media)
    writecsv(HERE/'private/audit_display_mapping.csv',dup)
    writejson(HERE/'reviewer/items.json',safe)
    for reviewer in ('reviewer_01','reviewer_02'):
        rows=[]
        for x in safe:
            r=dict(reviewer_id=reviewer,item_id=x['item_id'],stage=x['stage'])
            if x['stage']=='combination':r.update({k:'na' for k in EXTRA})
            else:
                if 'A' not in x['channels']:r['speech_words_understood']='na'
                if 'V' not in x['channels']:r.update(subtitles_present='na',subtitles_readable='na')
            rows.append(r)
        writecsv(HERE/f'reviewer/{reviewer}.blank.csv',rows,FIELDS)
    counts=dict(item_count=len(public),source_modality_units=len({(k['source_id'],k['modality']) for k in key.values()}),
        unique_audited_sources=len({k['source_id'] for k in key.values()}),
        audited_triplets=len({k['triplet_id'] for k in key.values()}),
        stages={stage:dict(display_rows=sum(x['stage']==stage for x in public),unique_display_contents=sum(s==stage for s,h in groups),
            excess_duplicate_displays=sum(len(ids)-1 for (s,h),ids in groups.items() if s==stage),
            duplicate_content_groups=sum(len(ids)>1 for (s,h),ids in groups.items() if s==stage)) for stage in ('channel','combination')},
        condition_stage_counts={stage:dict(Counter(k['condition'] for k in key.values() if k['stage']==stage)) for stage in ('channel','combination')},
        unique_underlying_full_input_hashes=170,independence_note='Display rows and repeated source ratings are not independent evidence. 72 audited triplets are only 36 source-modality units; source originals shared across donor groups.',
        frozen_audit_key_canonical_sha256=digest(key))
    # Full combination uniqueness should agree with the actual display content, not a hardcoded assumption.
    counts['unique_underlying_full_input_hashes']=counts['stages']['combination']['unique_display_contents']
    writejson(HERE/'tables/audit_composition.json',counts)
    protocol=dict(status='关系审核待完成',base='Frozen main-a800-v3 audit configuration and src/audit.py',
        original_artifact_hashes=frozen_hashes(),
        frozen_relation_rule='For BOTH reviewers: source and same-polarity donor share a nonzero polarity; opposite donor has opposite polarity; ALL THREE pairwise intensity differences <=1; all confidence >=2.',
        frozen_high_quality_rule='BOTH reviewers pass relation; both edited combinations processing_error=0; identity flags and scene flags each known (0/1) and equal between the paired edited inputs. No tested-model correctness requirement.',
        rule_exclusions='Speech conflicts, subtitles, timing and clarity are reported separately; they do not silently become new exclusions under the frozen rule.',
        posthoc_additions_before_human_ratings='clarity (1/2/3/unknown), speech_words_understood, subtitles_present and subtitles_readable; descriptive only. Stricter complete-form validation. Reviewer UI hides unit codes and serves only whitelist routes.',
        human_scores_present=False,selection_unchanged=True,reviewer_rows_each=360,channel_rows_each=180,combination_rows_each=180,
        analysis_denominators='Raw display-weighted agreement and within-content average deduplicated agreement are separately reported; repeated displays are never counted as independent samples.',
        high_quality_subset_scope='Only audited and completed triplets (max72), average available qualifying groups within each source, then equal source weighting; report one-group/two-group counts; no generalization to all180.',
        small_cells='Fewer than10 unique sources: counts only, no general-effect interpretation.',
        field_scales=dict(polarity=[-1,0,1],intensity=[0,1,2,3,4],confidence=[1,2,3],clarity=['1','2','3','unknown'],
            issue_flags=['0','1','unknown'],speech_words_understood=['0','1','unknown','na'],subtitles_present=['0','1','unknown','na'],subtitles_readable=['0','1','unknown','na']),
        code_files_sha256={f:file_hash(HERE/f) for f in ['common.py','prepare_review.py','merge_reviews.py','serve_review.py']})
    existing=HERE/'evidence/review_protocol.fixed.json'
    if existing.exists():
        writejson(HERE/f'evidence/protocol_history/{file_hash(existing)}.json',read_json(existing))
    protocol['fixed_time_utc']=now()
    writejson(HERE/'evidence/review_protocol.fixed.json',protocol)
    writejson(HERE/'evidence/review_protocol.sha256.json',dict(sha256=file_hash(HERE/'evidence/review_protocol.fixed.json'),
        reviewer_files={p.name:file_hash(p) for p in sorted((HERE/'reviewer').glob('*')) if p.is_file()},prepared_before_any_import=True))
    print('BLIND_PREPARATION',json.dumps(counts,ensure_ascii=True))

if __name__=='__main__':main()
