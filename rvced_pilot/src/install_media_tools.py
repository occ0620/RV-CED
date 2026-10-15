"""Fetch portable FFmpeg only into this workspace; verify publisher SHA256."""
from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'tools'
target.mkdir(exist_ok=True)
url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
checksum = urllib.request.urlopen(url + '.sha256', timeout=60).read().decode().split()[0]
archive = target / 'ffmpeg-release-essentials.zip'
if not archive.exists():
    with urllib.request.urlopen(url, timeout=60) as src, archive.open('wb') as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
actual = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
if actual != checksum:
    raise RuntimeError('Publisher checksum mismatch; refusing extraction')
with zipfile.ZipFile(archive) as z:
    for member in z.infolist():
        dest = (target / member.filename).resolve()
        if not dest.is_relative_to(target.resolve()):
            raise RuntimeError('Unsafe archive path')
    z.extractall(target)
(root / 'evidence/media_tools_download.json').write_text(json.dumps(
    dict(url=url, publisher_checksum=checksum, actual_sha256=actual, extracted_into=str(target)), indent=2), encoding='utf-8')
print('Portable FFmpeg verified and extracted into', target)
