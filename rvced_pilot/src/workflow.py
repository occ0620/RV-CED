from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os
import shutil
import time

from .common import (Blocked, MODALITIES, ROOT, append_jsonl, cache_key, code_fingerprint,
                     dependencies, digest, environment, failure, file_hash, now, read_json,
                     prediction_journal, read_jsonl, verify_frozen, write_csv, write_json, write_jsonl)
from .data import load_inventory, select_pairs
from .media import input_hash, materialize_pairs, validate_pair_hashes, verify_channels
from .model import QwenScorer, model_identity


def validation_signature(c, identity, prompt):
    return digest(dict(model=identity, prompt=prompt, preprocessing=c['preprocessing'],
                       scoring=c['scoring'], diagnostics=c['diagnostics'], code=code_fingerprint()))


def doctor(run, c):
    env = environment()
    problems = []
    labels = c['dataset'].get('labels_path')
    if not labels or not Path(labels).is_file():
        problems.append('CH-SIMS v2 supervised CSV missing')
    media = c['dataset'].get('media_root')
    if not media or not Path(media).is_dir():
        problems.append('Raw audio/video root missing')
    if not env['cuda_available']:
        problems.append('CUDA unavailable in active Python environment')
    for name in ('transformers', 'qwen-omni-utils', 'accelerate'):
        if not env['dependencies'].get(name):
            problems.append('Missing dependency: ' + name)
    for name in ('ffmpeg', 'ffprobe'):
        if not env[name]:
            problems.append('Missing media executable: ' + name)
    if not c['model'].get('local_path') or not Path(c['model']['local_path']).is_dir():
        problems.append('Qwen2.5-Omni-7B local weights unavailable')
    env['blockers'] = problems
    env['filesystem_scan_scope'] = 'Workspace, user-supplied dataset, configured paths, common HF/ModelScope caches; not an exhaustive scan of all drives'
    write_json(Path(run) / 'evidence/environment.json', env)
    write_json(Path(run) / 'evidence/dependencies.json', env['dependencies'])
    for item in problems:
        failure(run, 'doctor', 'resource_missing', item)
    print(json.dumps(dict(cuda=env['cuda_available'], gpus=env['gpus'], blockers=problems), ensure_ascii=False, indent=2))
    return env


def build(run, c, phase='main', smoke_dir=None):
    run = Path(run)
    if read_json(run / 'manifests/freeze.json', {}).get('status') == 'frozen':
        return verify_frozen(run, c)
    rows = load_inventory(run)
    pairs, candidates, stats = select_pairs(rows, c, phase)
    migration = read_json(run / 'evidence/migration_provenance.json')
    if migration:
        role_rows = [{k: p[k] for k in migration['role_fields']} for p in pairs]
        if digest(role_rows) != migration['expected_role_sha256'][phase]:
            raise Blocked('Source/donor allocation changed after migration; no new selection is permitted')
    write_csv(run / 'results/candidate_counts.csv', candidates,
              ['source_id', 'modality', 'source_strength_eligible', 'a_candidates', 'b_candidates',
               'compatible_ab_pairs', 'independent_groups_feasible'])
    write_json(run / 'results/selection_stats.json', stats)
    # Store the label-only allocation separately; it is never a model-ready frozen manifest.
    write_jsonl(run / 'manifests/pairs.proposed.jsonl', pairs)
    inputs = materialize_pairs(pairs, rows, run, c) if pairs else {}
    write_jsonl(run / 'manifests/pairs.proposed.jsonl', pairs)
    write_json(run / 'manifests/inputs.json', inputs)
    prompt = (ROOT / 'configs/prompt.txt').read_text(encoding='utf-8')
    reasons = []
    if not pairs:
        reasons.append('No strictly eligible real-data triplets')
    try:
        identity = model_identity(c)
    except Blocked as e:
        identity = None
        reasons.append(str(e))
    if phase == 'main':
        smoke_dir = Path(smoke_dir or ROOT / 'runs/smoke-v1')
        validation = read_json(smoke_dir / 'results/smoke_validation.json', {})
        if not validation.get('passed') or identity is None or validation.get('signature') != validation_signature(c, identity, prompt):
            reasons.append('Matching real-model smoke validation has not passed')
    if reasons:
        write_jsonl(run / 'manifests/pairs.jsonl', [])
        state = dict(status='blocked_not_frozen', phase=phase, reasons=reasons,
                     proposed_triplets=len(pairs), actual_frozen_triplets=0,
                     configuration_candidate_sha256=digest(c), code_sha256=code_fingerprint(),
                     estimated_main_unique_inputs=len({p['source_id'] for p in pairs}) + 2 * len(pairs),
                     note='Candidate configuration checksum is not an executed-experiment freeze')
        write_json(run / 'manifests/freeze.json', state)
        raise Blocked('; '.join(reasons))
    for pair in pairs:
        validate_pair_hashes(pair, inputs)
    write_jsonl(run / 'manifests/pairs.jsonl', pairs)
    write_json(run / 'manifests/inputs.json', inputs)
    write_json(run / 'manifests/model_identity.json', identity)
    write_json(run / 'manifests/config.frozen.json', c)
    (run / 'manifests/prompt.frozen.txt').write_text(prompt, encoding='utf-8')
    write_json(run / 'manifests/selection.frozen.json', stats)
    from .audit import export_audit
    export_audit(run, c)
    snapshot = run / 'evidence/source_snapshot'
    for folder in ('src', 'tests'):
        (snapshot / folder).mkdir(parents=True, exist_ok=True)
        for path in (ROOT / folder).glob('*.py'):
            shutil.copyfile(path, snapshot / folder / path.name)
    (snapshot / 'configs').mkdir(parents=True, exist_ok=True)
    (snapshot / 'evidence').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'pilot.py', snapshot / 'pilot.py')
    (snapshot / 'configs/prompt.txt').write_text(prompt, encoding='utf-8')
    write_json(snapshot / 'configs/pilot.yaml', c)  # JSON is a valid YAML document.
    shutil.copyfile(ROOT / 'evidence/related_work.md', snapshot / 'evidence/related_work.md')
    files = ['manifests/pairs.jsonl', 'manifests/inputs.json', 'manifests/model_identity.json',
             'manifests/config.frozen.json', 'manifests/prompt.frozen.txt', 'manifests/inventory.csv',
             'manifests/selection.frozen.json', 'audit/export.json', 'audit/key.private.json',
             'audit/items.blinded.json', 'audit/review_items.csv', 'audit/preview.html', 'audit/README.md']
    files.extend(str(p.relative_to(run)).replace('\\', '/') for p in snapshot.rglob('*') if p.is_file())
    freeze = dict(status='frozen', phase=phase, time_utc=now(), config_sha256=digest(c),
                  code_sha256=code_fingerprint(), files={f: file_hash(run / f) for f in files},
                  triplets=len(pairs), unique_main_inputs=len(inputs),
                  smoke_validation_file=str(smoke_dir) if phase == 'main' else None,
                  revision_reason=c['revision_reason'])
    write_json(run / 'manifests/freeze.json', freeze)
    return freeze


def scoring_jobs(pairs, inputs, c):
    main = {h: dict(input_hash=h, channels=ch, kind='main', codes=c['scoring']['codes']) for h, ch in inputs.items()}
    jobs = list(main.values())
    if c['diagnostics']['single_modality']:
        singles = {}
        for p in pairs:
            m = p['modality']
            for condition in ('original', 'a', 'b'):
                ch = {m: inputs[p['input_hash_' + condition]][m]}
                h = input_hash(ch)
                singles[h] = dict(input_hash=h, channels=ch, kind='single', codes=c['scoring']['codes'])
        jobs.extend(singles[h] for h in sorted(singles))
    for job in list(main.values())[:c['diagnostics']['swapped_mapping_inputs']]:
        jobs.append(dict(**{k: v for k, v in job.items() if k not in ('kind', 'codes')},
                         kind='swapped', codes=c['diagnostics']['swapped_codes']))
    return jobs


def media_smoke(run, c):
    """Validate genuine inputs without claiming processor/logit/model validation."""
    run = Path(run)
    pairs = read_jsonl(run / 'manifests/pairs.proposed.jsonl') or read_jsonl(run / 'manifests/pairs.jsonl')
    inputs = read_json(run / 'manifests/inputs.json', {})
    checked, counts, chosen_sources = [], Counter(), {m: set() for m in MODALITIES}
    for p in pairs:
        m = p['modality']
        if p['donor_group'] != 1 or len(chosen_sources[m]) >= c['selection']['smoke_sources_per_modality']:
            continue
        if p['source_id'] in chosen_sources[m]:
            continue
        chosen_sources[m].add(p['source_id'])
        validate_pair_hashes(p, inputs)
        for cond in ('original', 'a', 'b'):
            channels = inputs[p['input_hash_' + cond]]
            verify_channels(channels)
            if len(channels['V']['frames']) != c['preprocessing']['frames']:
                raise Blocked('Media smoke: incorrect frame count')
            if not all(Path(frame).suffix == '.png' for frame in channels['V']['frames']):
                raise Blocked('Media smoke: video is not a silent frame list')
            if channels['A']['sr'] != c['preprocessing']['audio_sr']:
                raise Blocked('Media smoke: audio sampling rate mismatch')
        if p['source_target_score'] * p['donor_a_target_score'] <= 0 or p['source_target_score'] * p['donor_b_target_score'] >= 0:
            raise Blocked('Media smoke: label direction mismatch')
        if p['direction'] != (-1 if p['source_target_score'] > 0 else 1):
            raise Blocked('Media smoke: expected direction mismatch')
        counts[m] += 1
        checked.append(p['triplet_id'])
    result = dict(real_media=True, media_only_passed=all(counts[m] == c['selection']['smoke_sources_per_modality'] for m in MODALITIES),
                  checked_triplets=checked, sources_per_modality={m: counts[m] for m in MODALITIES},
                  candidate_scoring_validated=False, official_processor_tensors_validated=False,
                  real_model_smoke_passed=False,
                  limitation='This validates decoded channel hashes and labels only, not real Qwen candidate logits')
    write_json(run / 'results/media_smoke_validation.json', result)
    if not result['media_only_passed']:
        raise Blocked('Insufficient real media-only smoke cases')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_infer(run, c, phase='main', shared_budget_dir=None):
    run = Path(run)
    freeze = verify_frozen(run, c)
    if freeze['phase'] != phase:
        raise Blocked('Requested inference phase differs from frozen phase')
    env = doctor(run, c)
    if env['blockers']:
        raise Blocked('; '.join(env['blockers']))
    identity = model_identity(c)
    if identity != read_json(run / 'manifests/model_identity.json'):
        raise Blocked('Model weights or dependencies changed after freeze')
    inputs = read_json(run / 'manifests/inputs.json')
    pairs = read_jsonl(run / 'manifests/pairs.jsonl')
    prompt = (run / 'manifests/prompt.frozen.txt').read_text(encoding='utf-8')
    for pair in pairs:
        validate_pair_hashes(pair, inputs)
    jobs = scoring_jobs(pairs, inputs, c)
    # One ledger across smoke/main runs; retries do not reset the unique-input budget.
    budget_dir = Path(shared_budget_dir or ROOT / 'cache')
    budget_dir.mkdir(parents=True, exist_ok=True)
    budget_path = budget_dir / ('budget-' + digest(identity) + '.json')
    budget = read_json(budget_path, dict(attempted_keys=[]))
    attempted = set(budget['attempted_keys'])
    for job in jobs:
        scoring = dict(c['scoring'], codes=job['codes'])
        job['cache_key'] = cache_key(identity, job['input_hash'], prompt, c['preprocessing'], scoring)
    proposed_new = len({j['cache_key'] for j in jobs} - attempted)
    main_count = len(inputs)
    print(json.dumps(dict(phase=phase, main_unique_inputs=main_count, jobs_including_diagnostics=len(jobs),
                          previously_attempted=len(attempted), estimated_new=proposed_new,
                          budget=c['model']['budget_unique_inputs'],
                          estimated_sequence_forwards='one/input for next_token; three/input for sequence scoring, plus repeats'), indent=2))
    write_json(run / 'results/budget_estimate.json', dict(main_unique_inputs=main_count,
                 estimated_new_unique_inputs=proposed_new, jobs=len(jobs), cap=c['model']['budget_unique_inputs']))
    scorer = None
    cache_folder = run / 'cache/scores'
    predictions = {r['cache_key']: r for r in prediction_journal(run)}
    previous_usage = read_json(run / 'results/model_usage.json', dict(sequence_forwards=0, gpu_forward_seconds=0,
                               attempted_unique_inputs=0, peak_allocated_bytes=0, peak_reserved_bytes=0))
    prior_keys = set(read_json(run / 'results/attempted_keys.json', []))
    lock = budget_dir / ('lock-' + digest(identity))
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise Blocked('Another inference owns the budget lock; if crashed, verify no process remains then remove ' + str(lock))
    diagnostics = []
    try:
        budget = read_json(budget_path, dict(attempted_keys=[]))
        attempted = set(budget['attempted_keys'])
        scorer = QwenScorer(c, identity, prompt)
        write_json(run / 'evidence/model_loading_info.json', scorer.loading_info)
        for job_index, job in enumerate(jobs, 1):
            key = job['cache_key']
            path = cache_folder / (key + '.json')
            cached = read_json(path)
            if cached is not None:
                if cached.get('cache_key') != key or cached.get('input_hash') != job['input_hash']:
                    raise Blocked('Score cache provenance mismatch')
                if key not in predictions:
                    append_jsonl(run / 'results/predictions.jsonl', cached)
                    predictions[key] = cached
                continue
            if key not in attempted and len(attempted) >= c['model']['budget_unique_inputs']:
                failure(run, 'infer', 'unique_input_budget_exhausted', job['kind'], key)
                continue
            attempted.add(key)
            prior_keys.add(key)
            write_json(budget_path, dict(attempted_keys=sorted(attempted), cap=c['model']['budget_unique_inputs']))
            write_json(run / 'results/attempted_keys.json', sorted(prior_keys))
            try:
                result = scorer.score(job['channels'], job['codes'])
                row = dict(cache_key=key, input_hash=job['input_hash'], kind=job['kind'],
                           codes=job['codes'], model_fingerprint=identity['fingerprint'],
                           scoring_method=c['scoring']['method'], time_utc=now(), **result)
                write_json(path, row)  # Atomic cache commit precedes journal append.
                append_jsonl(run / 'results/predictions.jsonl', row)
                predictions[key] = row
            except Exception as e:
                failure(run, 'infer', type(e).__name__, str(e), key)
            finally:
                usage = dict(sequence_forwards=previous_usage['sequence_forwards'] + scorer.sequence_forwards,
                    gpu_forward_seconds=previous_usage['gpu_forward_seconds'] + scorer.gpu_seconds,
                    attempted_unique_inputs=len(prior_keys),
                    peak_allocated_bytes=max(previous_usage['peak_allocated_bytes'], scorer.torch.cuda.max_memory_allocated()),
                    peak_reserved_bytes=max(previous_usage['peak_reserved_bytes'], scorer.torch.cuda.max_memory_reserved()))
                write_json(run / 'results/model_usage.json', usage)
                write_json(run / 'results/inference_progress.json', dict(completed_or_attempted_jobs=job_index,
                    planned_jobs=len(jobs), cached_predictions=len(predictions), time_utc=now(), **usage))
                if job_index % 10 == 0 or job_index == len(jobs):
                    print(f'Inference progress {job_index}/{len(jobs)}; successful_cached={len(predictions)}; sequence_forwards={usage["sequence_forwards"]}', flush=True)
        main_predictions = {r['input_hash']: r for r in predictions.values() if r['kind'] == 'main'}
        isolation_pass = True
        for p in pairs:
            hashes = [p['input_hash_' + x] for x in ('original', 'a', 'b')]
            if not all(h in main_predictions for h in hashes):
                isolation_pass = False
                continue
            for m in p['unchanged_channel_hashes']:
                actual = [main_predictions[h]['processor_channel_hashes'].get(m) for h in hashes]
                if actual[0] is None or len(set(actual)) != 1:
                    isolation_pass = False
                    failure(run, 'isolation', 'processed_non_target_channel_changed', m, p['triplet_id'])
        repeat_ok = True
        for h in list(inputs)[:c['diagnostics']['repeated_inputs']]:
            if h not in main_predictions:
                repeat_ok = False
                continue
            original = main_predictions[h]
            repeated = scorer.score(inputs[h])
            difference = max(abs(a - b) for a, b in zip(original['probabilities'], repeated['probabilities']))
            repeat_ok &= difference <= c['diagnostics']['repeat_absolute_tolerance']
            diagnostics.append(dict(input_hash=h, max_probability_difference=difference,
                                    repeated_raw_scores=repeated['raw_scores']))
        usage = dict(sequence_forwards=previous_usage['sequence_forwards'] + scorer.sequence_forwards,
                     gpu_forward_seconds=previous_usage['gpu_forward_seconds'] + scorer.gpu_seconds,
                     attempted_unique_inputs=len(prior_keys),
                     peak_allocated_bytes=max(previous_usage['peak_allocated_bytes'], scorer.torch.cuda.max_memory_allocated()),
                     peak_reserved_bytes=max(previous_usage['peak_reserved_bytes'], scorer.torch.cuda.max_memory_reserved()))
        write_json(run / 'results/model_usage.json', usage)
        write_json(run / 'results/repeat_diagnostics.json', diagnostics)
        write_json(run / 'results/isolation_validation.json', dict(passed=isolation_pass, checked_triplets=len(pairs)))
        if phase == 'smoke':
            counts = Counter(p['modality'] for p in pairs)
            required = c['selection']['smoke_sources_per_modality'] * c['selection']['smoke_groups_per_source']
            passed = isolation_pass and repeat_ok and all(counts[m] >= required for m in MODALITIES)
            write_json(run / 'results/smoke_validation.json', dict(passed=passed, real_model=True,
                       completed_main_inputs=len(main_predictions), required_main_inputs=len(inputs),
                       token_validation=scorer.last_token_validation, repeated_scores_pass=bool(repeat_ok),
                       processed_channel_isolation_pass=isolation_pass,
                       signature=validation_signature(c, identity, prompt)))
            if not passed:
                raise Blocked('Real smoke criteria not met; main pilot remains gated')
    finally:
        if scorer is not None:
            write_json(run / 'results/model_usage.json', dict(
                sequence_forwards=previous_usage['sequence_forwards'] + scorer.sequence_forwards,
                gpu_forward_seconds=previous_usage['gpu_forward_seconds'] + scorer.gpu_seconds,
                attempted_unique_inputs=len(prior_keys),
                peak_allocated_bytes=max(previous_usage['peak_allocated_bytes'], scorer.torch.cuda.max_memory_allocated()),
                peak_reserved_bytes=max(previous_usage['peak_reserved_bytes'], scorer.torch.cuda.max_memory_reserved())))
        lock.unlink(missing_ok=True)
