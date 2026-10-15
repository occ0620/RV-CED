"""Exercise dropdown values, unchanged saved scores, CSV roundtrip and import conflict rejection."""
import json
import shutil
import subprocess
import zipfile
from http.server import ThreadingHTTPServer
import threading
from urllib.request import urlopen
from urllib.error import HTTPError
from common import *
from review_ui_labels import OPTION_LABELS,labeled_page
import serve_review_labeled

def main():
    sharing=HERE/'sharing';page=(sharing/'reviewer_02_offline_v2/打开盲审.html').read_text(encoding='utf-8')
    start=page.index('data=[{')+len('data=');end=page.index(';answers=loadSaved();show();',start)
    items=__import__('json').loads(page[start:end]);original=read_json(HERE/'reviewer/items.json')
    assert [(x['item_id'],x['stage']) for x in items]==[(x['item_id'],x['stage']) for x in original]
    assert "'rvced-blind-offline-v1-'+rid()" in page and 'o.value=v' in page
    checks=HERE/'checks/display_v2';checks.mkdir(parents=True,exist_ok=True)
    script=page.split('<script>')[1].split('</script>')[0];(checks/'offline_ui.js').write_text(script,encoding='utf-8')
    node=shutil.which('node');assert node
    result=subprocess.run([node,'--check',str(checks/'offline_ui.js')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    channel=next(x for x in items if x['stage']=='channel');combo=next(x for x in items if x['stage']=='combination')
    fixture={channel['item_id']:dict(polarity='-1',intensity='2',confidence='1',clarity='2',notes='仅用于内存兼容性测试，含逗号,和引号"\n换行'),
             combo['item_id']:dict(processing_error='0',speech_content_conflict='1',subtitle_leakage='unknown',identity_discontinuity='unknown',scene_discontinuity='0',timing_discontinuity='unknown')}
    # These are compatibility fixtures only; never save them as human rating files.
    test=r'''import vm from 'node:vm';
class Element {constructor(tag){this.tag=tag;this.children=[];this.value='';this.textContent=''}append(...args){this.children.push(...args)}replaceChildren(){this.children=[]}click(){}}
const elements=new Map();function element(id){if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id)}element('#reviewer').value='reviewer_02';
const fixture=__FIXTURE__,memory=new Map([['rvced-blind-offline-v1-reviewer_02',JSON.stringify(fixture)]]);
let captured;const context={console,Blob,document:{createElement:t=>new Element(t),querySelector:element},
localStorage:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)},
URL:{createObjectURL:b=>{captured=b;return 'test-only'},revokeObjectURL:()=>{}},setTimeout:f=>f(),confirm:()=>true};
vm.createContext(context);vm.runInContext(__SCRIPT__,context);
const run=s=>vm.runInContext(s,context),before=run('JSON.stringify(answers)');
if(before!==JSON.stringify(fixture))throw Error('Existing browser ratings changed during rendering');
const labels=__LABELS__;
for(let[key,values]of Object.entries(labels)){
  for(let[code,meaning]of Object.entries(values))if(run(`optionText(${JSON.stringify(key)},${JSON.stringify(code)})`)!==code+'＝'+meaning)throw Error('Wrong option text');
  context.testBox=new Element('div');context.testOpts=['',...Object.keys(values)];
  run(`choices(testBox,{item_id:'schema-only'},${JSON.stringify(key)},testOpts.slice(1))`);
  let select=context.testBox.children[0].children.find(x=>x.tag==='select');
  if(JSON.stringify(select.children.map(x=>x.value))!==JSON.stringify(context.testOpts))throw Error('Option.value recoded');
}
if(run('JSON.stringify(answers)')!==before)throw Error('Dropdown display modified scores');
element('#export').onclick();const bytes=new Uint8Array(await captured.arrayBuffer());if(bytes[0]!==239||bytes[1]!==187||bytes[2]!==191)throw Error('CSV BOM missing');
context.csvText=new TextDecoder('utf-8').decode(bytes);const rows=run('parseCsv(csvText)');if(rows.length!==361)throw Error('CSV row count changed');
let header=rows[0],record=rows.find(r=>r[header.indexOf('item_id')]===Object.keys(fixture)[0]);
for(let[k,v]of Object.entries(fixture[Object.keys(fixture)[0]]))if(record[header.indexOf(k)]!==v)throw Error('CSV changed existing score or note');
run('validatedImport(csvText)');if(run('JSON.stringify(answers)')!==before)throw Error('Import parser mutated current answers');
let conflictRows=rows.map(r=>r.slice()),conflict=conflictRows.find(r=>r[header.indexOf('item_id')]===Object.keys(fixture)[0]);conflict[header.indexOf('confidence')]='3';
context.conflictText=conflictRows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
let rejected=false;try{run('validatedImport(conflictText)')}catch(e){rejected=true}if(!rejected||run('JSON.stringify(answers)')!==before)throw Error('Conflicting import overwrote scores');
let pending=run('validatedImport(csvText)');run('answers={}');context.pending=pending;run('for(let[id,v]of Object.entries(pending))answers[id]=v');
for(let[id,values]of Object.entries(fixture))for(let[k,v]of Object.entries(values))if(run(`answers[${JSON.stringify(id)}][${JSON.stringify(k)}]`)!==v)throw Error('Restore changed rating');
console.log('DISPLAY_VALUES_STORAGE_AND_CSV_COMPATIBLE');
'''.replace('__FIXTURE__',json.dumps(fixture,ensure_ascii=False)).replace('__SCRIPT__',json.dumps(script,ensure_ascii=False)).replace('__LABELS__',json.dumps(OPTION_LABELS,ensure_ascii=False))
    (checks/'compatibility.mjs').write_text(test,encoding='utf-8')
    result=subprocess.run([node,str(checks/'compatibility.mjs')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    server=ThreadingHTTPServer(('127.0.0.1',0),serve_review_labeled.handler());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/') as r:body=r.read().decode();assert 'optionText(key,v)' in body and '1＝低信心' not in body # labels generated at runtime
        with urlopen(base+'/guide_labeled.md') as r:assert '强度' in r.read().decode()
        for path in ('/private/media_map.json','/results/predictions.jsonl'):
            try:urlopen(base+path);raise AssertionError('Private route exposed')
            except HTTPError as error:assert error.code==404
    finally:server.shutdown();server.server_close();thread.join()
    with zipfile.ZipFile(sharing/'reviewer_02_offline_v1.zip') as a,zipfile.ZipFile(sharing/'reviewer_02_offline_v2.zip') as b:
        ma={p.filename.split('/media/',1)[1]:(p.CRC,p.file_size) for p in a.infolist() if '/media/' in p.filename}
        mb={p.filename.split('/media/',1)[1]:(p.CRC,p.file_size) for p in b.infolist() if '/media/' in p.filename}
        assert ma==mb and len(ma)==657
    protocol=read_json(HERE/'evidence/review_protocol.fixed.json')
    for name,expected in protocol['code_files_sha256'].items():assert file_hash(HERE/name)==expected
    assert frozen_hashes()==read_json(HERE/'evidence/integrity.json')['original_file_hashes']
    writejson(HERE/'evidence/review_display_v2_verification.json',dict(passed=True,dropdown_codes_unchanged=True,
        existing_browser_scores_unchanged=True,csv_roundtrip_preserves_codes_and_notes=True,conflicting_import_rejected=True,
        old_csv_restoration_passed=True,items_unchanged=360,media_files_unchanged=657,private_http_routes_404=True,
        frozen_protocol_and_original_files_unchanged=True,fixture_notice='Synthetic compatibility scores only existed in Node memory; no human rating files generated'))
    print(result.stdout.strip(),flush=True)

if __name__=='__main__':main()
