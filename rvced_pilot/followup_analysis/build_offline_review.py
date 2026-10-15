"""Package the existing blinded reviewer surface for private, offline transfer."""
from pathlib import Path
from collections import Counter
import json
import shutil
import subprocess
import zipfile
from common import HERE, FIELDS, ISSUES, read_json, file_hash, frozen_hashes, writejson, now
from serve_review import PAGE

SHARING=HERE/'sharing'
DEST=SHARING/'reviewer_02_offline_v1'
ARCHIVE=SHARING/'reviewer_02_offline_v1.zip'

def main():
    if ARCHIVE.exists():raise SystemExit('Package already exists; preserve it and use a new version')
    before=frozen_hashes()
    protocol_sha=file_hash(HERE/'evidence/review_protocol.fixed.json')
    original=read_json(HERE/'reviewer/items.json')
    media=read_json(HERE/'private/media_map.json')
    items=json.loads(json.dumps(original));needed={}
    DEST.mkdir(parents=True,exist_ok=True);(DEST/'media').mkdir(exist_ok=True)
    def materialize(url):
        opaque=url.removeprefix('/media/');entry=media[opaque]
        suffix='.wav' if entry['content_type']=='audio/wav' else '.png'
        relative='media/'+opaque+suffix
        needed[relative]=entry
        return relative
    for item in items:
        assert set(item)=={'item_id','stage','channels'}
        for m,desc in item['channels'].items():
            if m=='A':desc['audio']=materialize(desc['audio'])
            elif m=='V':desc['frames']=[materialize(url) for url in desc['frames']]
    for i,(relative,entry) in enumerate(sorted(needed.items()),1):
        source=Path(entry['path']);target=DEST/relative
        assert file_hash(source)==entry['sha256']
        if not target.exists():shutil.copyfile(source,target)
        assert file_hash(target)==entry['sha256']
        if i%100==0:print('OFFLINE_MEDIA',i,'/',len(needed),flush=True)
    # Only transport/rendering changes: identical item IDs/order, rating fields and criteria.
    page=PAGE.replace('__FIELDS__',json.dumps(FIELDS)).replace('__ISSUES__',json.dumps(ISSUES))
    page=page.replace('<option>reviewer_01</option><option>reviewer_02</option>','<option>reviewer_02</option>')
    page=page.replace('<a href="/reviewer_01.blank.csv">审核者1空表</a>','')
    page=page.replace('href="/guide.md"','href="字段说明.md"').replace('href="/reviewer_02.blank.csv"','href="reviewer_02.blank.csv"')
    page=page.replace('独立盲审</h1>','独立盲审：审核者2（离线）</h1>')
    page=page.replace("'rvced-blind-'+rid()","'rvced-blind-offline-v1-'+rid()")
    page=page.replace("function persist(){localStorage.setItem(storage(),JSON.stringify(answers))}",
        "function persist(){try{localStorage.setItem(storage(),JSON.stringify(answers))}catch(e){document.querySelector('#status').textContent+=' · 浏览器无法保存进度，请及时导出CSV'}}\nfunction loadSaved(){try{return JSON.parse(localStorage.getItem(storage())||'{}')}catch(e){return {}}}")
    page=page.replace("JSON.parse(localStorage.getItem(storage())||'{}');show()","loadSaved();show()")
    marker="fetch('/items.json').then(r=>r.json()).then(d=>{data=d;answers=JSON.parse(localStorage.getItem(storage())||'{}');show()});"
    # The prior replacement may already have changed the saved-progress expression.
    marker2="fetch('/items.json').then(r=>r.json()).then(d=>{data=d;answers=loadSaved();show()});"
    encoded=json.dumps(items,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e')
    replacement='data='+encoded+';answers=loadSaved();show();'
    assert marker in page or marker2 in page
    page=page.replace(marker,replacement).replace(marker2,replacement)
    begin=page.index("document.querySelector('#export').onclick=")
    end=page.index("document.querySelector('#reviewer').onchange=",begin)
    exporter=r'''document.querySelector('#export').onclick=()=>{
let rows=[fields.join(',')];
for(let x of data){let a=answers[x.item_id]||{};let row={...a,reviewer_id:'reviewer_02',item_id:x.item_id,stage:x.stage};
for(let k of ['clarity','speech_words_understood','subtitles_present','subtitles_readable'])if(x.stage==='combination')row[k]='na';
if(x.stage==='channel'&&!x.channels.A)row.speech_words_understood='na';
if(x.stage==='channel'&&!x.channels.V){row.subtitles_present='na';row.subtitles_readable='na'}
rows.push(fields.map(k=>csv(row[k])).join(','))}
let blob=new Blob(['\ufeff'+rows.join('\r\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=el('a');
link.href=url;link.download='reviewer_02.completed.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
'''
    page=page[:begin]+exporter+page[end:]
    assert "fetch(" not in page and '__FIELDS__' not in page and 'key.private' not in page
    (DEST/'打开盲审.html').write_text(page,encoding='utf-8')
    shutil.copyfile(HERE/'reviewer/guide.md',DEST/'字段说明.md')
    shutil.copyfile(HERE/'reviewer/reviewer_02.blank.csv',DEST/'reviewer_02.blank.csv')
    (DEST/'先读我.txt').write_text('''这是审核者2的离线盲审包，无需Python，无需打开原来的127.0.0.1链接。

1. 先把整个ZIP解压到一个普通文件夹，不要在压缩包内直接打开网页。
2. 双击“打开盲审.html”，使用Chrome或Edge浏览器打开。
3. 按“字段说明.md”先做第一步独立通道，再做第二步组合输入。
4. 每完成一部分，点击“导出当前评分CSV”另存备份。最后把reviewer_02.completed.csv发回给组织者。

不要移动或删除media文件夹，它保存页面需要的音频和画面。
评分编号固定为reviewer_02；共360个展示项目，不代表360个独立样本。
两位审核者独立评分，不查看对方评分、模型结果或标签。不确定填unknown，不编造判断。
浏览器临时进度可能因清理缓存、隐私模式或移动文件夹而丢失，以导出的CSV为备份。
包内包含实验媒体，只在约定的审核者之间私下传递，不公开上传。
''',encoding='utf-8')
    names=['打开盲审.html','字段说明.md','reviewer_02.blank.csv','先读我.txt']+sorted(needed)
    SHARING.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for name in names:z.write(DEST/name,'reviewer_02_offline_v1/'+name)
    # Verify the archive's complete contents; no analysis tables or private maps are allowed.
    with zipfile.ZipFile(ARCHIVE) as z:
        assert z.testzip() is None
        assert set(z.namelist())=={'reviewer_02_offline_v1/'+name for name in names}
    assert [(x['item_id'],x['stage']) for x in original]==[(x['item_id'],x['stage']) for x in items]
    assert file_hash(HERE/'evidence/review_protocol.fixed.json')==protocol_sha
    assert frozen_hashes()==before
    sha=file_hash(ARCHIVE);ARCHIVE.with_name(ARCHIVE.name+'.sha256').write_text(sha+'  '+ARCHIVE.name+'\n')
    writejson(SHARING/'offline_package_verification.json',dict(time_utc=now(),passed=True,items=len(items),
        stages=dict(Counter(x['stage'] for x in items)),media_files=len(needed),archive_bytes=ARCHIVE.stat().st_size,
        archive_sha256=sha,original_artifacts_unchanged=True,review_protocol_sha256=protocol_sha,
        rating_schema_unchanged=True,selection_unchanged=True,reviewer='reviewer_02',
        only_changes='Offline transport, relative media URLs, embedded blinded data, fixed reviewer ID, robust CSV serialization. No judgments, thresholds or item selection changed.',
        contains_predictions=False,contains_private_mapping=False,contains_dataset_labels=False))
    print('OFFLINE_PACKAGE_VERIFIED',str(ARCHIVE),ARCHIVE.stat().st_size,sha,flush=True)

if __name__=='__main__':main()
