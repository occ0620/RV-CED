"""Explicit user-run download; resolve main to an immutable official repository commit first."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

p = argparse.ArgumentParser()
p.add_argument('--destination', required=True)
p.add_argument('--revision', default='main')
args = p.parse_args()
repo = 'Qwen/Qwen2.5-Omni-7B'
revision = HfApi().model_info(repo, revision=args.revision).sha
destination = Path(args.destination)
if (destination / 'download_manifest.json').exists():
    old = json.loads((destination / 'download_manifest.json').read_text())
    if old['revision'] != revision:
        raise SystemExit('Different revision already downloaded here; select a fresh destination directory')
print('Official model:', repo, 'immutable revision:', revision, flush=True)
snapshot_download(repo_id=repo, revision=revision, local_dir=str(destination))
(destination / 'download_manifest.json').write_text(json.dumps(dict(repo=repo, revision=revision,
    downloaded_utc=datetime.now(timezone.utc).isoformat()), indent=2), encoding='utf-8')
