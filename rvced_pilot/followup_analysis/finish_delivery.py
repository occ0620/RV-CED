"""Freeze this cache-analysis delivery; original experiment and human forms are untouched."""
import importlib.metadata
import json
import platform
import zipfile
from urllib.request import urlopen
from common import *

original=read_json(HERE/'evidence/integrity.json')['original_file_hashes']
assert frozen_hashes()==original
assert read_json(HERE/'evidence/delivery_verification.json')['passed']
with urlopen('http://127.0.0.1:8765/items.json',timeout=10) as response:
    assert len(json.load(response))==360
writejson(HERE/'evidence/followup_environment.json',dict(time_utc=now(),python=platform.python_version(),
    packages={k:importlib.metadata.version(k) for k in ('numpy','matplotlib','Pillow')},model_inference=False,
    torch_loaded='torch' in sys.modules,source_artifacts_unchanged=True,active_blind_review_url='http://127.0.0.1:8765/',
    human_review_status='关系审核待完成'))
files=[p for p in sorted(HERE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and
       p.name not in ('DELIVERY_MANIFEST.json','followup_delivery.zip','followup_delivery.zip.sha256')]
manifest={p.relative_to(HERE).as_posix():file_hash(p) for p in files}
writejson(HERE/'DELIVERY_MANIFEST.json',dict(time_utc=now(),files_sha256=manifest,original_run_sha256=original,
    status='cache_analysis_completed_human_relation_audit_pending',contains_private_analysis=True,
    reviewer_entry='Use only dedicated loopback HTTP service; do not share the full package with reviewers'))
archive=HERE/'followup_delivery.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files+[HERE/'DELIVERY_MANIFEST.json']:z.write(p,'followup_analysis/'+p.relative_to(HERE).as_posix())
assert zipfile.ZipFile(archive).testzip() is None
archive.with_name(archive.name+'.sha256').write_text(file_hash(archive)+'  '+archive.name+'\n')
print('FOLLOWUP_DELIVERY_SAVED',len(files),'files;',archive.stat().st_size,'bytes; originals unchanged')
