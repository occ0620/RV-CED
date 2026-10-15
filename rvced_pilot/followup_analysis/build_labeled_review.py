"""Create a display-only v2 offline package plus a small patch for existing v1 users."""
from pathlib import Path
import json
import zipfile
from common import *
from review_ui_labels import OPTION_LABELS,FIELD_HELP,labeled_page,revised_guide

def main():
    sharing=HERE/'sharing';old=sharing/'reviewer_02_offline_v1'
    new=sharing/'reviewer_02_offline_v2';new.mkdir(parents=True,exist_ok=True)
    before=frozen_hashes();protocol=read_json(HERE/'evidence/review_protocol.fixed.json')
    for name,sha in protocol['code_files_sha256'].items():assert file_hash(HERE/name)==sha
    old_page=(old/'打开盲审.html').read_text(encoding='utf-8')
    page=labeled_page(old_page,'字段说明_中文选项.md')
    guide=revised_guide((HERE/'reviewer/guide.md').read_text(encoding='utf-8'))
    (new/'打开盲审.html').write_text(page,encoding='utf-8')
    (new/'字段说明_中文选项.md').write_text(guide,encoding='utf-8')
    (HERE/'reviewer/guide_labeled_v2.md').write_text(guide,encoding='utf-8')
    instructions='''审核者2：中文选项显示版v2。评分编码、项目编号和原评分标准保持不变。

首次使用：完整解压reviewer_02_offline_v2.zip，双击“打开盲审.html”。
已经填过v1：先在旧页面导出CSV备份；新页面选择“恢复旧评分CSV”导入。不要丢弃旧CSV或旧包。
导入不会改编码；发现与当前非空评分冲突会整体拒绝导入，不覆盖任何评分。

只下载小补丁时：把reviewer_02_options_patch_v2.zip解压到v1包的上一级目录；
它会在原reviewer_02_offline_v1目录新增“打开盲审_中文选项.html”，保留原HTML和media目录。
随后打开新增加的中文选项HTML，按需导入旧CSV。没有原v1媒体时应下载完整v2包。

按“字段说明_中文选项.md”先评通道，再评组合。数字只是存储编码，下拉框现同时显示中文含义。
继续及时导出reviewer_02.completed.csv备份，最终私下交给组织者。两人仍需独立评分。
'''
    (new/'先读我.txt').write_text(instructions,encoding='utf-8')
    metadata=dict(version='display-v2',time_utc=now(),classification='display labels and explanatory anchors; no recoding',
        missing_or_ambiguous_before_revision=[
            'Dropdowns omitted existing Chinese grade names',
            'Intensity1/2 adjacent ordering was not explicit; retained names, clarified ordinal0..4',
            'Low/medium/high confidence lacked brief behavioral descriptions',
            'Blank, unknown and na were not explained beside the choices'],
        resolution='Chinese option labels plus unified explanatory guide; no new thresholds, categories or quality gates',
        option_labels=OPTION_LABELS,field_help=FIELD_HELP,stored_codes_unchanged=True,existing_scores_automatically_changed=False,
        same_item_ids=True,legacy_browser_storage_keys_preserved=True,csv_import_conflicts='Reject entire import; preserve current answers',
        original_protocol_sha256=file_hash(HERE/'evidence/review_protocol.fixed.json'),
        original_render_code_sha256=file_hash(HERE/'serve_review.py'),
        overlay_code_sha256=file_hash(HERE/'review_ui_labels.py'))
    writejson(HERE/'evidence/review_display_v2.json',metadata)
    small=sharing/'reviewer_02_options_patch_v2.zip'
    full=sharing/'reviewer_02_offline_v2.zip'
    for path in (small,full):
        if path.exists():raise SystemExit('Version already packaged; preserve it and use a new version')
    with zipfile.ZipFile(small,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('reviewer_02_offline_v1/打开盲审_中文选项.html',page)
        z.writestr('reviewer_02_offline_v1/字段说明_中文选项.md',guide)
        z.writestr('reviewer_02_offline_v1/中文选项升级说明.txt',instructions)
    files=[(new/'打开盲审.html','打开盲审.html'),(new/'字段说明_中文选项.md','字段说明_中文选项.md'),(new/'先读我.txt','先读我.txt'),(old/'reviewer_02.blank.csv','reviewer_02.blank.csv')]
    media=sorted((old/'media').iterdir());assert len(media)==657
    files.extend((p,'media/'+p.name) for p in media)
    with zipfile.ZipFile(full,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        for source,name in files:z.write(source,'reviewer_02_offline_v2/'+name)
    artifacts=[]
    for path in (small,full):
        with zipfile.ZipFile(path) as z:assert z.testzip() is None
        checksum=file_hash(path);path.with_name(path.name+'.sha256').write_text(checksum+'  '+path.name+'\n')
        artifacts.append(dict(name=path.name,bytes=path.stat().st_size,sha256=checksum))
    assert frozen_hashes()==before
    writejson(sharing/'labeled_packages_v2.json',dict(passed=True,artifacts=artifacts,media_files=657,review_items=360,
        original_files_unchanged=True,stored_codes_unchanged=True,protocol_sha256=metadata['original_protocol_sha256']))
    print('LABELED_V2_PACKAGES',json.dumps(artifacts),flush=True)

if __name__=='__main__':main()
