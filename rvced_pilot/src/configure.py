"""Create a new configuration from the verified local CSV schema; no inferred columns."""
import argparse
from pathlib import Path
import json
import yaml

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument('--base', default=str(root / 'configs/pilot.yaml'))
p.add_argument('--labels', required=True)
p.add_argument('--media-root', required=True)
p.add_argument('--model', required=True)
p.add_argument('--out', default=str(root / 'configs/gpu.yaml'))
args = p.parse_args()
out = Path(args.out).resolve()
if out.exists():
    raise SystemExit('Refusing to overwrite an existing configuration; use a new --out path')
c = yaml.safe_load(Path(args.base).read_text(encoding='utf-8'))
c['dataset']['labels_path'] = str(Path(args.labels).resolve())
c['dataset']['media_root'] = str(Path(args.media_root).resolve())
c['model']['local_path'] = str(Path(args.model).resolve())
manifest = Path(args.model) / 'download_manifest.json'
if manifest.exists():
    c['model']['revision'] = json.loads(manifest.read_text())['revision']
c['revision_reason'] = 'Relocated verified raw dataset and official local model onto GPU environment; before Qwen scoring'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding='utf-8')
print(out)
