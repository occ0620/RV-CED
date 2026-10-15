"""Strict validation and cache-only recomputation after TWO independent human forms."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
import shutil
import sys
import numpy as np
from common import *

def validate(path,items):
    rows=readcsv(path);errors=[]
    expected={x['item_id']:x for x in items};seen=set();reviewers=set()
    for i,r in enumerate(rows,2):
        prefix=f'{path.name}: row {i}'
        missing=set(FIELDS)-set(r)
        if missing:errors.append(prefix+': missing columns '+','.join(sorted(missing)));continue
        iid=r['item_id'].strip();rid=r['reviewer_id'].strip()
        if not rid:errors.append(prefix+': reviewer_id missing')
        else:reviewers.add(rid)
        if iid in seen:errors.append(prefix+': duplicate item_id '+iid)
        seen.add(iid)
        if iid not in expected:errors.append(prefix+': unknown item_id '+iid);continue
        x=expected[iid]
        if r['stage']!=x['stage']:errors.append(prefix+': stage changed')
        def allowed(k,values):
            if r[k].strip() not in values:errors.append(prefix+': '+k+' missing/invalid')
        if x['stage']=='channel':
            allowed('polarity',{'-1','0','1'});allowed('intensity',set('01234'));allowed('confidence',set('123'))
            allowed('clarity',{'1','2','3','unknown'})
            allowed('speech_words_understood',{'0','1','unknown'} if 'A' in x['channels'] else {'na'})
            allowed('subtitles_present',{'0','1','unknown'} if 'V' in x['channels'] else {'na'})
            allowed('subtitles_readable',{'0','1','unknown','na'} if 'V' in x['channels'] else {'na'})
            if 'V' in x['channels']:
                if r['subtitles_present']=='0' and r['subtitles_readable']!='na':errors.append(prefix+': absent subtitles require readability=na')
                if r['subtitles_present']=='1' and r['subtitles_readable']=='na':errors.append(prefix+': present subtitles require readable 0/1/unknown')
            for k in ISSUES:allowed(k,{''})
        else:
            for k in ('polarity','intensity','confidence'):allowed(k,{''})
            for k in EXTRA:allowed(k,{'na'})
            for k in ISSUES:allowed(k,{'0','1','unknown'})
    if seen!=set(expected):errors.append(f'{path.name}: missing={len(set(expected)-seen)} unexpected={len(seen-set(expected))}')
    if len(rows)!=360:errors.append(f'{path.name}: expected 360 rows, got {len(rows)}')
    if len(reviewers)!=1:errors.append(f'{path.name}: expected exactly one reviewer_id, got {len(reviewers)}')
    return rows,errors,next(iter(reviewers)) if len(reviewers)==1 else None

def weighted_agreement(rows1,rows2,items):
    a={r['item_id']:r for r in rows1};b={r['item_id']:r for r in rows2}
    signatures={x['item_id']:digest(x['channels']) for x in items if x['stage']=='channel'}
    counts=Counter(signatures.values());ids=list(signatures)
    weights=np.array([1/counts[signatures[i]] for i in ids]);weights/=weights.sum()
    x=np.array([int(a[i]['polarity']) for i in ids]);y=np.array([int(b[i]['polarity']) for i in ids])
    observed=float(weights@(x==y));expected=sum(float(weights@(x==k))*float(weights@(y==k)) for k in (-1,0,1))
    return dict(unique_channel_contents=len(counts),weighted_agreement=observed,
        weighted_cohen_kappa=(observed-expected)/(1-expected) if expected<1 else None,
        definition='Each display receives weight 1/occurrences of identical channel content, normalized over unique contents; descriptive, not independent evidence')

def subset_stats(triplets,qualifying,c):
    groups=defaultdict(list)
    for r in triplets:
        if r['triplet_id'] in qualifying:groups[r['source_id'],r['modality']].append(r)
    source=[dict(source_id=s,modality=m,groups=len(rs),**{k:float(np.mean([float(r[k]) for r in rs])) for k in ('delta_A','delta_B','D','abs_shift_A','IR_A','B_positive','B_zero','B_negative')}) for (s,m),rs in sorted(groups.items())]
    allids=sorted({r['source_id'] for r in source});rng=np.random.default_rng(c['bootstrap_seed']);draws=rng.integers(0,len(allids),(c['bootstrap_repetitions'],len(allids))) if allids else []
    output=[]
    for m in MODS:
        rr=[r for r in source if r['modality']==m];n=len(rr)
        for k in ('delta_A','delta_B','D','abs_shift_A','IR_A','B_positive','B_zero','B_negative'):
            lookup={r['source_id']:r[k] for r in rr};boot=[]
            if n>=10:
                for draw in draws:
                    values=[lookup[allids[int(i)]] for i in draw if allids[int(i)] in lookup]
                    if values:boot.append(np.mean(values))
            lo,hi=ci(boot) if boot else (None,None)
            output.append(dict(modality=m,metric=k,source_n=n,qualifying_triplets=sum(r['groups'] for r in rr),
                one_group_sources=sum(r['groups']==1 for r in rr),two_group_sources=sum(r['groups']==2 for r in rr),
                value=float(np.mean([r[k] for r in rr])) if n>=10 else None,ci_low=lo,ci_high=hi,
                interpretation='audited subset descriptive only' if n>=10 else 'counts_only_too_few_sources'))
    return source,output

def main():
    p=argparse.ArgumentParser();p.add_argument('--reviewer-1',required=True);p.add_argument('--reviewer-2',required=True)
    p.add_argument('--out',required=True);p.add_argument('--validate-only',action='store_true');args=p.parse_args()
    out=Path(args.out).resolve();assert out.is_relative_to(HERE.resolve()),'Output must remain inside followup_analysis'
    if out.exists():raise SystemExit('Output already exists; choose a new version to preserve raw ratings and disagreements')
    out.mkdir(parents=True);before=frozen_hashes()
    items=read_json(HERE/'reviewer/items.json');paths=[Path(args.reviewer_1).resolve(),Path(args.reviewer_2).resolve()]
    for i,path in enumerate(paths,1):shutil.copyfile(path,out/f'reviewer_{i:02}.original.csv')
    results=[validate(path,items) for path in paths];errors=[e for _,ee,_ in results for e in ee]
    if results[0][2]==results[1][2]:errors.append('Two different reviewer IDs required')
    fixed=read_json(HERE/'evidence/review_protocol.sha256.json')
    if file_hash(HERE/'evidence/review_protocol.fixed.json')!=fixed['sha256']:errors.append('Fixed review protocol changed')
    protocol=read_json(HERE/'evidence/review_protocol.fixed.json')
    if before!=protocol['original_artifact_hashes']:errors.append('Original frozen artifacts differ from the pre-review verification')
    for name,sha in protocol['code_files_sha256'].items():
        if file_hash(HERE/name)!=sha:errors.append('Review program changed after protocol fixation: '+name)
    writejson(out/'validation.json',dict(valid=not errors,errors=errors,reviewer_ids=[r[2] for r in results],
        input_sha256=[file_hash(p) for p in paths],protocol_sha256=fixed['sha256'],raw_rows=[len(r[0]) for r in results],
        human_independence='Two IDs required; genuine independent completion remains a human procedural requirement'))
    if errors:
        print('INCOMPLETE_OR_INVALID',len(errors),'issues; raw forms preserved; no relation judgments generated')
        raise SystemExit(2)
    if args.validate_only:print('VALID_TWO_REVIEWER_FORMS');return
    allrows=results[0][0]+results[1][0];writecsv(out/'ratings.merged.csv',allrows,FIELDS)
    from src.audit import audit_results
    c=read_json(RUN/'manifests/config.frozen.json')
    relation_rows,summary,relations,high=audit_results(RUN,out/'ratings.merged.csv',c)
    assert summary['relation_assessed_triplets']==72
    # Preserve all180 rows; 108 outside the fixed audit sample remain unassessed.
    writecsv(out/'relations.all180.csv',relation_rows)
    summary['deduplicated_channel_agreement']=weighted_agreement(results[0][0],results[1][0],items)
    combo_signatures={x['item_id']:digest(x['channels']) for x in items if x['stage']=='combination'}
    content_counts=Counter(combo_signatures.values());dedup_issues={}
    for field in ISSUES:
        numerator=denominator=0.0
        for form,_,_ in results:
            for row in form:
                if row['item_id'] in combo_signatures and row[field] in ('0','1'):
                    weight=1/content_counts[combo_signatures[row['item_id']]]
                    numerator+=weight*(row[field]=='1');denominator+=weight
        dedup_issues[field]=dict(weighted_positive_mass=numerator,weighted_known_mass=denominator,
            fraction=numerator/denominator if denominator else None,
            definition='Within each reviewer, total weight1 per identical combination content; unknown excluded from known mass')
    summary['deduplicated_issue_rates']=dedup_issues
    summary['frozen_rule_unchanged']=True;summary['model_single_modality_correctness_required']=False
    summary['raw_display_denominator']=180;summary['relationship_denominator']=72
    summary['status']='Audited fixed sample only; remaining108 triplets unassessed; protocol validity requires interpretation'
    writejson(out/'audit_summary.json',summary)
    a={r['item_id']:r for r in results[0][0]};b={r['item_id']:r for r in results[1][0]};disagreements=[]
    for x in items:
        for k in (('polarity','intensity','confidence',*EXTRA) if x['stage']=='channel' else ISSUES):
            if a[x['item_id']][k]!=b[x['item_id']][k]:disagreements.append(dict(item_id=x['item_id'],stage=x['stage'],field=k,reviewer1=a[x['item_id']][k],reviewer2=b[x['item_id']][k]))
    writecsv(out/'disagreements.csv',disagreements,['item_id','stage','field','reviewer1','reviewer2'])
    duplicate_map=readcsv(HERE/'private/audit_display_mapping.csv');bycontent=defaultdict(list)
    for x in duplicate_map:bycontent[x['stage'],x['display_signature']].append(x['item_id'])
    repeated=[]
    for ir,scores in enumerate((a,b),1):
        for (stage,signature),ids in bycontent.items():
            if len(ids)<2:continue
            for i,j in combinations(ids,2):
                for k in (('polarity','intensity','confidence',*EXTRA) if stage=='channel' else ISSUES):
                    repeated.append(dict(reviewer=ir,stage=stage,item1=i,item2=j,field=k,agrees=scores[i][k]==scores[j][k],
                        note='repeated content diagnostic, not additional independent evidence'))
    writecsv(out/'repeat_consistency.csv',repeated)
    from run_analysis import calculate
    original_c,pairs,inputs,predictions,inventory=load()
    lookup={r['input_hash']:r for r in predictions if r['kind']=='main'}
    for r in predictions:
        if r!=read_json(RUN/'cache/scores'/f"{r['cache_key']}.json"):
            raise SystemExit('Atomic score cache differs from preserved journal')
    triplets,_,_=calculate(c,pairs,lookup,inventory)
    writecsv(out/'cache_recomputed.all180.triplets.csv',triplets)
    audited={tid for tid,v in relations.items() if v is not None};passed={tid for tid,v in relations.items() if v};hq={tid for tid,v in high.items() if v}
    # No model diagnostic acts as an inclusion criterion.
    for name,ids in [('frozen_all180',{r['triplet_id'] for r in triplets}),('audited_all',audited),('relation_passed',passed),('high_quality_frozen_rule',hq)]:
        src,stats=subset_stats(triplets,ids,c);writecsv(out/f'{name}.source.csv',src,['source_id','modality','groups','delta_A','delta_B','D','abs_shift_A','IR_A','B_positive','B_zero','B_negative']);writecsv(out/f'{name}.summary.csv',stats)
    writecsv(out/'high_quality.triplet_ids.csv',[dict(triplet_id=tid) for tid in sorted(hq)],['triplet_id'])
    # Model agreement remains a separately named diagnostic stratum, never a high-quality gate.
    singles={r['input_hash']:r['agrees'] for r in readcsv(RUN/'results/single_modality_diagnostics.csv')}
    from src.media import input_hash
    inputs=read_json(RUN/'manifests/inputs.json');diagnostics=[]
    for pair in read_jsonl(RUN/'manifests/pairs.jsonl'):
        m=pair['modality'];vals=[]
        for cond in ('original','a','b'):
            ch=inputs[pair['input_hash_'+cond]];vals.append(singles.get(input_hash({m:ch[m]})))
        diagnostics.append(dict(triplet_id=pair['triplet_id'],all_three_model_single_agree=all(v=='True' for v in vals),
            high_quality_frozen_rule=high[pair['triplet_id']],used_as_quality_gate=False))
    writecsv(out/'model_single_agreement.separate_diagnostic.csv',diagnostics)
    writejson(out/'provenance.json',dict(time_utc=now(),model_inference=False,training=False,original_files_unchanged=frozen_hashes()==before,
        input_sha256=[file_hash(p) for p in paths],protocol_sha256=fixed['sha256'],original_run_sha256=before,
        statistical_unit='Average qualifying donor groups within source, then equal source weights; joint source bootstrap preserves modalities',
        note='Do not overwrite independent ratings when adjudicating. Save adjudication in another version.'))
    (out/'report.md').write_text(f'''# 人工审核合并与缓存重算

本次导入两位独立审核者各360行原始评分，原始CSV和分歧逐项保留。
已评分72个三元组，未抽审的108个仍未评分。关系通过 {len(passed)}/72；原冻结规则高质量 {len(hq)}/72。
极性一致率和kappa的原展示分母为180个通道评分对；去重加权分母及问题已知分母见 audit_summary.json。
高质量条件未使用被测模型单模态正确性。全部180个冻结样本仍保留，结果由已有预测缓存重新计算，没有模型推理或训练。

各子集先在每源通过的供体组内平均，再跨源等权；source/summary表分别列1组和2组源数。
少于10个源的小格只列数量。高质量子集只覆盖固定抽审范围，不能直接代表全180。
审核可靠性、供体稳定性以及相对自然冲突评测的增量信息仍需联合判断，不能仅凭关系通过率或区间宣布协议有效。
''',encoding='utf-8')
    assert frozen_hashes()==before
    print('HUMAN_FORMS_MERGED_AND_CACHE_RESULTS_RECOMPUTED',out)

if __name__=='__main__':main()
