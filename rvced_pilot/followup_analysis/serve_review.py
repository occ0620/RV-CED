"""Loopback-only blind review server. No arbitrary paths, directory listing or private mapping routes."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse
import json
from urllib.parse import urlsplit
from common import HERE,read_json,file_hash

PAGE='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>独立盲审</title>
<style>body{font:16px system-ui;max-width:1050px;margin:25px auto;line-height:1.6}article{border:1px solid #bbb;padding:18px}img{width:24%;height:auto}button,a{padding:8px}pre{white-space:pre-wrap}input,textarea,select{font:inherit;margin:5px}textarea{width:95%}.info{background:#eef;padding:12px}</style>
<h1>独立盲审</h1><p class="info">关系审核待完成。只评价展示内容，不参考标签或模型结果。先完成并导出第一步，再查看第二步。两人独立操作；浏览器进度不代表已经提交评分。</p>
<label>审核者 <select id="reviewer"><option>reviewer_01</option><option>reviewer_02</option></select></label>
<a href="/guide.md" target="_blank">字段说明</a><a href="/reviewer_01.blank.csv">审核者1空表</a><a href="/reviewer_02.blank.csv">审核者2空表</a>
<div><button id="one">第一步：独立通道</button><button id="two">第二步：组合输入</button><button id="prev">上一项</button><button id="next">下一项</button><button id="export">导出当前评分CSV</button></div>
<p id="status"></p><main id="item"></main>
<script>
let data=[],stage='channel',index=0,answers={};
const fields=__FIELDS__,issues=__ISSUES__;
const labels={polarity:'极性',intensity:'强度',confidence:'判断信心',clarity:'清晰度',speech_words_understood:'完整语音词义可理解',subtitles_present:'存在字幕',subtitles_readable:'字幕可读',speech_content_conflict:'语词冲突',subtitle_leakage:'字幕泄露',identity_discontinuity:'身份变化',scene_discontinuity:'场景变化',timing_discontinuity:'同步问题',processing_error:'明显处理错误'};
function rid(){return document.querySelector('#reviewer').value}function storage(){return 'rvced-blind-'+rid()}
function persist(){localStorage.setItem(storage(),JSON.stringify(answers))}
function el(t,text){let x=document.createElement(t);if(text!==undefined)x.textContent=text;return x}
function choices(box,item,key,opts){let label=el('label',labels[key]+': '),s=el('select');for(let v of ['',...opts]){let o=el('option',v===''?'未填写':v);o.value=v;s.append(o)}s.value=answers[item.item_id]?.[key]||'';s.onchange=()=>{answers[item.item_id]??={};answers[item.item_id][key]=s.value;persist()};label.append(s);box.append(label)}
function show(){let items=data.filter(x=>x.stage===stage);index=Math.max(0,Math.min(index,items.length-1));let x=items[index],host=document.querySelector('#item');host.replaceChildren();document.querySelector('#status').textContent=`${stage==='channel'?'第一步':'第二步'} ${index+1}/${items.length} · ${x.item_id}`;let box=el('article');
for(let[m,c]of Object.entries(x.channels)){box.append(el('h3',m));if(m==='T')box.append(el('pre',c.text));if(m==='A'){box.append(el('p',c.speech_type));let a=el('audio');a.controls=true;a.preload='none';a.src=c.audio;box.append(a)}if(m==='V'){box.append(el('p',`无声 ${c.frame_count} 帧，原片段 ${c.duration.toFixed(3)} 秒。八帧不能充分判断唇音同步；不确定填 unknown。`));for(let f of c.frames){let i=el('img');i.src=f;box.append(i)}}}
if(stage==='channel'){choices(box,x,'polarity',['-1','0','1']);choices(box,x,'intensity',['0','1','2','3','4']);choices(box,x,'confidence',['1','2','3']);choices(box,x,'clarity',['1','2','3','unknown']);choices(box,x,'speech_words_understood',x.channels.A?['0','1','unknown']:['na']);choices(box,x,'subtitles_present',x.channels.V?['0','1','unknown']:['na']);choices(box,x,'subtitles_readable',x.channels.V?['0','1','unknown','na']:['na'])}else{for(let f of issues)choices(box,x,f,['0','1','unknown'])}
let notes=el('textarea');notes.placeholder='备注：看不清、不能判断、发现重复等';notes.value=answers[x.item_id]?.notes||'';notes.oninput=()=>{answers[x.item_id]??={};answers[x.item_id].notes=notes.value;persist()};box.append(notes);host.append(box)}
function csv(v){return '"'+String(v??'').replaceAll('"','""')+'"'}
document.querySelector('#export').onclick=()=>{let rows=[fields.join(',')];for(let x of data){let a=answers[x.item_id]||{};let row={...a,reviewer_id:rid(),item_id:x.item_id,stage:x.stage};for(let k of ['clarity','speech_words_understood','subtitles_present','subtitles_readable'])if(x.stage==='combination')row[k]='na';rows.push(fields.map(k=>csv(row[k])).join(','))}let b=new Blob(['\\ufeff'+rows.join('\\r\\n')],{type:'text/csv;charset=utf-8'}),u=URL.createObjectURL(b),a=el('a');a.href=u;a.download=rid()+'.completed.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
document.querySelector('#reviewer').onchange=()=>{answers=JSON.parse(localStorage.getItem(storage())||'{}');show()};document.querySelector('#one').onclick=()=>{stage='channel';index=0;show()};document.querySelector('#two').onclick=()=>{if(!confirm('请先完成并导出第一步。是否进入第二步？'))return;stage='combination';index=0;show()};document.querySelector('#prev').onclick=()=>{index--;show()};document.querySelector('#next').onclick=()=>{index++;show()};
fetch('/items.json').then(r=>r.json()).then(d=>{data=d;answers=JSON.parse(localStorage.getItem(storage())||'{}');show()});
</script></html>'''

def handler():
    media=read_json(HERE/'private/media_map.json');public=HERE/'reviewer'
    from common import FIELDS,ISSUES
    page=PAGE.replace('__FIELDS__',json.dumps(FIELDS)).replace('__ISSUES__',json.dumps(ISSUES)).encode()
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/':data,kind=page,'text/html; charset=utf-8'
            elif path in ('/items.json','/reviewer_01.blank.csv','/reviewer_02.blank.csv','/guide.md'):
                f=public/path[1:];data=f.read_bytes();kind='application/json; charset=utf-8' if path.endswith('.json') else 'text/plain; charset=utf-8'
            elif path.startswith('/media/') and path[len('/media/'):] in media:
                entry=media[path[len('/media/'):]];data=__import__('pathlib').Path(entry['path']).read_bytes();kind=entry['content_type']
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    return ReviewHandler

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    print(f'BLIND_REVIEW http://127.0.0.1:{a.port}  (only whitelisted public routes)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),handler()).serve_forever()
