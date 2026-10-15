"""Check the shipped offline HTML and its real CSV exporter; no human scores are generated."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
from common import *

folder=HERE/'sharing/reviewer_02_offline_v1'
page=(folder/'打开盲审.html').read_text(encoding='utf-8')
start=page.index('data=[{')+len('data=');end=page.index(';answers=loadSaved();show();',start)
data=json.loads(page[start:end]);original=read_json(HERE/'reviewer/items.json')
assert [(x['item_id'],x['stage']) for x in data]==[(x['item_id'],x['stage']) for x in original]
assert len(data)==360 and 'fetch(' not in page
for x in data:
    for m,d in x['channels'].items():
        paths=[d['audio']] if m=='A' else d['frames'] if m=='V' else []
        for relative in paths:
            target=(folder/relative).resolve()
            assert target.is_relative_to(folder.resolve()) and target.is_file()
script=page.split('<script>')[1].split('</script>')[0]
checks=HERE/'sharing/checks';checks.mkdir(exist_ok=True)
(checks/'offline_ui.js').write_text(script,encoding='utf-8')
node=shutil.which('node');assert node,'Node required to verify JavaScript'
result=subprocess.run([node,'--check',str(checks/'offline_ui.js')],capture_output=True,text=True)
assert result.returncode==0,result.stderr
exporter=script[script.index("document.querySelector('#export').onclick="):script.index("document.querySelector('#reviewer').onchange=")]
test='import fs from "node:fs";\n'+f'const fields={json.dumps(FIELDS)},data={json.dumps(data,ensure_ascii=False)},answers={{}};\n'
test+='''let captured;const button={};const document={querySelector:()=>button};
function el(){return {click(){}}}function csv(v){return '"'+String(v??'').replaceAll('"','""')+'"'}
URL.createObjectURL=(blob)=>{captured=blob;return 'test-only'};URL.revokeObjectURL=()=>{};globalThis.setTimeout=(f)=>f();
'''+exporter+'''
button.onclick();const buffer=Buffer.from(await captured.arrayBuffer());
if(buffer[0]!==239||buffer[1]!==187||buffer[2]!==191)throw Error('UTF8 BOM missing');
fs.writeFileSync(process.argv[2],buffer);
'''
(checks/'export_test.mjs').write_text(test,encoding='utf-8')
csvpath=checks/'blank_export_test_NOT_HUMAN.csv'
result=subprocess.run([node,str(checks/'export_test.mjs'),str(csvpath)],capture_output=True,text=True)
assert result.returncode==0,result.stderr
with csvpath.open(encoding='utf-8-sig',newline='') as f:
    reader=csv.DictReader(f);rows=list(reader);assert reader.fieldnames==list(FIELDS)
assert len(rows)==360 and {r['reviewer_id'] for r in rows}=={'reviewer_02'}
assert {r['item_id'] for r in rows}=={x['item_id'] for x in original}
assert all(not r['polarity'] and not r['intensity'] and not r['confidence'] for r in rows)
verification=read_json(HERE/'sharing/offline_package_verification.json')
verification['offline_html_and_media_paths_passed']=True
verification['javascript_syntax_passed']=True
verification['actual_csv_export_utf8_bom_360_rows_passed']=True
verification['test_notice']='Only blank CSV exported in automated test, no human ratings or judgments generated'
writejson(HERE/'sharing/offline_package_verification.json',verification)
print('OFFLINE_HTML_AND_CSV_EXPORT_VERIFIED',len(rows))
