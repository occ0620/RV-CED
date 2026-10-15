"""Read-only access to the frozen pilot; all new output stays in this directory."""
from pathlib import Path
import csv
import hashlib
import json
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RUN = PROJECT / 'runs/main-a800-v3'
sys.path.insert(0, str(PROJECT))
from src.common import digest, file_hash, read_json, read_jsonl, cache_key, now
from src.data import load_inventory, compatible

MODS = ('T','A','V')
ISSUES = ('speech_content_conflict','subtitle_leakage','identity_discontinuity',
          'scene_discontinuity','timing_discontinuity','processing_error')
EXTRA = ('clarity','speech_words_understood','subtitles_present','subtitles_readable')
FIELDS = ('reviewer_id','item_id','stage','polarity','intensity','confidence',*EXTRA,*ISSUES,'notes')

def readcsv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def writejson(path, data):
    path = Path(path)
    assert path.resolve().is_relative_to(HERE.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def writecsv(path, rows, fields=None):
    path = Path(path)
    assert path.resolve().is_relative_to(HERE.resolve())
    path.parent.mkdir(parents=True,exist_ok=True)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def local_media(path):
    prefix='/root/autodl-tmp/RV-CED/rvced_pilot/'
    assert path.startswith(prefix)
    result=PROJECT/path[len(prefix):]
    assert result.resolve().is_relative_to((RUN/'cache/inputs').resolve())
    return result

def polarity(y,tau=.2):
    return 1 if y>tau else -1 if y < -tau else 0

def sign(x,tol):
    return 1 if x>tol else -1 if x < -tol else 0

def load():
    return (read_json(RUN/'manifests/config.frozen.json'),read_jsonl(RUN/'manifests/pairs.jsonl'),
            read_json(RUN/'manifests/inputs.json'),read_jsonl(RUN/'results/predictions.jsonl'),
            {r['clip_id']:r for r in load_inventory(RUN)})

def frozen_hashes():
    freeze=read_json(RUN/'manifests/freeze.json')
    files=list(freeze['files'])+['manifests/freeze.json','results/predictions.jsonl','results/summary.csv',
          'results/isolation_validation.json','results/repeat_diagnostics.json','results/failures.csv']
    return {f:file_hash(RUN/f) for f in files}

def ci(values):
    return [float(v) for v in np.quantile(values,[.025,.975])]
