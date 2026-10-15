"""Recheck and decompose existing scores. Never loads a model or changes the frozen run."""
from collections import Counter,defaultdict
from itertools import combinations
from pathlib import Path
import argparse
import json
import math
import sys
import numpy as np
from common import *

METRICS=('delta_A','delta_B','D','abs_shift_A','IR_A','B_positive','B_zero','B_negative')

def audit_integrity(c,pairs,inputs,predictions,inventory,out):
    before=frozen_hashes();freeze=read_json(RUN/'manifests/freeze.json')
    assert freeze['status']=='frozen'
    for f,sha in freeze['files'].items():assert before[f]==sha,f
    assert digest(c)==freeze['config_sha256']
    code=digest({p.relative_to(PROJECT).as_posix():file_hash(p) for p in sorted((PROJECT/'src').glob('*.py'))})
    assert code==freeze['code_sha256']
    assert len({p['triplet_id'] for p in pairs})==180
    assert len(inputs)==390
    identity=read_json(RUN/'manifests/model_identity.json')
    prompt=(RUN/'manifests/prompt.frozen.txt').read_text(encoding='utf-8')
    for r in predictions:
        assert r==read_json(RUN/'cache/scores'/f"{r['cache_key']}.json")
        scoring=dict(c['scoring'],codes=r['codes'])
        assert cache_key(identity,r['input_hash'],prompt,c['preprocessing'],scoring)==r['cache_key']
        assert all(math.isfinite(v) for v in r['raw_scores'])
        x=np.array(r['raw_scores']);p=np.exp(x-x.max());p/=p.sum()
        assert np.max(np.abs(p-r['probabilities']))<1e-12
        assert abs(p[2]-p[0]-r['s'])<1e-12
        assert r['prediction']==c['scoring']['classes'][int(p.argmax())]
        assert r['candidate_lengths']==[1,1,1] and r['scoring_method']=='next_token'
        assert r['token_validation']['tokens']==[[ord(x)-33] for x in r['codes']]
    assert len(predictions)==821==len({r['cache_key'] for r in predictions})
    lookup={r['input_hash']:r for r in predictions if r['kind']=='main'}
    assert set(lookup)==set(inputs)
    checked=set();media_records=[]
    for channels in inputs.values():
        for m,desc in channels.items():
            if (m,desc['hash']) in checked:continue
            if m=='T':assert digest(desc['text'])==desc['hash']
            elif m=='A':
                path=local_media(desc['path']);assert file_hash(path)==desc['file_sha256']
                a=np.load(path,allow_pickle=False)
                from src.media import tensor_hash
                assert tensor_hash(a)==desc['array_hash']
                assert digest(dict(array=tensor_hash(a),sr=desc['sr']))==desc['hash']
                media_records.append(dict(channel=m,path=str(path.relative_to(PROJECT)),sha256=desc['file_sha256']))
            else:
                assert len(desc['frames'])==len(desc['indices'])==len(set(desc['indices']))==8
                assert digest(dict(frames=desc['frame_sha256'],sample_fps=desc['sample_fps'],indices=desc['indices']))==desc['hash']
                for path,sha in zip(desc['frames'],desc['frame_sha256']):
                    local=local_media(path);assert file_hash(local)==sha
                    media_records.append(dict(channel=m,path=str(local.relative_to(PROJECT)),sha256=sha))
            checked.add((m,desc['hash']))
    reuse=Counter(p[k] for p in pairs for k in ('donor_a_id','donor_b_id'))
    sources={p['source_id'] for p in pairs};units=defaultdict(list);isolation=[]
    assert len(sources)==30 and not sources.intersection(reuse) and max(reuse.values())<=2
    for p in pairs:
        s,a,b=[inventory[p[k]] for k in ('source_id','donor_a_id','donor_b_id')]
        k='y_'+p['modality'].lower();tol=c['selection']['label_tolerance']+1e-10
        assert all(r['eligible'] and r['split']=='test' for r in (s,a,b))
        assert p['direction']==(-1 if s[k]>0 else 1) and abs(s[k])>=.4
        assert [s[k],a[k],b[k]]==[p[x] for x in ('source_target_score','donor_a_target_score','donor_b_target_score')]
        assert s[k]*a[k]>0 and s[k]*b[k]<0
        assert abs(s[k]-a[k])<=tol and abs(abs(s[k])-abs(b[k]))<=tol and abs(abs(a[k])-abs(b[k]))<=tol
        assert compatible(s,a,b,p['modality'],c) is not None
        hs=[p['input_hash_'+x] for x in ('original','a','b')]
        for h in hs:assert digest({m:d['hash'] for m,d in inputs[h].items()})==h
        for m,h in p['unchanged_channel_hashes'].items():
            assert all(inputs[x][m]['hash']==h for x in hs)
            assert len({lookup[x]['processor_channel_hashes'][m] for x in hs})==1
        units[p['source_id'],p['modality']].append(p)
        isolation.append(dict(triplet_id=p['triplet_id'],non_target_content_passed=True,processor_tensors_passed=True))
    assert len(units)==90
    for u in units.values():
        assert sorted(p['donor_group'] for p in u)==[1,2]
        assert len({p[k] for p in u for k in ('donor_a_id','donor_b_id')})==4
    assert not readcsv(RUN/'results/failures.csv')
    assert read_json(RUN/'results/isolation_validation.json')==dict(passed=True,checked_triplets=180)
    repeats=read_json(RUN/'results/repeat_diagnostics.json')
    assert all(r['max_probability_difference']<=c['diagnostics']['repeat_absolute_tolerance'] for r in repeats)
    writecsv(out/'tables/isolation_checks.csv',isolation)
    writecsv(out/'evidence/media_hashes.csv',media_records)
    result=dict(passed=True,frozen_files=len(freeze['files']),source_samples=30,source_modality_units=90,
        triplets=180,main_inputs=390,prediction_records=821,prediction_kinds=dict(Counter(p['kind'] for p in predictions)),
        distinct_donors=len(reuse),donor_reuse_histogram=dict(Counter(reuse.values())),
        channel_payloads=len(checked),media_files=len(media_records),failed_triplets=0,
        frozen_before_first_prediction=freeze['time_utc']<min(r['time_utc'] for r in predictions),
        max_repeat_probability_difference=max(r['max_probability_difference'] for r in repeats),
        original_file_hashes=before,code_sha256_posix=code)
    writejson(out/'evidence/integrity.json',result)
    return lookup,reuse,before

def calculate(c,pairs,lookup,inventory):
    rows=[]
    for p in pairs:
        o,a,b=[lookup[p['input_hash_'+x]] for x in ('original','a','b')]
        da=p['direction']*(a['s']-o['s']);db=p['direction']*(b['s']-o['s']);tol=c['zero_tolerance']
        r={k:p[k] for k in ('triplet_id','source_id','modality','donor_group','donor_a_id','donor_b_id','direction')}
        r.update(s_original=o['s'],s_A=a['s'],s_B=b['s'],delta_A=da,delta_B=db,D=db-da,
            abs_shift_A=abs(a['s']-o['s']),IR_A=int(o['prediction']==a['prediction']),
            B_positive=int(db>tol),B_zero=int(abs(db)<=tol),B_negative=int(db < -tol),
            original_prediction=o['prediction'],A_prediction=a['prediction'],B_prediction=b['prediction'])
        rows.append(r)
    # All original metrics are verified before any new exploratory statistics.
    grouped=defaultdict(list)
    for r in rows:grouped[r['source_id'],r['modality']].append(r)
    source=[dict(source_id=s,modality=m,donor_groups=len(rs),**{k:float(np.mean([r[k] for r in rs])) for k in METRICS})
            for (s,m),rs in sorted(grouped.items())]
    old={(r['modality'],r['metric']):r for r in readcsv(RUN/'results/summary.csv')}
    for m in MODS:
        for k in METRICS:assert abs(np.mean([r[k] for r in source if r['modality']==m])-float(old[m,k]['value']))<1e-12
    return rows,source,grouped

def effects(c,rows,source,grouped,out):
    ids=sorted({r['source_id'] for r in source});n=len(ids)
    cube=np.array([[[r[k] for k in METRICS] for m in MODS for r in source if r['source_id']==s and r['modality']==m] for s in ids])
    rng=np.random.default_rng(c['bootstrap_seed']);draws=rng.integers(0,n,(c['bootstrap_repetitions'],n))
    boot=cube[draws];summary=[]
    for im,m in enumerate(MODS):
        for ik,k in enumerate(METRICS):
            vals=cube[:,im,ik];trip=[r[k] for r in rows if r['modality']==m]
            for stat,estimate,samples in [('mean',vals.mean(),boot[:,:,im,ik].mean(axis=1)),('median',np.median(vals),np.median(boot[:,:,im,ik],axis=1))]:
                lo,hi=ci(samples)
                summary.append(dict(modality=m,metric=k,statistic=stat,value=float(estimate),ci_low=lo,ci_high=hi,
                    source_n=n,triplet_n=len(trip),aggregation='mean within source over two donors, then '+stat+' across sources',
                    role='original_primary' if stat=='mean' else 'posthoc_exploratory',pooled_triplet_median=float(np.median(trip))))
    contrasts=[]
    for ma,mb in combinations(MODS,2):
        ia,ib=MODS.index(ma),MODS.index(mb)
        for k in ('delta_A','delta_B','D'):
            ik=METRICS.index(k);v=cube[:,ia,ik]-cube[:,ib,ik];lo,hi=ci(v[draws].mean(axis=1))
            contrasts.append(dict(modality_a=ma,modality_b=mb,contrast=ma+' minus '+mb,metric=k,source_n=n,
                mean_difference=float(v.mean()),ci_low=lo,ci_high=hi,role='posthoc_exploratory' if k!='D' else 'planned_paired_D'))
    stability=[];stability_summary=[]
    for (s,m),rs in sorted(grouped.items()):
        a,b=sorted(rs,key=lambda r:r['donor_group'])
        for k in ('delta_A','delta_B','D'):
            stability.append(dict(source_id=s,modality=m,metric=k,group1=a[k],group2=b[k],
                sign1=sign(a[k],c['zero_tolerance']),sign2=sign(b[k],c['zero_tolerance']),
                sign_agrees=sign(a[k],c['zero_tolerance'])==sign(b[k],c['zero_tolerance']),
                absolute_difference=abs(a[k]-b[k]),signed_group2_minus_group1=b[k]-a[k]))
    for m in MODS:
        for k in ('delta_A','delta_B','D'):
            rr=[r for r in stability if r['modality']==m and r['metric']==k];a=np.array([r['group1'] for r in rr]);b=np.array([r['group2'] for r in rr])
            agree=np.array([r['sign_agrees'] for r in rr]);diff=np.abs(a-b)
            lo,hi=ci(agree[draws].mean(axis=1));dl,dh=ci(diff[draws].mean(axis=1))
            stability_summary.append(dict(modality=m,metric=k,source_n=n,sign_agreement_count=int(agree.sum()),
                sign_agreement_rate=float(agree.mean()),ci_low=lo,ci_high=hi,mean_absolute_difference=float(diff.mean()),
                absolute_difference_ci_low=dl,absolute_difference_ci_high=dh,median_absolute_difference=float(np.median(diff)),
                pearson_descriptive=float(np.corrcoef(a,b)[0,1]) if min(a.std(),b.std())>0 else None))
    loo=[];loo_summary=[]
    for im,m in enumerate(MODS):
        for ik,k in enumerate(METRICS[:3]):
            vals=cube[:,im,ik];full=vals.mean();means=(vals.sum()-vals)/(n-1)
            for s,value in zip(ids,means):loo.append(dict(removed_source=s,modality=m,metric=k,remaining_n=n-1,full_mean=float(full),mean_without=float(value),change=float(value-full)))
            loo_summary.append(dict(modality=m,metric=k,full_mean=float(full),minimum_without=float(means.min()),maximum_without=float(means.max()),
                min_change=float((means-full).min()),max_change=float((means-full).max()),max_absolute_change=float(np.max(abs(means-full)))))
    exceptions=[dict(r,definition='exact D>0 and delta_B<=0') for r in rows if r['D']>0 and r['delta_B']<=0]
    source_exceptions=[dict(r,definition='source-mean D>0 and source-mean delta_B<=0') for r in source if r['D']>0 and r['delta_B']<=0]
    tolerance=[]
    for t in c['zero_tolerance_sensitivity']:
        for m in MODS:
            rr=[r for r in rows if r['modality']==m];ss=[r for r in source if r['modality']==m]
            tolerance.append(dict(modality=m,tolerance=t,triplet_n=len(rr),triplet_B_positive=sum(r['delta_B']>t for r in rr),
                triplet_B_zero=sum(abs(r['delta_B'])<=t for r in rr),triplet_B_negative=sum(r['delta_B'] < -t for r in rr),
                D_positive_B_nonpositive_with_tolerance=sum(r['D']>t and r['delta_B']<=t for r in rr),
                source_mean_B_positive=sum(r['delta_B']>t for r in ss),source_mean_B_zero=sum(abs(r['delta_B'])<=t for r in ss),
                source_mean_B_negative=sum(r['delta_B'] < -t for r in ss),source_n=n))
    writecsv(out/'tables/triplet_effects.csv',rows);writecsv(out/'tables/source_effects.csv',source)
    for name,data in [('effect_summary',summary),('paired_modality_decomposition',contrasts),('donor_stability_source',stability),
                      ('donor_stability_summary',stability_summary),('leave_one_source_out',loo),('leave_one_source_out_summary',loo_summary),
                      ('D_positive_B_nonpositive_triplets',exceptions),('D_positive_B_nonpositive_sources',source_exceptions),('zero_tolerance',tolerance)]:
        writecsv(out/f'tables/{name}.csv',data,fields=list(data[0]) if data else ['source_id','modality','definition'])
    return summary,contrasts,stability,stability_summary,loo_summary,exceptions,source_exceptions,draws

def structure(c,pairs,lookup,inventory,reuse,source,out):
    originals={p['source_id']:lookup[p['input_hash_original']] for p in pairs};labels=[];long=[]
    for sid,pred in sorted(originals.items()):
        s=inventory[sid];pols={m:polarity(s['y_'+m.lower()]) for m in MODS};multi=polarity(s['y_multi'])
        count=sum(v==multi for v in pols.values())
        agreement='all_four_agree' if count==3 else 'all_modalities_agree_but_multi_differs' if len(set(pols.values()))==1 else 'modality_conflict'
        row=dict(source_id=sid,structure=agreement,modalities_matching_multi=count,**{k:s[k] for k in ('y_t','y_a','y_v','y_multi')},
            **{f'polarity_{m}':v for m,v in pols.items()},polarity_multi=multi,
            **{f'strength_{m}':abs(s['y_'+m.lower()]) for m in MODS},strength_multi=abs(s['y_multi']),
            original_prediction=pred['prediction'],s_original=pred['s'],p_negative=pred['probabilities'][0],p_neutral=pred['probabilities'][1],p_positive=pred['probabilities'][2],
            logit_negative=pred['raw_scores'][0],logit_neutral=pred['raw_scores'][1],logit_positive=pred['raw_scores'][2],
            candidate_max_score=max(pred['probabilities']),confidence_stratum='>=0.9' if max(pred['probabilities'])>=.9 else '<0.9',
            speaker_id=s['speaker_id'],source_video_id=s['source_video_id'],scene_id=s['scene_id'],subtitle_status=s['subtitle_status'],
            language=s['language'],recording_environment=s['recording_environment'],speech_content_conflict='unknown',subtitle_leakage='unknown',
            relation_audit='pending',full_speech_words_preserved=True)
        for m in MODS:
            unique=pols[m]==multi and count==1;row['unique_aligned_'+m]=unique
            long.append(dict(source_id=sid,modality=m,target_polarity=pols[m],target_strength=abs(s['y_'+m.lower()]),
                original_target_aligned=pred['prediction']==pols[m],original_high_confidence=max(pred['probabilities'])>=.9,
                target_only_aligned_multi=unique,relation_status='pending',subtitle_status=s['subtitle_status'],
                speech_content_conflict='unknown',subtitle_leakage='unknown',label_structure=agreement))
        labels.append(row)
    matches=[]
    for p in pairs:
        s,a,b=[inventory[p[k]] for k in ('source_id','donor_a_id','donor_b_id')];m=p['modality'];key='y_'+m.lower()
        measure=(lambda x:len(x['text'])) if m=='T' else (lambda x:x['audio_duration' if m=='A' else 'duration'])
        matches.append(dict(triplet_id=p['triplet_id'],source_id=p['source_id'],modality=m,donor_group=p['donor_group'],
            donor_a=p['donor_a_id'],donor_b=p['donor_b_id'],a_reuse=reuse[p['donor_a_id']],b_reuse=reuse[p['donor_b_id']],
            source_measure=measure(s),a_measure=measure(a),b_measure=measure(b),a_source_ratio=measure(a)/measure(s),b_source_ratio=measure(b)/measure(s),
            ab_measure_ratio=max(measure(a),measure(b))/min(measure(a),measure(b)),source_label=s[key],a_label=a[key],b_label=b[key],
            a_source_intensity_difference=abs(abs(a[key])-abs(s[key])),b_source_intensity_difference=abs(abs(b[key])-abs(s[key])),ab_intensity_difference=abs(abs(a[key])-abs(b[key])),
            source_speaker=s['speaker_id'],a_speaker=a['speaker_id'],b_speaker=b['speaker_id'],source_scene=s['scene_id'],a_scene=a['scene_id'],b_scene=b['scene_id'],
            source_subtitles=s['subtitle_status'],a_subtitles=a['subtitle_status'],b_subtitles=b['subtitle_status'],
            match_features=p['match_features'],match_missingness=p['match_missingness']))
    strat=[];lookup={(r['source_id'],r['modality']):r for r in source}
    planned=['target_polarity','original_target_aligned','original_high_confidence','target_only_aligned_multi',
             'relation_status','subtitle_status','speech_content_conflict','subtitle_leakage']
    for key in planned+['label_structure']:
        for value in sorted({str(r[key]) for r in long}):
            for m in MODS:
                group=[lookup[r['source_id'],m] for r in long if r['modality']==m and str(r[key])==value];n=len(group)
                strat.append(dict(stratum=key,level=value,modality=m,source_n=n,small_cell=n<10,
                    role='planned_descriptive' if key in planned else 'posthoc_exploratory',
                    **{k:float(np.mean([r[k] for r in group])) if n>=10 else None for k in METRICS[:3]}))
    writecsv(out/'tables/source_labels_predictions.csv',labels);writecsv(out/'tables/source_modality_structure.csv',long)
    writecsv(out/'tables/donor_matching.csv',matches);writecsv(out/'tables/strata.csv',strat)
    writecsv(out/'tables/donor_reuse.csv',[dict(clip_id=k,uses=v) for k,v in sorted(reuse.items())])
    info=dict(sources=30,structure_counts=dict(Counter(r['structure'] for r in labels)),matching_multi_counts=dict(Counter(r['modalities_matching_multi'] for r in labels)),
        unique_aligned_counts={m:sum(r['unique_aligned_'+m] for r in labels) for m in MODS},confidence_counts=dict(Counter(r['confidence_stratum'] for r in labels)),
        original_prediction_counts=dict(Counter(r['original_prediction'] for r in labels)),
        metadata_known={k:sum(r[k] not in ('unknown','',None) for r in labels) for k in ('speaker_id','source_video_id','scene_id','subtitle_status','language','recording_environment','speech_content_conflict')})
    writejson(out/'tables/structure_summary.json',info)
    return labels,matches,strat,info

def plots(source,stability,summary,contrasts,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dest=out/'figures';dest.mkdir(parents=True,exist_ok=True)
    colors=['#3978ad','#d88b37','#5b9760']
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    for ax,k in zip(axes,METRICS[:3]):
        for i,m in enumerate(MODS):
            vals=np.array([r[k] for r in source if r['modality']==m]);ax.scatter(i+np.linspace(-.15,.15,len(vals)),vals,color=colors[i],alpha=.6,s=16)
            r=next(r for r in summary if r['modality']==m and r['metric']==k and r['statistic']=='mean')
            ax.errorbar(i,r['value'],yerr=[[r['value']-r['ci_low']],[r['ci_high']-r['value']]],fmt='D',color='black',capsize=4)
        ax.set(xticks=range(3),xticklabels=MODS,title=k,ylabel='Within-source mean over 2 donor groups');ax.axhline(0,color='#777',lw=.7)
    fig.suptitle('All 30 frozen sources; descriptive source-cluster 95% intervals; human audit pending')
    fig.tight_layout();fig.savefig(dest/'effect_decomposition.png',dpi=180);plt.close(fig)
    fig,axes=plt.subplots(3,3,figsize=(11,10))
    for im,m in enumerate(MODS):
        for ik,k in enumerate(METRICS[:3]):
            rr=[r for r in stability if r['modality']==m and r['metric']==k];ax=axes[im,ik]
            a=[r['group1'] for r in rr];b=[r['group2'] for r in rr];ax.scatter(a,b,color=colors[im],s=22,alpha=.7)
            limit=max(.1,max(abs(x) for x in a+b))*1.08;ax.plot([-limit,limit],[-limit,limit],'--',color='#aaa',lw=.7)
            ax.axhline(0,color='#999',lw=.6);ax.axvline(0,color='#999',lw=.6);ax.set(title=f'{m}: {k}',xlabel='Donor group 1',ylabel='Donor group 2')
    fig.suptitle('Same source, independent donor groups; 30 points per panel; audit pending')
    fig.tight_layout();fig.savefig(dest/'donor_stability.png',dpi=180);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    for ax,(a,b) in zip(axes,combinations(MODS,2)):
        rr=[next(r for r in contrasts if r['modality_a']==a and r['modality_b']==b and r['metric']==k) for k in METRICS[:3]]
        for i,r in enumerate(rr):ax.errorbar(i,r['mean_difference'],yerr=[[r['mean_difference']-r['ci_low']],[r['ci_high']-r['mean_difference']]],fmt='o',capsize=4,color=colors[i])
        ax.axhline(0,color='#999',lw=.7);ax.set(title=f'{a} minus {b}: same 30 sources',xticks=range(3),xticklabels=['delta_A','delta_B','D'])
    fig.suptitle('Paired decomposition: difference in D = difference in delta_B - difference in delta_A')
    fig.tight_layout();fig.savefig(dest/'paired_decomposition.png',dpi=180);plt.close(fig)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default=str(HERE));args=p.parse_args();out=Path(args.out).resolve()
    assert out.is_relative_to(HERE.resolve())
    c,pairs,inputs,preds,inv=load()
    lookup,reuse,before=audit_integrity(c,pairs,inputs,preds,inv,out)
    rows,source,grouped=calculate(c,pairs,lookup,inv)
    summary,contrasts,stability,ss,loo,exc,excs,draws=effects(c,rows,source,grouped,out)
    labels,matches,strat,info=structure(c,pairs,lookup,inv,reuse,source,out)
    plots(source,stability,summary,contrasts,out)
    writejson(out/'evidence/analysis_manifest.json',dict(time_utc=now(),run=str(RUN),model_inference=False,training=False,
        original_sample_changes=False,bootstrap_seed=c['bootstrap_seed'],bootstrap_repetitions=c['bootstrap_repetitions'],
        resampling='single common source draw; retain all modalities and both donor groups',zero_tolerance=c['zero_tolerance'],
        original_primary_metrics_preserved=True,source_file_unchanged=frozen_hashes()==before,
        posthoc_additions=['medians','delta_A donor stability','paired A/B decomposition','leave-one-source-out','blind form clarity and accessibility fields']))
    assert frozen_hashes()==before
    print('VERIFIED_AND_ANALYZED',len(rows),'triplets;',len(source),'source-modality units; no inference')

if __name__=='__main__':main()
