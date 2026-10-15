from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import wave

import numpy as np
from PIL import Image

from .common import Blocked, command, digest, failure, file_hash, read_json, write_json


def tensor_hash(value):
    if hasattr(value, 'detach'):
        value = value.detach().cpu().contiguous()
        # bfloat16 cannot be converted directly to NumPy.
        value = value.float().numpy() if str(value.dtype) == 'torch.bfloat16' else value.numpy()
    a = np.ascontiguousarray(value)
    return digest(dict(shape=list(a.shape), dtype=str(a.dtype), sha256=hashlib.sha256(a.tobytes()).hexdigest()))


def verify_channels(channels):
    for m, desc in channels.items():
        if m == 'T':
            if desc['hash'] != digest(desc['text']):
                raise Blocked('Text hash mismatch')
        elif m == 'A':
            if file_hash(desc['path']) != desc['file_sha256']:
                raise Blocked('Audio cache file changed')
            a = np.load(desc['path'], allow_pickle=False)
            if tensor_hash(a) != desc['array_hash']:
                raise Blocked('Decoded audio hash mismatch')
        else:
            for path, sha in zip(desc['frames'], desc['frame_sha256']):
                if file_hash(path) != sha:
                    raise Blocked('Frame cache changed')


def prepare_clip(row, run, c):
    folder = Path(run).resolve() / 'cache/inputs' / digest([row['clip_id'], row['media_sha256'], c['preprocessing']])
    folder.mkdir(parents=True, exist_ok=True)
    meta = folder / 'channels.json'
    if meta.exists():
        channels = read_json(meta)
        verify_channels(channels)
        return channels
    if file_hash(row['media_path']) != row['media_sha256']:
        raise Blocked('Raw media changed after inventory')
    p = c['preprocessing']
    sr, n = p['audio_sr'], p['frames']
    timeout = p['decode_timeout_seconds']
    raw = command(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-i', row['media_path'],
                   '-map', '0:a:0', '-vn', '-ac', '1', '-ar', sr, '-f', 'f32le', 'pipe:1'], timeout)
    a = np.frombuffer(raw, dtype='<f4').copy()
    if not len(a) or not np.isfinite(a).all():
        raise Blocked('Invalid decoded audio')
    if abs(len(a) / sr - row['audio_duration']) > 0.15:
        raise Blocked('Decoded audio duration differs from inventory; no forced alignment')
    audio = folder / 'audio.npy'
    np.save(audio, a, allow_pickle=False)
    # WAV is for human preview only. The model receives the float32 waveform above.
    with wave.open(str(folder / 'audio.wav'), 'wb') as w:
        w.setparams((1, 2, sr, len(a), 'NONE', 'not compressed'))
        w.writeframes((np.clip(a, -1, 1) * 32767).astype('<i2').tobytes())
    indices = np.linspace(0, row['video_frames'] - 1, n).round().astype(int).tolist()
    if len(set(indices)) != n:
        raise Blocked('Uniform sampling did not yield eight distinct frame indices')
    selector = '+'.join(f'eq(n\\,{i})' for i in indices)
    command(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-xerror', '-threads', '2', '-i', row['media_path'],
             '-map', '0:v:0', '-an', '-vf', 'select=' + selector, '-vsync', '0',
             '-frames:v', n, '-threads', '1', str(folder / 'frame_%02d.png')], timeout)
    frames = sorted(folder.glob('frame_*.png'))
    if len(frames) != n:
        raise Blocked('Unexpected effective frame count')
    for path in frames:
        with Image.open(path) as image:
            if image.size != (row['video_width'], row['video_height']):
                raise Blocked('Unexpected image geometry; rotation requires documented preprocessing revision')
    shas = [file_hash(path) for path in frames]
    fps = n / row['duration']
    channels = dict(T=dict(text=row['text'], hash=digest(row['text'])),
                    A=dict(path=str(audio), wav_path=str(folder / 'audio.wav'), sr=sr,
                           samples=len(a), array_hash=tensor_hash(a), file_sha256=file_hash(audio),
                           hash=digest(dict(array=tensor_hash(a), sr=sr))),
                    V=dict(frames=[str(f) for f in frames], frame_sha256=shas, indices=indices,
                           sample_fps=fps, duration=row['duration'],
                           hash=digest(dict(frames=shas, sample_fps=fps, indices=indices))))
    write_json(meta, channels)
    return channels


def input_hash(channels):
    return digest({m: value['hash'] for m, value in channels.items()})


def replace_channel(source, donor, modality):
    channels = {**source, modality: donor[modality]}
    for m in source:
        if m != modality and channels[m]['hash'] != source[m]['hash']:
            raise Blocked('Non-target channel changed')
    return channels


def validate_pair_hashes(pair, inputs):
    hashes = [pair['input_hash_' + x] for x in ('original', 'a', 'b')]
    original, a, b = [inputs[h] for h in hashes]
    for m, expected in pair['unchanged_channel_hashes'].items():
        if not all(x[m]['hash'] == expected for x in (original, a, b)):
            raise Blocked('Non-target channel isolation failed: ' + m)
    for h, channels in zip(hashes, (original, a, b)):
        if h != input_hash(channels):
            raise Blocked('Input content hash failed')


def materialize_pairs(pairs, rows, run, c):
    lookup = {r['clip_id']: r for r in rows}
    clips, inputs = {}, {}
    ids = sorted({p[k] for p in pairs for k in ('source_id', 'donor_a_id', 'donor_b_id')})
    errors = []
    with ThreadPoolExecutor(max_workers=c.get('media_workers', 4)) as pool:
        jobs = {pool.submit(prepare_clip, lookup[sid], run, c): sid for sid in ids}
        for index, future in enumerate(as_completed(jobs), 1):
            sid = jobs[future]
            try:
                clips[sid] = future.result()
            except Exception as e:
                errors.append(sid)
                failure(run, 'preprocess', 'clip_preparation_failed', str(e), sid)
            if index % 25 == 0 or index == len(ids):
                print(f'Prepare real clips: {index}/{len(ids)}; failures={len(errors)}', flush=True)
    if errors:
        raise Blocked(f'Preparation failed for {len(errors)} preselected clips; selection is preserved, no donor replacement performed')
    for pair in pairs:
        source = clips[pair['source_id']]
        aa = replace_channel(source, clips[pair['donor_a_id']], pair['modality'])
        bb = replace_channel(source, clips[pair['donor_b_id']], pair['modality'])
        for condition, channels in (('original', source), ('a', aa), ('b', bb)):
            h = input_hash(channels)
            inputs[h] = channels
            pair['input_hash_' + condition] = h
        pair['unchanged_channel_hashes'] = {m: v['hash'] for m, v in source.items() if m != pair['modality']}
        validate_pair_hashes(pair, inputs)
    return inputs
