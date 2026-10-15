from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import math

import numpy as np

from .audit import ISSUES, audit_results
from .common import (Blocked, MODALITIES, STATUS, cache_key, digest, polarity, prediction_journal, read_json, read_jsonl,
                     verify_frozen, write_csv, write_json)
from .data import load_inventory
from .media import input_hash

TRIPLET_FIELDS = ['triplet_id', 'source_id', 'modality', 'donor_group', 'donor_a_id', 'donor_b_id',
                  'direction', 's_original', 's_a', 's_b', 'delta_A', 'delta_B', 'D', 'IR_A',
                  'abs_shift_A', 'B_positive', 'B_zero', 'B_negative', 'relation_pass', 'high_quality',
                  'target_polarity', 'original_target_aligned', 'original_high_confidence',
                  'target_only_aligned_multi', 'subtitle_status', *ISSUES]
METRICS = ['delta_A', 'delta_B', 'D', 'IR_A', 'abs_shift_A', 'B_positive', 'B_zero', 'B_negative',
           's_original', 's_a', 's_b']


def triplet_metrics(s0, sa, sb, direction, pred0, preda, tol):
    da, db = direction * (sa - s0), direction * (sb - s0)
    return dict(delta_A=da, delta_B=db, D=db - da, IR_A=float(pred0 == preda),
                abs_shift_A=abs(sa - s0), B_positive=float(db > tol), B_zero=float(abs(db) <= tol),
                B_negative=float(db < -tol), s_original=s0, s_a=sa, s_b=sb)


def source_aggregate(triplets):
    grouped = defaultdict(list)
    for row in triplets:
        grouped[(row['source_id'], row['modality'])].append(row)
    out = []
    for (source, m), group in sorted(grouped.items()):
        out.append(dict(source_id=source, modality=m, complete_groups=len(group),
                        **{k: float(np.mean([r[k] for r in group])) for k in METRICS}))
    return out


def completion_counts(intended, completed):
    n = len(intended)
    finished = len(completed)
    return dict(intended_triplets=n, complete_triplets=finished,
                failed_or_incomplete_triplets=n - finished,
                failure_rate=(n - finished) / n if n else None)


def cluster_draws(source_rows, repetitions, seed, cluster_key='source_id'):
    """Draw once globally, retaining every modality row in each sampled source/cluster."""
    grouped = defaultdict(list)
    for row in source_rows:
        grouped[row[cluster_key]].append(row)
    ids = sorted(grouped)
    if not ids:
        return
    rng = np.random.default_rng(seed)
    for _ in range(repetitions):
        sampled = rng.choice(len(ids), size=len(ids), replace=True)
        yield [row for ix in sampled for row in grouped[ids[int(ix)]]]


def summarize_sources(source_rows):
    out = {}
    for m in MODALITIES:
        rows = [r for r in source_rows if r['modality'] == m]
        out[m] = dict(n_sources=len(rows), **{k: float(np.mean([r[k] for r in rows])) if rows else None for k in METRICS})
    return out


def bootstrap(source_rows, c, cluster_key='source_id'):
    distributions = defaultdict(list)
    for sampled in cluster_draws(source_rows, c['bootstrap_repetitions'], c['bootstrap_seed'], cluster_key):
        summary = summarize_sources(sampled)
        for m in MODALITIES:
            for metric in METRICS:
                if summary[m][metric] is not None:
                    distributions[(m, metric)].append(summary[m][metric])
    return {m: {k: dict(low=float(np.quantile(distributions[(m, k)], .025)),
                        high=float(np.quantile(distributions[(m, k)], .975)),
                        defined_replicates=len(distributions[(m, k)]))
                if distributions[(m, k)] else dict(low=None, high=None, defined_replicates=0)
                for k in METRICS} for m in MODALITIES}


def paired_modalities(source_rows, c):
    lookup = {(r['source_id'], r['modality']): r for r in source_rows}
    out = []
    for ma, mb in combinations(MODALITIES, 2):
        ids = sorted({s for s, m in lookup if m == ma} & {s for s, m in lookup if m == mb})
        diffs = [lookup[s, ma]['D'] - lookup[s, mb]['D'] for s in ids]
        boot = []
        if ids:
            rng = np.random.default_rng(c['bootstrap_seed'])
            for _ in range(c['bootstrap_repetitions']):
                ix = rng.integers(0, len(ids), len(ids))
                boot.append(float(np.mean([diffs[int(i)] for i in ix])))
        out.append(dict(modality_a=ma, modality_b=mb, common_sources=len(ids),
                        paired_D_difference=float(np.mean(diffs)) if diffs else None,
                        ci_low=float(np.quantile(boot, .025)) if boot else None,
                        ci_high=float(np.quantile(boot, .975)) if boot else None,
                        interpretation='feasibility_only' if len(ids) < 10 else 'descriptive_paired_difference'))
    return out


def group_stability(triplets, tol):
    grouped = defaultdict(list)
    for r in triplets:
        grouped[(r['source_id'], r['modality'])].append(r)
    out = []
    def sg(v):
        return 1 if v > tol else -1 if v < -tol else 0
    for (sid, m), rows in sorted(grouped.items()):
        if len(rows) != 2:
            continue
        a, b = sorted(rows, key=lambda r: r['donor_group'])
        out.append(dict(source_id=sid, modality=m, delta_B_1=a['delta_B'], delta_B_2=b['delta_B'],
                        D_1=a['D'], D_2=b['D'], delta_B_direction_agrees=sg(a['delta_B']) == sg(b['delta_B']),
                        D_sign_agrees=sg(a['D']) == sg(b['D']),
                        delta_B_amplitude_difference=abs(a['delta_B'] - b['delta_B']),
                        D_amplitude_difference=abs(a['D'] - b['D'])))
    return out


def matching_statistics(pairs, inventory):
    out = []
    for m in MODALITIES:
        subset = [p for p in pairs if p['modality'] == m]
        names = ['text_characters'] if m == 'T' else ['audio_duration', 'audio_sr'] if m == 'A' else ['duration', 'video_width', 'video_height', 'video_fps']
        for feature in names:
            a, b, relative_a, relative_b = [], [], [], []
            for pair in subset:
                src, aa, bb = [inventory[pair[key]] for key in ('source_id', 'donor_a_id', 'donor_b_id')]
                value = lambda row: len(row['text']) if feature == 'text_characters' else row[feature]
                x, y, z = value(src), value(aa), value(bb)
                if x is not None and y is not None and z is not None:
                    a.append(y)
                    b.append(z)
                    relative_a.append(y / x)
                    relative_b.append(z / x)
            variance = (np.var(a) + np.var(b)) / 2 if a else 0
            out.append(dict(modality=m, feature=feature, pairs=len(a),
                a_mean=float(np.mean(a)) if a else None, b_mean=float(np.mean(b)) if b else None,
                a_median=float(np.median(a)) if a else None, b_median=float(np.median(b)) if b else None,
                mean_paired_b_minus_a=float(np.mean(np.array(b) - a)) if a else None,
                mean_absolute_paired_difference=float(np.mean(np.abs(np.array(b) - a))) if a else None,
                standardized_mean_difference=float((np.mean(b) - np.mean(a)) / np.sqrt(variance)) if variance > 0 else None,
                mean_a_source_ratio=float(np.mean(relative_a)) if a else None,
                mean_b_source_ratio=float(np.mean(relative_b)) if b else None))
    return out


def figures(run, triplets, stability, pairs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    run = Path(run)
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.7))
    for ax, metric in zip(axes, ('delta_A', 'delta_B', 'D')):
        any_data = False
        for m in MODALITIES:
            rows = [r[metric] for r in triplets if r['modality'] == m]
            if rows:
                ax.hist(rows, bins=np.linspace(-4, 4, 33) if metric == 'D' else np.linspace(-2, 2, 33),
                        alpha=.4, label=f'{m}: triplets N={len(rows)}')
                any_data = True
        ax.set_title(metric)
        ax.axvline(0, color='#555', linewidth=.7)
        if any_data:
            ax.legend(fontsize=8)
        else:
            ax.text(.5, .5, 'N=0\nNo real model scores available', ha='center', transform=ax.transAxes)
    fig.suptitle('Label-constructed; human relation audit pending')
    fig.tight_layout()
    fig.savefig(run / 'figures/effect_distributions.png', dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.7))
    for ax, m in zip(axes, MODALITIES):
        rows = [r for r in stability if r['modality'] == m]
        ax.scatter([r['delta_B_1'] for r in rows], [r['delta_B_2'] for r in rows], alpha=.6)
        ax.axhline(0, color='#888', linewidth=.6)
        ax.axvline(0, color='#888', linewidth=.6)
        ax.set(title=f'{m}: paired sources N={len(rows)}', xlabel='Donor group 1: delta_B', ylabel='Donor group 2: delta_B')
    fig.tight_layout()
    fig.savefig(run / 'figures/donor_stability.png', dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.7))
    for ax, m in zip(axes, MODALITIES):
        subset = [p for p in pairs if p['modality'] == m]
        ra = [p['match_features']['a_measure'] / p['match_features']['source_measure'] for p in subset]
        rb = [p['match_features']['b_measure'] / p['match_features']['source_measure'] for p in subset]
        if subset:
            ax.scatter(ra, rb, alpha=.4)
        ax.plot([.5, 2], [.5, 2], '--', color='#888')
        ax.set(title=f'{m}: constructed triplets N={len(subset)}', xlabel='A/source length or duration', ylabel='B/source length or duration')
    fig.tight_layout()
    fig.savefig(run / 'figures/matching_balance.png', dpi=150)
    plt.close(fig)


def analyze(run, c, reviews_path=None):
    run = Path(run)
    freeze = read_json(run / 'manifests/freeze.json', {})
    frozen = freeze.get('status') == 'frozen'
    if frozen:
        verify_frozen(run, c)
    pairs = read_jsonl(run / 'manifests/pairs.jsonl') if frozen else []
    proposals = read_jsonl(run / 'manifests/pairs.proposed.jsonl')
    rows = load_inventory(run)
    inventory = {r['clip_id']: r for r in rows}
    balance = matching_statistics(pairs or proposals, inventory)
    write_csv(run / 'results/matching_balance.csv', balance,
              ['modality', 'feature', 'pairs', 'a_mean', 'b_mean', 'a_median', 'b_median',
               'mean_paired_b_minus_a', 'mean_absolute_paired_difference', 'standardized_mean_difference',
               'mean_a_source_ratio', 'mean_b_source_ratio'])
    human_rows, human_summary, relations, high_quality = audit_results(run, reviews_path, c)
    write_json(run / 'results/audit_summary.json', human_summary)
    write_csv(run / 'results/audit_relations.csv', human_rows,
              ['triplet_id', 'relation_pass', 'high_quality', 'complete_independent_reviewers', *ISSUES])
    human_by_triplet = {r['triplet_id']: r for r in human_rows}
    predictions = prediction_journal(run)
    lookup = {}
    identity = read_json(run / 'manifests/model_identity.json', {})
    prompt = (run / 'manifests/prompt.frozen.txt').read_text(encoding='utf-8') if frozen else ''
    for p in predictions:
        if p['kind'] != 'main':
            continue
        if not frozen or p['cache_key'] != cache_key(identity, p['input_hash'], prompt, c['preprocessing'], c['scoring']):
            raise Blocked('Prediction provenance does not match the frozen model/input/prompt/config')
        if p.get('scoring_method') != c['scoring']['method'] or p.get('codes') != c['scoring']['codes']:
            raise Blocked('Mixed scoring definitions or candidate mappings in main results')
        probs = p['probabilities']
        if len(probs) != 3 or not all(math.isfinite(v) and 0 <= v <= 1 for v in probs) or abs(sum(probs) - 1) > 1e-6:
            raise Blocked('Invalid model candidate scores')
        if abs(p['s'] - (probs[2] - probs[0])) > 1e-6:
            raise Blocked('Invalid signed score')
        lookup[p['input_hash']] = p
    technical = read_json(run / 'results/isolation_validation.json', {})
    if lookup and not technical.get('passed'):
        raise Blocked('Technical isolation not verified; no interpretable continuous analysis')
    complete = []
    for p in pairs:
        hs = [p['input_hash_' + x] for x in ('original', 'a', 'b')]
        if not all(h in lookup for h in hs):
            continue
        original, aa, bb = [lookup[h] for h in hs]
        source = inventory[p['source_id']]
        ym = polarity(source['y_multi'], c['selection']['tau'])
        target = polarity(p['source_target_score'], c['selection']['tau'])
        aligned = {m: polarity(source['y_' + m.lower()], c['selection']['tau']) == ym for m in MODALITIES}
        r = {k: p[k] for k in ('triplet_id', 'source_id', 'modality', 'donor_group', 'donor_a_id', 'donor_b_id', 'direction')}
        r.update(triplet_metrics(original['s'], aa['s'], bb['s'], p['direction'],
                                 original['prediction'], aa['prediction'], c['zero_tolerance']))
        r.update(relation_pass=relations.get(p['triplet_id']), high_quality=high_quality.get(p['triplet_id'], False),
                  target_polarity=target, original_target_aligned=original['prediction'] == target,
                  original_high_confidence=max(original['probabilities']) >= .9,
                  target_only_aligned_multi=aligned[p['modality']] and sum(aligned.values()) == 1,
                  subtitle_status=source['subtitle_status'])
        r.update({k: human_by_triplet.get(p['triplet_id'], {}).get(k, 'unknown') for k in ISSUES})
        complete.append(r)
    sources = source_aggregate(complete)
    write_csv(run / 'results/triplet_level.csv', complete, TRIPLET_FIELDS)
    write_csv(run / 'results/source_level.csv', sources, ['source_id', 'modality', 'complete_groups', *METRICS])
    summary, cis = summarize_sources(sources), bootstrap(sources, c)
    flat = []
    for m in MODALITIES:
        intended = [p for p in pairs if p['modality'] == m]
        done = [r for r in complete if r['modality'] == m]
        unit_intended = len({p['source_id'] for p in intended})
        for metric in METRICS:
            flat.append(dict(modality=m, metric=metric, value=summary[m][metric],
                   ci_low=cis[m][metric]['low'], ci_high=cis[m][metric]['high'],
                   bootstrap_defined_replicates=cis[m][metric]['defined_replicates'],
                   **completion_counts(intended, done),
                   proposed_triplets=sum(p['modality'] == m for p in proposals),
                   intended_source_units=unit_intended, unique_complete_sources=summary[m]['n_sources'],
                   unique_donors=len({p[k] for p in intended for k in ('donor_a_id', 'donor_b_id')}),
                   inference_scope='feasibility_only' if summary[m]['n_sources'] < 10 else 'descriptive_pilot',
                   status=STATUS))
    fields = ['modality', 'metric', 'value', 'ci_low', 'ci_high', 'bootstrap_defined_replicates',
              'intended_triplets', 'proposed_triplets', 'complete_triplets', 'failed_or_incomplete_triplets', 'failure_rate',
              'intended_source_units', 'unique_complete_sources', 'unique_donors', 'inference_scope', 'status']
    write_csv(run / 'results/summary.csv', flat, fields)
    write_json(run / 'results/bootstrap.json', dict(seed=c['bootstrap_seed'], repetitions=c['bootstrap_repetitions'],
              resampling_unit='source_id', retains_all_modalities=True, cis=cis,
              limitation='Source uncertainty conditional on this donor construction; donor reuse not fully accounted for'))
    comparisons = paired_modalities(sources, c)
    write_csv(run / 'results/common_source_comparisons.csv', comparisons,
              ['modality_a', 'modality_b', 'common_sources', 'paired_D_difference', 'ci_low', 'ci_high', 'interpretation'])
    stability = group_stability(complete, c['zero_tolerance'])
    write_csv(run / 'results/donor_stability.csv', stability,
              ['source_id', 'modality', 'delta_B_1', 'delta_B_2', 'D_1', 'D_2', 'delta_B_direction_agrees',
               'D_sign_agrees', 'delta_B_amplitude_difference', 'D_amplitude_difference'])
    stability_summary = {}
    for m in MODALITIES:
        group = [r for r in stability if r['modality'] == m]
        stability_summary[m] = dict(paired_source_units=len(group), **{k: float(np.mean([r[k] for r in group])) if group else None for k in
                                  ('delta_B_direction_agrees', 'D_sign_agrees', 'delta_B_amplitude_difference', 'D_amplitude_difference')})
    write_json(run / 'results/donor_stability_summary.json', stability_summary)
    sensitivity = []
    for tol in c['zero_tolerance_sensitivity']:
        altered = [dict(r, B_positive=float(r['delta_B'] > tol), B_zero=float(abs(r['delta_B']) <= tol),
                        B_negative=float(r['delta_B'] < -tol)) for r in complete]
        summ = summarize_sources(source_aggregate(altered))
        for m in MODALITIES:
            sensitivity.append(dict(modality=m, tolerance=tol, sources=summ[m]['n_sources'],
                                    **{k: summ[m][k] for k in ('B_positive', 'B_zero', 'B_negative')}))
    write_csv(run / 'results/zero_tolerance_sensitivity.csv', sensitivity,
              ['modality', 'tolerance', 'sources', 'B_positive', 'B_zero', 'B_negative'])
    reuse = Counter(p[k] for p in pairs for k in ('donor_a_id', 'donor_b_id'))
    no_reuse = [r for r in complete if reuse[r['donor_a_id']] == reuse[r['donor_b_id']] == 1]
    high = [r for r in complete if r['high_quality']]
    write_json(run / 'results/subsets.json', dict(no_donor_reuse=summarize_sources(source_aggregate(no_reuse)),
                 audited_high_quality=summarize_sources(source_aggregate(high)),
                 donor_reuse_histogram=dict(Counter(reuse.values())),
                 caution='Small subsets (<10 sources per modality) support feasibility descriptions only'))
    # Higher-level grouping uses documented CSV metadata, never inferred speaker identities.
    higher = {}
    for key in ('source_video_id', 'speaker_id'):
        valid = [dict(r, cluster=inventory[r['source_id']][key]) for r in sources if inventory[r['source_id']].get(key) not in ('unknown', '', None)]
        count = len({r['cluster'] for r in valid})
        higher[key] = dict(known_sources=len({r['source_id'] for r in valid}), clusters=count,
                           intervals=bootstrap(valid, c, 'cluster') if count >= 10 else None,
                           status='descriptive sensitivity' if count >= 10 else 'too few reliable clusters')
    write_json(run / 'results/higher_cluster_sensitivity.json', higher)
    strata = []
    for key in ('target_polarity', 'original_target_aligned', 'original_high_confidence',
                'target_only_aligned_multi', 'relation_pass', 'subtitle_status', 'speech_content_conflict', 'subtitle_leakage'):
        for value in sorted({str(r[key]) for r in complete}):
            subset = [r for r in complete if str(r[key]) == value]
            for m, ss in summarize_sources(source_aggregate(subset)).items():
                strata.append(dict(stratum=key, level=value, modality=m, sources=ss['n_sources'],
                                   mean_D=ss['D'] if ss['n_sources'] >= 10 else None,
                                   interpretation='counts_only' if ss['n_sources'] < 10 else 'descriptive'))
    write_csv(run / 'results/strata.csv', strata, ['stratum', 'level', 'modality', 'sources', 'mean_D', 'interpretation'])
    originals = {p['source_id']: lookup.get(p['input_hash_original']) for p in pairs}
    originals = {sid: p for sid, p in originals.items() if p is not None}
    natural = dict(n=len(originals))
    for m in ('multi', 't', 'a', 'v'):
        vals = [pred['prediction'] == polarity(inventory[sid]['y_' + m], c['selection']['tau']) for sid, pred in originals.items()]
        natural['accuracy' if m == 'multi' else 'agreement_' + m] = float(np.mean(vals)) if vals else None
    write_json(run / 'results/natural_samples.json', natural)
    single_rows = []
    all_inputs = read_json(run / 'manifests/inputs.json', {})
    expected = {}
    for p in pairs:
        for cond, label_key in (('original', 'source_target_score'), ('a', 'donor_a_target_score'), ('b', 'donor_b_target_score')):
            ch = all_inputs[p['input_hash_' + cond]]
            h = input_hash({p['modality']: ch[p['modality']]})
            expected[h] = (p['modality'], polarity(p[label_key], c['selection']['tau']))
    for p in predictions:
        if p['kind'] == 'single' and p['input_hash'] in expected:
            m, y = expected[p['input_hash']]
            single_rows.append(dict(input_hash=p['input_hash'], modality=m, label_polarity=y,
                                    prediction=p['prediction'], agrees=p['prediction'] == y, s=p['s']))
    write_csv(run / 'results/single_modality_diagnostics.csv', single_rows,
              ['input_hash', 'modality', 'label_polarity', 'prediction', 'agrees', 's'])
    swapped = [dict(input_hash=p['input_hash'], baseline_prediction=lookup[p['input_hash']]['prediction'],
                    swapped_prediction=p['prediction'], agrees=p['prediction'] == lookup[p['input_hash']]['prediction'])
               for p in predictions if p['kind'] == 'swapped' and p['input_hash'] in lookup]
    write_csv(run / 'results/code_mapping_diagnostics.csv', swapped,
              ['input_hash', 'baseline_prediction', 'swapped_prediction', 'agrees'])
    paradoxes = [r['triplet_id'] for r in complete if r['D'] > c['zero_tolerance'] and r['delta_B'] < -c['zero_tolerance']]
    write_json(run / 'results/analysis_status.json', dict(status=STATUS, frozen=frozen,
               proposed_triplets=len(proposals), intended_triplets=len(pairs), complete_triplets=len(complete),
               unique_model_inputs=len(lookup), positive_D_negative_delta_B=dict(count=len(paradoxes), triplet_ids=paradoxes),
               interpretation='D>0 does not imply delta_B>0; matched direction difference is not a net causal effect',
               resource_blocked=not frozen, input_type='real_data_only'))
    figures(run, complete, stability, pairs or proposals)
