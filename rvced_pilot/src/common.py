from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
# Prefer workspace-local portable tools, without modifying the user's system PATH.
for _binary in sorted((ROOT / 'tools').glob('*/bin/ffmpeg.exe')):
    os.environ['PATH'] = str(_binary.parent) + os.pathsep + os.environ.get('PATH', '')
    break
STATUS = '标签规则构造，关系审核待完成'
MODALITIES = ('T', 'A', 'V')
INVENTORY_FIELDS = ['clip_id', 'split', 'text', 'media_path', 'y_t', 'y_a', 'y_v', 'y_multi',
                    'duration', 'video_width', 'video_height', 'audio_sr', 'speaker_id',
                    'source_video_id', 'scene_id', 'subtitle_status', 'language',
                    'recording_environment', 'video_frames', 'video_fps', 'audio_duration',
                    'media_sha256', 'eligible', 'exclusion_reasons']
FAILURE_FIELDS = ['time_utc', 'stage', 'item_id', 'reason', 'details']


class Blocked(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def prediction_journal(run):
    """Recover only an interrupted final journal line; atomic score files remain authoritative."""
    run = Path(run)
    path = run / 'results/predictions.jsonl'
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines, rows, interrupted = raw.splitlines(), [], False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if index != len(lines) - 1:
                raise Blocked('Corrupt prediction journal before final line; inspect preserved cache manually')
            interrupted = True
    if interrupted or (raw and not raw.endswith(b'\n')):
        backup = run / 'evidence' / ('prediction_journal_original_' + hashlib.sha256(raw).hexdigest()[:16] + '.jsonl')
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(raw)
        write_jsonl(path, rows)
        failure(run, 'resume', 'interrupted_final_journal_line' if interrupted else 'missing_final_newline',
                'Original journal preserved in ' + str(backup))
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as f:
        for row in rows:
            f.write(canonical(row) + '\n')
    os.replace(tmp, path)


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as f:
        f.write(canonical(row) + '\n')
        f.flush()
        os.fsync(f.fileno())


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def failure(run, stage, reason, details='', item_id=''):
    path = Path(run) / 'results/failures.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open('a', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FAILURE_FIELDS)
        if new:
            w.writeheader()
        w.writerow(dict(time_utc=now(), stage=stage, item_id=item_id, reason=reason, details=details))


def load_config(path):
    import yaml
    c = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if c['preprocessing']['use_audio_in_video'] is not False:
        raise Blocked('use_audio_in_video must be false')
    if c['scoring']['method'] not in ('next_token', 'sequence_log_likelihood'):
        raise Blocked('Unsupported scoring definition')
    if c['scoring']['sequence_length_normalization'] != 'none':
        raise Blocked('Only summed candidate sequence log likelihood is implemented')
    if c['model']['local_files_only'] is not True:
        raise Blocked('Inference requires explicit local weight files')
    if c['scoring']['classes'] != [-1, 0, 1] or len(set(c['scoring']['codes'])) != 3:
        raise Blocked('Invalid candidate mapping')
    return c


def code_fingerprint():
    return digest({str(p.relative_to(ROOT)): file_hash(p) for p in sorted((ROOT / 'src').glob('*.py'))})


def dependencies():
    names = ['torch', 'transformers', 'qwen-omni-utils', 'numpy', 'pandas', 'Pillow',
             'PyYAML', 'pytest', 'matplotlib', 'accelerate', 'torchvision', 'librosa', 'soundfile',
             'tokenizers', 'huggingface-hub', 'safetensors', 'Jinja2', 'av', 'scipy', 'einops', 'soxr']
    out = {}
    for name in names:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def command(args, timeout=180):
    result = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    if result.returncode:
        raise Blocked(result.stderr.decode('utf-8', errors='replace')[-3000:])
    return result.stdout


def environment():
    env = dict(time_utc=now(), platform=platform.platform(), python=sys.version,
               executable=sys.executable, dependencies=dependencies(), cpu_count=os.cpu_count(),
               ffmpeg=shutil.which('ffmpeg'), ffprobe=shutil.which('ffprobe'),
               nvidia_smi=shutil.which('nvidia-smi'))
    try:
        import torch
        env.update(torch_cuda_build=torch.version.cuda, cuda_available=torch.cuda.is_available(),
                   gpus=[dict(name=torch.cuda.get_device_name(i),
                              vram_bytes=torch.cuda.get_device_properties(i).total_memory)
                         for i in range(torch.cuda.device_count())])
    except Exception as e:
        env.update(cuda_available=False, gpu_probe_error=str(e), gpus=[])
    if env['nvidia_smi']:
        try:
            env['nvidia_smi_output'] = command(['nvidia-smi'], 15).decode(errors='replace')
        except Exception as e:
            env['nvidia_smi_error'] = str(e)
    env['disk'] = dict(zip(('total', 'used', 'free'), shutil.disk_usage(ROOT)))
    for exe in ('ffmpeg', 'ffprobe'):
        if env[exe]:
            try:
                env[exe + '_version'] = command([env[exe], '-version'], 15).decode(errors='replace').splitlines()[0]
            except Exception as e:
                env[exe + '_version_error'] = str(e)
    return env


def polarity(y, tau=0.2):
    return 1 if y > tau + 1e-10 else -1 if y < -tau - 1e-10 else 0


def sign(y):
    return 1 if y > 0 else -1 if y < 0 else 0


def normalize_scores(raw):
    if len(raw) != 3 or not all(math.isfinite(x) for x in raw):
        raise ValueError('Non-finite or missing candidate scores')
    exps = [math.exp(x - max(raw)) for x in raw]
    p = [x / sum(exps) for x in exps]
    return dict(raw_scores=raw, probabilities=p, s=p[2] - p[0], prediction=(-1, 0, 1)[p.index(max(p))])


def cache_key(model_identity, input_hash, prompt, preprocessing, scoring):
    return digest(dict(model=model_identity, input=input_hash, prompt=prompt,
                       preprocessing=preprocessing, scoring=scoring))


def ensure_layout(run):
    run = Path(run)
    for folder in ('manifests', 'results', 'audit', 'figures', 'evidence', 'cache/inputs', 'cache/scores'):
        (run / folder).mkdir(parents=True, exist_ok=True)
    for name in ('predictions.jsonl',):
        (run / 'results' / name).touch(exist_ok=True)
    if not (run / 'results/failures.csv').exists():
        write_csv(run / 'results/failures.csv', [], FAILURE_FIELDS)


def record_resource(run, stage, start_wall, start_cpu, status, extra=None):
    path = Path(run) / 'results/resource_usage.json'
    r = read_json(path, dict(events=[], model_unique_inputs=0, sequence_forwards=0,
                             gpu_forward_seconds=0.0, peak_gpu_allocated_bytes=0,
                             peak_gpu_reserved_bytes=0, gpu_energy_kwh=None,
                             energy_status='not measured', monetary_cost=None))
    r['events'].append(dict(stage=stage, time_utc=now(), status=status,
                            wall_seconds=time.perf_counter() - start_wall,
                            cpu_seconds=time.process_time() - start_cpu, **(extra or {})))
    model = read_json(Path(run) / 'results/model_usage.json', {})
    if model:
        r.update(model_unique_inputs=model['attempted_unique_inputs'], sequence_forwards=model['sequence_forwards'],
                 gpu_forward_seconds=model['gpu_forward_seconds'],
                 peak_gpu_allocated_bytes=model['peak_allocated_bytes'],
                 peak_gpu_reserved_bytes=model['peak_reserved_bytes'])
    write_json(path, r)


def verify_frozen(run, config=None):
    run = Path(run)
    freeze = read_json(run / 'manifests/freeze.json', {})
    if freeze.get('status') != 'frozen':
        raise Blocked('No real-data frozen manifest; blocked/empty files are not a freeze')
    for rel, expected in freeze['files'].items():
        if file_hash(run / rel) != expected:
            raise Blocked('Frozen file modified: ' + rel)
    if freeze['code_sha256'] != code_fingerprint():
        raise Blocked('Code changed after freeze; use a new run directory with a revision reason')
    if config is not None and digest(config) != freeze['config_sha256']:
        raise Blocked('Configuration changed after freeze; use a new run directory')
    return freeze
