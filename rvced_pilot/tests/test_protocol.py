"""Synthetic values below are unit fixtures only, never experimental results."""
from collections import Counter
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common import Blocked, cache_key, digest, load_config, normalize_scores, polarity
from src.analysis import cluster_draws, completion_counts, source_aggregate, triplet_metrics, summarize_sources
from src.data import candidate_matrix, compatible, independent_groups, select_pairs
from src.media import input_hash, replace_channel, validate_pair_hashes
from src.model import candidate_tokens, single_chat_text

CONFIG = load_config(Path(__file__).resolve().parents[1] / 'configs/pilot.yaml')


def sample(sid, y):
    return dict(clip_id=sid, split='test', eligible=True, text='fixture', y_t=y, y_a=y, y_v=y,
                y_multi=y, duration=2., audio_duration=2., video_width=640, video_height=480,
                speaker_id='unknown', source_video_id='video', scene_id='unknown',
                language='unknown', recording_environment='unknown', subtitle_status='unknown')


def test_label_boundaries_and_direction():
    assert [polarity(x) for x in (-1, -.4, -.2, 0, .2, .4, 1)] == [-1, -1, 0, 0, 0, 1, 1]
    c = copy.deepcopy(CONFIG)
    c['selection']['sources_per_modality'] = 1
    rows = [sample(str(i), .6 if i % 2 else -.6) for i in range(24)]
    pairs, _, _ = select_pairs(rows, c, 'main')
    assert pairs
    for p in pairs:
        assert p['direction'] == (-1 if p['source_target_score'] > 0 else 1)
        assert p['source_target_score'] * p['donor_a_target_score'] > 0
        assert p['source_target_score'] * p['donor_b_target_score'] < 0


def test_scores_and_D_counterexample():
    r = normalize_scores([0., 0., 0.])
    assert r['s'] == 0
    result = normalize_scores([-2., 0., 2.])
    assert result['s'] == pytest.approx(result['probabilities'][2] - result['probabilities'][0])
    with pytest.raises(ValueError):
        normalize_scores([float('nan'), 0, 1])
    # Positive D can arise when BOTH interventions move against the expected direction.
    m = triplet_metrics(0., -.5, -.2, 1, 0, -1, 1e-6)
    assert m['D'] > 0 and m['delta_B'] < 0


def test_non_target_hash_and_tamper_detection():
    src = {m: dict(hash=digest([m, 'source'])) for m in ('T', 'A', 'V')}
    donor = {m: dict(hash=digest([m, 'donor'])) for m in ('T', 'A', 'V')}
    a = replace_channel(src, donor, 'A')
    assert a['T'] is src['T'] and a['V'] is src['V']
    inputs = {input_hash(x): x for x in (src, a)}
    pair = dict(input_hash_original=input_hash(src), input_hash_a=input_hash(a), input_hash_b=input_hash(a),
                unchanged_channel_hashes={m: src[m]['hash'] for m in ('T', 'V')})
    validate_pair_hashes(pair, inputs)
    a['V'] = dict(hash='tampered')
    with pytest.raises(Blocked):
        validate_pair_hashes(pair, inputs)


def test_strict_reuse_disjointness_and_independent_groups():
    rows = [sample(str(i), .6 if i % 2 else -.6) for i in range(60)]
    pairs, _, stats = select_pairs(rows, CONFIG, 'main')
    donors = Counter(p[k] for p in pairs for k in ('donor_a_id', 'donor_b_id'))
    assert donors and max(donors.values()) <= 2
    assert not ({p['source_id'] for p in pairs} & set(donors))
    for unit in {(p['source_id'], p['modality']) for p in pairs}:
        group = [p for p in pairs if (p['source_id'], p['modality']) == unit]
        assert len(group) == 2
        assert len({p[k] for p in group for k in ('donor_a_id', 'donor_b_id')}) == 4
    assert select_pairs(rows, CONFIG, 'main') == select_pairs(rows, CONFIG, 'main')


def test_global_selection_cannot_relax_constraints():
    source = sample('s', .6)
    a, b = sample('a', .6), sample('b', -.6)
    a['speaker_id'] = 'known'
    assert compatible(source, a, b, 'T', CONFIG) is not None  # source unknown: both unknown
    source['speaker_id'] = 'known'
    assert compatible(source, a, b, 'T', CONFIG) is None
    source['speaker_id'] = a['speaker_id'] = b['speaker_id'] = 'known'
    b['duration'] = 3.
    assert compatible(source, a, b, 'V', CONFIG) is None
    b['duration'] = 2.
    option = (a, b, {}, [])
    assert independent_groups([option], 2, Counter(), set(), 2) == []
    assert independent_groups([option], 1, Counter(a=2), set(), 2) == []


def test_vectorized_candidate_constraints_equal_scalar_reference():
    source = sample('source', .6)
    source.update(speaker_id='s1', source_video_id='v1', scene_id='scene1', text='abcdef')
    donors = []
    for i in range(30):
        row = sample(str(i), [.4, .6, .8, -.4, -.6, -.8][i % 6])
        row.update(speaker_id=['s1', 's2', 'unknown'][i % 3], source_video_id=['v1', 'v2'][i % 2],
                   scene_id=['scene1', 'scene2'][i % 2], duration=1.6 + (i % 5) * .2,
                   audio_duration=1.6 + (i % 5) * .2, text='x' * (3 + i % 9))
        donors.append(row)
    for modality in ('T', 'A', 'V'):
        aa, bb, mask = candidate_matrix(source, donors, modality, CONFIG)
        for i, a in enumerate(aa):
            for j, b in enumerate(bb):
                assert bool(mask[i, j]) == (compatible(source, a, b, modality, CONFIG) is not None)


def test_source_weighting_and_cluster_preservation():
    rows = []
    for sid, m, vals in [('s1', 'T', [0., 2.]), ('s2', 'T', [0.]), ('s1', 'A', [1., 3.])]:
        for val in vals:
            rows.append(dict(source_id=sid, modality=m, **{k: val for k in
                ['delta_A', 'delta_B', 'D', 'IR_A', 'abs_shift_A', 'B_positive', 'B_zero', 'B_negative', 's_original', 's_a', 's_b']}))
    src = source_aggregate(rows)
    assert summarize_sources(src)['T']['D'] == .5  # (mean(0,2) + 0)/2, not mean(0,2,0)
    for draw in cluster_draws(src, 50, 20261016):
        assert sum(r['source_id'] == 's1' and r['modality'] == 'T' for r in draw) == sum(r['source_id'] == 's1' and r['modality'] == 'A' for r in draw)
    assert list(cluster_draws(src, 5, 1)) == list(cluster_draws(src, 5, 1))


def test_cache_provenance_every_component():
    base = ['model-v1', 'content', 'prompt', {'frames': 8}, {'method': 'next_token'}]
    old = cache_key(*base)
    for index in range(5):
        changed = base[:]
        changed[index] = {'changed': True}
        assert cache_key(*changed) != old


def test_interrupted_prediction_journal_is_recoverable(tmp_path):
    from src.common import ensure_layout, prediction_journal
    ensure_layout(tmp_path)
    path = tmp_path / 'results/predictions.jsonl'
    path.write_bytes(b'{"cache_key":"unit-test-only"}\n{"cache_key":')
    assert prediction_journal(tmp_path) == [{'cache_key': 'unit-test-only'}]
    assert list((tmp_path / 'evidence').glob('prediction_journal_original_*.jsonl'))
    assert path.read_bytes().endswith(b'\n')
    path.write_bytes(b'{broken}\n{"cache_key":"unit-test-only"}\n')
    with pytest.raises(Blocked):
        prediction_journal(tmp_path)


class TokenizerFixture:
    def encode(self, value, add_special_tokens=False):
        return [ord(c) for c in value]

    def decode(self, values, skip_special_tokens=False):
        return ''.join(chr(x) for x in values)


def test_answer_prefix_tokens_and_no_silent_fallback():
    tok = TokenizerFixture()
    assert candidate_tokens(tok, '<assistant>\n', ['A', 'B', 'C'], 'next_token') == [[65], [66], [67]]
    with pytest.raises(Blocked):
        candidate_tokens(tok, '<assistant>\n', ['AA', 'B', 'C'], 'next_token')
    assert len(candidate_tokens(tok, '<assistant>\n', ['AA', 'B', 'C'], 'sequence_log_likelihood')[0]) == 2
    assert single_chat_text(['<assistant>\n']) == single_chat_text('<assistant>\n')
    with pytest.raises(Blocked):
        single_chat_text(['first', 'second'])
    with pytest.raises(Blocked):
        single_chat_text([])


def test_failure_denominators_in_analysis(tmp_path):
    from src.common import ensure_layout, write_csv, write_json, write_jsonl, INVENTORY_FIELDS
    from src.analysis import analyze
    ensure_layout(tmp_path)
    write_csv(tmp_path / 'manifests/inventory.csv', [], INVENTORY_FIELDS)
    write_json(tmp_path / 'manifests/freeze.json', dict(status='blocked_not_frozen'))
    write_jsonl(tmp_path / 'manifests/pairs.proposed.jsonl', [])
    analyze(tmp_path, CONFIG)
    from src.common import read_csv
    summary = read_csv(tmp_path / 'results/summary.csv')
    assert summary
    assert all(r['value'] == '' and r['failure_rate'] == '' for r in summary)
    assert all(r['status'] == '标签规则构造，关系审核待完成' for r in summary)
    assert completion_counts([1, 2, 3], [1]) == dict(intended_triplets=3, complete_triplets=1,
                    failed_or_incomplete_triplets=2, failure_rate=2 / 3)
    assert completion_counts([1, 2], [])['failure_rate'] == 1
    assert completion_counts([], [])['failure_rate'] is None


def test_human_reviews_are_required_and_recomputed_without_model(tmp_path):
    from src.audit import ISSUES, REVIEW_FIELDS, audit_results
    from src.common import ensure_layout, write_csv, write_json, write_jsonl
    ensure_layout(tmp_path)
    p = dict(triplet_id='fixture', source_id='src', modality='T', donor_group=1)
    write_jsonl(tmp_path / 'manifests/pairs.proposed.jsonl', [p])
    keys, reviews = {}, []
    for cond in ('original', 'a', 'b'):
        for stage in ('channel', 'combination'):
            iid = cond + stage
            keys[iid] = dict(**p, condition=cond, stage=stage)
            for reviewer in ('human_fixture_1', 'human_fixture_2'):
                row = dict(reviewer_id=reviewer, item_id=iid, stage=stage)
                if stage == 'channel':
                    row.update(polarity=-1 if cond == 'b' else 1, intensity=3, confidence=3)
                else:
                    row.update({field: 0 for field in ISSUES})
                reviews.append(row)
    write_json(tmp_path / 'audit/key.private.json', keys)
    _, summary, relations, high = audit_results(tmp_path, None, CONFIG)
    assert relations['fixture'] is None and not high['fixture']
    write_csv(tmp_path / 'reviews.csv', reviews, REVIEW_FIELDS)
    _, summary, relations, high = audit_results(tmp_path, tmp_path / 'reviews.csv', CONFIG)
    assert relations['fixture'] and high['fixture']
    assert summary['relation_pass_rate'] == summary['polarity_agreement'] == 1
    # One-sided scene discontinuity removes HQ eligibility but preserves relation ratings.
    for row in reviews:
        if row['item_id'] == 'acombination':
            row['scene_discontinuity'] = 1
    write_csv(tmp_path / 'reviews.csv', reviews, REVIEW_FIELDS)
    _, _, relations, high = audit_results(tmp_path, tmp_path / 'reviews.csv', CONFIG)
    assert relations['fixture'] and not high['fixture']
