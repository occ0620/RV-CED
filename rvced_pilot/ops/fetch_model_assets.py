"""Fetch only small official configuration/tokenizer files at the pinned public commit."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1]
metadata = json.loads((root / 'evidence/official_model_metadata.json').read_text(encoding='utf-8'))
revision = metadata['sha']
dest = root / '.remote/staging/model-assets'
dest.mkdir(parents=True, exist_ok=True)
files = [s for s in metadata['siblings'] if not s['rfilename'].endswith('.safetensors') and s['rfilename'] not in ('.gitattributes', 'README.md')]


def download(entry):
    name = entry['rfilename']
    path = dest / name
    url = 'https://huggingface.co/Qwen/Qwen2.5-Omni-7B/resolve/' + revision + '/' + name
    data = urllib.request.urlopen(url, timeout=60).read()
    if entry.get('lfs'):
        assert hashlib.sha256(data).hexdigest() == entry['lfs']['sha256']
    else:
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['blobId']
    path.write_bytes(data)
    print('VERIFIED', name, len(data), flush=True)
    return name, hashlib.sha256(data).hexdigest()


with ThreadPoolExecutor(max_workers=3) as pool:
    hashes = dict(pool.map(download, files))
(dest / 'download_manifest.json').write_text(json.dumps(dict(repo=metadata['id'], revision=revision,
    source='Official Hugging Face immutable commit; weight shards will be linked only after matching official LFS SHA256',
    asset_sha256=hashes), indent=2), encoding='utf-8')
print('PINNED_REVISION', revision, flush=True)
