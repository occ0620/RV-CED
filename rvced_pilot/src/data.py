from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import random
import shutil
import numpy as np

from .common import (Blocked, INVENTORY_FIELDS, MODALITIES, ROOT, command, digest, failure,
                     file_hash, polarity, read_csv, read_json, write_csv, write_json)


def inspect_labels(path):
    path = Path(path)
    if not path.is_file():
        raise Blocked('Label file does not exist: ' + str(path))
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            examples = []
            n = 0
            for row in reader:
                n += 1
                if len(examples) < 3:
                    examples.append(row)
            return dict(path=str(path.resolve()), sha256=file_hash(path), rows=n,
                        fields=reader.fieldnames, examples=examples,
                        note='Inspection only; no field mapping inferred')
    raise Blocked('Only explicitly mapped UTF-8 CSV labels supported; export other formats using their documented schema')


def probe_media(path, config):
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        raise Blocked('ffmpeg/ffprobe unavailable')
    timeout = config['preprocessing']['decode_timeout_seconds']
    raw = command(['ffprobe', '-v', 'error', '-threads', '1', '-show_streams', '-show_format',
                   '-of', 'json', path], timeout)
    info = json.loads(raw)
    video = [s for s in info['streams'] if s['codec_type'] == 'video']
    audio = [s for s in info['streams'] if s['codec_type'] == 'audio']
    if len(video) != 1 or len(audio) != 1:
        raise Blocked(f'Expected one video and one audio stream, got {len(video)}/{len(audio)}')
    v, a = video[0], audio[0]
    duration = float(v.get('duration', info.get('format', {}).get('duration', 0)))
    aduration = float(a.get('duration', info.get('format', {}).get('duration', 0)))
    if duration <= 0 or aduration <= 0:
        raise Blocked('Missing/invalid natural stream duration')
    starts = [float(s.get('start_time', 0)) for s in (v, a)]
    if abs(starts[0] - starts[1]) > config['preprocessing']['max_stream_start_gap_seconds']:
        raise Blocked('Audio/video start times differ beyond prespecified tolerance')
    fps_parts = v.get('avg_frame_rate', '0/1').split('/')
    fps = float(fps_parts[0]) / float(fps_parts[1])
    progress = command(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', path, '-map', '0:v:0',
             '-map', '0:a:0', '-vsync', '0', '-progress', 'pipe:1', '-f', 'null', '-'], timeout).decode('utf-8', errors='replace')
    frame_counts = [int(line.split('=', 1)[1]) for line in progress.splitlines() if line.startswith('frame=')]
    frames = frame_counts[-1] if frame_counts else 0
    if frames < config['preprocessing']['frames']:
        raise Blocked('Too few distinct decoded video frames')
    if v.get('nb_frames', 'N/A') != 'N/A' and frames != int(v['nb_frames']):
        raise Blocked('Decoded frame count differs from container metadata')
    return dict(duration=duration, audio_duration=aduration, video_width=int(v['width']),
                video_height=int(v['height']), audio_sr=int(a['sample_rate']),
                video_frames=frames, video_fps=fps)


def cached_probe(original, d, media_root, c, run):
    columns = d['columns']
    value = d['media_path_template'].format_map(original) if d.get('media_path_template') else original[columns['media_path']]
    media = Path(value)
    media = (media_root / media).resolve() if not media.is_absolute() else media.resolve()
    try:
        if not media.is_file():
            raise Blocked('missing_media')
        sha = file_hash(media)
        key = digest(dict(media=sha, preprocessing=c['preprocessing'], ffprobe=shutil.which('ffprobe')))
        cache = Path(run) / 'cache/media_probe' / (key + '.json')
        result = read_json(cache)
        if result is None:
            result = dict(media_sha256=sha, **probe_media(media, c))
            write_json(cache, result)
        return result, None
    except Exception as e:
        return {}, str(e)


def load_inventory(run):
    rows = read_csv(Path(run) / 'manifests/inventory.csv')
    for r in rows:
        for k in ('y_t', 'y_a', 'y_v', 'y_multi', 'duration', 'audio_duration', 'video_fps'):
            r[k] = float(r[k]) if r.get(k) not in ('', None) else None
        for k in ('video_width', 'video_height', 'video_frames', 'audio_sr'):
            r[k] = int(r[k]) if r.get(k) not in ('', None) else None
        r['eligible'] = str(r['eligible']).lower() == 'true'
    return rows


def inventory(run, c):
    run = Path(run)
    if read_json(run / 'manifests/freeze.json', {}).get('status') == 'frozen':
        raise Blocked('Inventory is frozen; create a new run directory')
    d = c['dataset']
    if not d.get('labels_path'):
        write_csv(run / 'manifests/inventory.csv', [], INVENTORY_FIELDS)
        write_json(run / 'results/inventory_stats.json', dict(status='blocked_no_labels', observed_rows=0,
                   dataset_total=None, label_availability=None, candidate_counts=None,
                   reason='No dataset path supplied; zero observed rows is not a dataset count'))
        raise Blocked('CH-SIMS v2.0 supervised CSV and raw media not supplied')
    inspected = inspect_labels(d['labels_path'])
    write_json(run / 'evidence/label_schema.json', inspected)
    if not d.get('schema_evidence') or not d.get('label_scale'):
        raise Blocked('Read actual schema first; dataset.schema_evidence and label_scale are required')
    columns = d['columns']
    required = ['split', 'text', 'y_t', 'y_a', 'y_v', 'y_multi']
    for key in ('clip_id', 'media_path'):
        if not d.get(key + '_template'):
            required.append(key)
    for key in required:
        if columns.get(key) not in inspected['fields']:
            raise Blocked('Missing explicit documented column mapping for ' + key)
    for key, column in columns.items():
        if column and column not in inspected['fields']:
            raise Blocked('Mapped column absent from real file: ' + str(column))
    media_root = Path(d['media_root'] or Path(d['labels_path']).parent).resolve()
    out, seen = [], set()
    originals = read_csv(d['labels_path'])
    workers = ThreadPoolExecutor(max_workers=c.get('inventory_workers', 6))
    futures = [workers.submit(cached_probe, original, d, media_root, c, run) for original in originals]
    for row_number, original in enumerate(originals):
        row = {k: original.get(v, 'unknown') if v else 'unknown' for k, v in columns.items()}
        for key in ('clip_id', 'media_path'):
            if d.get(key + '_template'):
                row[key] = d[key + '_template'].format_map(original)
        if not row['clip_id'] or row['clip_id'] == 'unknown' or row['clip_id'] in seen:
            raise Blocked('Missing or duplicate clip ID; fix documented ID mapping before pairing')
        seen.add(row['clip_id'])
        raw_split = row['split']
        row['split'] = d['split_values'].get(raw_split, 'unknown')
        reasons = []
        if row['split'] == 'unknown':
            reasons.append('unmapped_split:' + raw_split)
        if not row['text'].strip() or row['text'] == 'unknown':
            reasons.append('missing_text')
        if '<|' in row['text'] or '<transcript_json>' in row['text'] or '</transcript_json>' in row['text']:
            reasons.append('reserved_control_delimiter_in_text')
        for key in ('y_t', 'y_a', 'y_v', 'y_multi'):
            try:
                y = float(row[key]) * d['label_multiplier'] + d['label_offset']
                if not (-1 <= y <= 1):
                    raise ValueError('Label outside documented normalized scale')
                row[key] = y
            except (TypeError, ValueError):
                reasons.append('missing_or_invalid_' + key)
                row[key] = None
        media = Path(row['media_path'])
        media = (media_root / media).resolve() if not media.is_absolute() else media.resolve()
        row['media_path'] = str(media)
        if row.get('subtitle_status') not in ('present', 'absent', 'unknown'):
            reasons.append('invalid_subtitle_metadata')
            row['subtitle_status'] = 'unknown'
        media_info, media_error = futures[row_number].result()
        row.update(media_info)
        if media_error:
            reasons.append('media_error:' + media_error)
        row['eligible'] = not reasons
        row['exclusion_reasons'] = reasons
        for reason in reasons:
            failure(run, 'inventory', reason.split(':')[0], reason, row['clip_id'])
        out.append(row)
        if (row_number + 1) % 100 == 0:
            print(f'Media inventory: {row_number + 1}/{len(originals)}, eligible={sum(r["eligible"] for r in out)}', flush=True)
            write_csv(run / 'manifests/inventory.partial.csv', out, INVENTORY_FIELDS)
    workers.shutdown()
    write_csv(run / 'manifests/inventory.csv', out, INVENTORY_FIELDS)
    n = len(out)
    distributions = {}
    for m in (*MODALITIES, 'multi'):
        key = 'y_' + m.lower()
        valid = [r[key] for r in out if r[key] is not None]
        distributions[m] = dict(available=len(valid), denominator=n,
                                available_fraction=len(valid) / n if n else None,
                                polarities=dict(Counter(polarity(y, c['selection']['tau']) for y in valid)))
    write_json(run / 'results/inventory_stats.json', dict(status='observed_real_files', observed_rows=n,
               eligible=sum(r['eligible'] for r in out), splits=dict(Counter(r['split'] for r in out)),
               label_availability=distributions, source_csv_sha256=inspected['sha256'],
               terms_status=d['terms_status'], schema_evidence=d['schema_evidence']))
    return out


def known(value):
    return value not in (None, '', 'unknown')


def metadata_status(source, donor, key):
    if not known(source.get(key)) or not known(donor.get(key)):
        return 'unknown'
    return 'same' if source[key] == donor[key] else 'different'


def candidate_lists(source, rows, m, c):
    cfg = c['selection']
    key = 'y_' + m.lower()
    y = source[key]
    if y is None or abs(y) + 1e-10 < cfg['min_source_strength']:
        return [], []
    a, b = [], []
    for donor in rows:
        if donor['clip_id'] == source['clip_id'] or donor[key] is None:
            continue
        if m == 'T':
            ratio = len(donor['text']) / len(source['text'])
            low, high = cfg['text_length_ratio']
        else:
            field = 'audio_duration' if m == 'A' else 'duration'
            ratio = donor[field] / source[field]
            low, high = cfg['duration_ratio']
        if not low - 1e-10 <= ratio <= high + 1e-10:
            continue
        if sign_product(y, donor[key]) > 0 and abs(donor[key] - y) <= cfg['label_tolerance'] + 1e-10:
            a.append(donor)
        if sign_product(y, donor[key]) < 0 and abs(abs(donor[key]) - abs(y)) <= cfg['label_tolerance'] + 1e-10:
            b.append(donor)
    return a, b


def sign_product(x, y):
    return x * y


def compatible(source, a, b, m, c):
    cfg = c['selection']
    key = 'y_' + m.lower()
    if abs(abs(a[key]) - abs(b[key])) > cfg['label_tolerance'] + 1e-10:
        return None
    if m == 'T':
        measure = lambda r: len(r['text'])
        max_ratio = cfg['ab_text_length_ratio']
    else:
        field = 'audio_duration' if m == 'A' else 'duration'
        measure = lambda r: r[field]
        max_ratio = cfg['ab_duration_ratio']
    if max(measure(a), measure(b)) / min(measure(a), measure(b)) > max_ratio + 1e-10:
        return None
    features, missing = {}, []
    for k in ('speaker_id', 'source_video_id', 'scene_id'):
        sa, sb = metadata_status(source, a, k), metadata_status(source, b, k)
        if sa != sb:
            return None
        features[k] = sa
        if sa == 'unknown':
            missing.append(k)
    penalty = 0
    for k in ('language', 'recording_environment'):
        sa, sb = metadata_status(source, a, k), metadata_status(source, b, k)
        features[k] = [sa, sb]
        if 'unknown' in (sa, sb):
            missing.append(k)
        penalty += sum(x != 'same' for x in (sa, sb))
    qualities = []
    if m in ('A', 'V'):
        for donor in (a, b):
            qualities.append(abs(donor['video_width'] / source['video_width'] - 1) +
                             abs(donor['video_height'] / source['video_height'] - 1))
    features.update(source_measure=measure(source), a_measure=measure(a), b_measure=measure(b),
                    ab_measure_ratio=max(measure(a), measure(b)) / min(measure(a), measure(b)),
                    priority=[penalty, sum(qualities)])
    return features, missing


def candidate_pairs(source, rows, m, c):
    a, b = candidate_lists(source, rows, m, c)
    pairs = []
    for aa in a:
        for bb in b:
            match = compatible(source, aa, bb, m, c)
            if match is not None:
                pairs.append((aa, bb, *match))
    return a, b, pairs


def candidate_matrix(source, rows, m, c):
    """Exact vectorization of compatible(); no condition is relaxed."""
    aa, bb = candidate_lists(source, rows, m, c)
    if not aa or not bb:
        return aa, bb, np.zeros((len(aa), len(bb)), dtype=bool)
    cfg = c['selection']
    key = 'y_' + m.lower()
    ya, yb = np.array([abs(a[key]) for a in aa]), np.array([abs(b[key]) for b in bb])
    mask = np.abs(ya[:, None] - yb[None, :]) <= cfg['label_tolerance'] + 1e-10
    if m == 'T':
        ma, mb = np.array([len(a['text']) for a in aa]), np.array([len(b['text']) for b in bb])
        limit = cfg['ab_text_length_ratio']
    else:
        field = 'audio_duration' if m == 'A' else 'duration'
        ma, mb = np.array([a[field] for a in aa]), np.array([b[field] for b in bb])
        limit = cfg['ab_duration_ratio']
    mask &= np.maximum(ma[:, None], mb[None, :]) / np.minimum(ma[:, None], mb[None, :]) <= limit + 1e-10
    for field in ('speaker_id', 'source_video_id', 'scene_id'):
        sa = np.array([metadata_status(source, a, field) for a in aa])
        sb = np.array([metadata_status(source, b, field) for b in bb])
        mask &= sa[:, None] == sb[None, :]
    return aa, bb, mask


def matrix_feasible(mask, groups):
    if not mask.any():
        return False
    if groups == 1:
        return True
    # A bipartite graph has a matching of size 2 iff its edges use >=2 vertices on both sides.
    return bool(np.count_nonzero(mask.any(axis=1)) >= 2 and np.count_nonzero(mask.any(axis=0)) >= 2)


def pick_matrix(source, aa, bb, mask, m, c, groups, reuse, forbidden, rng):
    mask = mask.copy()
    for i, donor in enumerate(aa):
        if donor['clip_id'] in forbidden or reuse[donor['clip_id']] >= c['selection']['donor_max_reuse']:
            mask[i, :] = False
    for j, donor in enumerate(bb):
        if donor['clip_id'] in forbidden or reuse[donor['clip_id']] >= c['selection']['donor_max_reuse']:
            mask[:, j] = False
    if not matrix_feasible(mask, groups):
        return []
    ia, ib = np.nonzero(mask)
    def priorities(donors):
        penalties, quality = [], []
        for donor in donors:
            penalties.append(sum(metadata_status(source, donor, f) != 'same' for f in ('language', 'recording_environment')))
            quality.append(abs(donor['video_width'] / source['video_width'] - 1) +
                           abs(donor['video_height'] / source['video_height'] - 1) if m in ('A', 'V') else 0)
        return np.array(penalties), np.array(quality)
    pa, qa = priorities(aa)
    pb, qb = priorities(bb)
    # Randomness is used only within equal documented matching priorities.
    ties = np.array([rng.random() for _ in range(len(ia))])
    order = np.lexsort((ties, qa[ia] + qb[ib], pa[ia] + pb[ib]))
    chosen = []
    for index in order:
        i, j = int(ia[index]), int(ib[index])
        if groups == 1:
            chosen = [(i, j)]
            break
        disjoint = order[(ia[order] != i) & (ib[order] != j)]
        if len(disjoint):
            second = int(disjoint[0])
            chosen = [(i, j), (int(ia[second]), int(ib[second]))]
            break
    return [(aa[i], bb[j], *compatible(source, aa[i], bb[j], m, c)) for i, j in chosen]


def independent_groups(options, n, reuse, forbidden, cap):
    available = [p for p in options if all(d['clip_id'] not in forbidden and reuse[d['clip_id']] < cap for d in p[:2])]
    if n == 1:
        return available[:1]
    # Exact search for the two disjoint edges required by the protocol, not a greedy feasibility test.
    for i, first in enumerate(available):
        ids = {first[0]['clip_id'], first[1]['clip_id']}
        for second in available[i + 1:]:
            if not ids & {second[0]['clip_id'], second[1]['clip_id']}:
                return [first, second]
    return []


def select_pairs(rows, c, phase):
    rows = sorted([r for r in rows if r['eligible'] and r['split'] == c['selection']['split']], key=lambda r: r['clip_id'])
    cfg = c['selection']
    target = cfg['smoke_sources_per_modality'] if phase == 'smoke' else cfg['sources_per_modality']
    groups = cfg['smoke_groups_per_source'] if phase == 'smoke' else cfg['groups_per_source']
    rng = random.Random(c['seed'])
    counts, feasible, candidate_stats = Counter(), {}, []
    # Full-label candidate feasibility is independent of all model outputs.
    for source in rows:
        feasible[source['clip_id']] = {}
        for m in MODALITIES:
            aa, bb, mask = candidate_matrix(source, rows, m, c)
            ok = matrix_feasible(mask, groups)
            if ok:
                feasible[source['clip_id']][m] = True
            candidate_stats.append(dict(source_id=source['clip_id'], modality=m,
                                        source_strength_eligible=source['y_' + m.lower()] is not None and abs(source['y_' + m.lower()]) + 1e-10 >= cfg['min_source_strength'],
                                        a_candidates=len(aa), b_candidates=len(bb), compatible_ab_pairs=int(mask.sum()),
                                        independent_groups_feasible=ok))
    ordered = rows[:]
    rng.shuffle(ordered)
    ordered.sort(key=lambda r: -len(feasible[r['clip_id']]))
    selected, source_ids, reuse = [], set(), Counter()
    for source in ordered:
        sid = source['clip_id']
        if reuse[sid]:
            continue
        for m in MODALITIES:
            if counts[m] >= target or not feasible[sid].get(m):
                continue
            aa, bb, mask = candidate_matrix(source, rows, m, c)
            picked = pick_matrix(source, aa, bb, mask, m, c, groups, reuse, source_ids | {sid}, rng)
            if len(picked) != groups:
                continue
            source_ids.add(sid)
            counts[m] += 1
            for k, (a, b, features, missing) in enumerate(picked, 1):
                for donor in (a, b):
                    reuse[donor['clip_id']] += 1
                y = source['y_' + m.lower()]
                selected.append(dict(triplet_id=digest([phase, sid, m, k])[:20], source_id=sid,
                     modality=m, donor_group=k, donor_a_id=a['clip_id'], donor_b_id=b['clip_id'],
                     source_target_score=y, donor_a_target_score=a['y_' + m.lower()],
                     donor_b_target_score=b['y_' + m.lower()], direction=-1 if y > 0 else 1,
                     match_features=features, match_missingness=missing, selection_seed=c['seed'],
                     split=source['split'], quality_flags=['relation_audit_pending', 'speech_is_full_content',
                        'subtitle_' + source['subtitle_status']] + ['unknown_' + x for x in missing]))
    return selected, candidate_stats, dict(source_counts={m: counts[m] for m in MODALITIES},
          donor_reuse=dict(reuse), donor_reuse_histogram=dict(Counter(reuse.values())),
          source_donor_overlap=sorted(source_ids & set(reuse)), target=target, groups=groups,
          common_sources=sorted(set.intersection(*[{p['source_id'] for p in selected if p['modality'] == m} for m in MODALITIES])),
          selection_priority='Prefer sources feasible in more modalities; metadata match, then seeded random tie order; global source/donor disjointness; no model scores',
          limitation='Deterministic strict allocation, not an optimal maximum-cardinality matching; feasibility counts precede global reuse/disjointness constraints')
