"""Display-only Chinese option labels layered over the frozen reviewer UI."""
from pathlib import Path
import json
import re

OPTION_LABELS={
 'polarity':{'-1':'负向情感','0':'中性情感','1':'正向情感'},
 'intensity':{'0':'无情感强度','1':'弱（最低非零档）','2':'较弱（高于1档、低于3档）','3':'较强','4':'强'},
 'confidence':{'1':'低信心','2':'中等信心','3':'高信心'},
 'clarity':{'1':'难以辨认','2':'受限但部分可辨','3':'清楚'},
 'speech_words_understood':{'0':'不能理解词义','1':'能理解词义'},
 'subtitles_present':{'0':'未见字幕','1':'存在字幕'},
 'subtitles_readable':{'0':'字幕不可读','1':'字幕可读'},
 'speech_content_conflict':{'0':'无明显语词冲突','1':'有明显语词冲突'},
 'subtitle_leakage':{'0':'无明显字幕信息泄露','1':'有明显字幕信息泄露'},
 'identity_discontinuity':{'0':'无明显身份变化／不一致','1':'有明显身份变化／不一致'},
 'scene_discontinuity':{'0':'无明显场景变化／不一致','1':'有明显场景变化／不一致'},
 'timing_discontinuity':{'0':'无明显同步问题','1':'有明显同步问题'},
 'processing_error':{'0':'无明显处理错误','1':'有明显处理错误'}
}
FIELD_HELP={
 'polarity':'仅判断所展示内容；无法判断请留空并备注，不要把未知当中性。',
 'intensity':'沿用原名称，按0至4递增。1为最低非零档，2为其上相邻档；没有新增数值阈值。',
 'confidence':'人的判断信心：1不确定较多；2基本可判断但仍有疑问；3有明确把握。',
 'clarity':'内容能否辨认，与情感强弱、判断信心是不同字段。',
 'speech_words_understood':'完整语音未模糊语词；非音频项不适用。',
 'subtitles_present':'仅判断展示的8帧中是否可见字幕；看不清用unknown。',
 'subtitles_readable':'确认无字幕时填na；有字幕但看不清填0或unknown，不要自动当可读。',
 'speech_content_conflict':'语词与独立文本明显语义不一致；仅语气不同不自动算语词冲突。',
 'subtitle_leakage':'可读字幕或画面文字是否提供词义或情感线索。',
 'identity_discontinuity':'没有原始参照、无法从展示本身判断时用unknown，不猜成0。',
 'scene_discontinuity':'没有原始参照、无法从展示本身判断时用unknown，不猜成0。',
 'timing_discontinuity':'8帧不足以充分检查唇音同步；证据不足用unknown。',
 'processing_error':'明显损坏、缺通道、异常重复或解码错误；情感不同本身不是处理错误。'
}

def labeled_page(page,guide_url):
    """Keep option.value, item order, storage keys and existing answer strings intact."""
    assert "v===''?'未填写':v" in page
    page=page.replace("v===''?'未填写':v","optionText(key,v)")
    script='const optionLabels='+json.dumps(OPTION_LABELS,ensure_ascii=False)+';\nconst fieldHelp='+json.dumps(FIELD_HELP,ensure_ascii=False)+''';
function optionText(key,value){if(value==='')return '未填写（尚未判断）';if(value==='unknown')return 'unknown＝无法判断／证据不足';if(value==='na')return 'na＝不适用';return value+'＝'+(optionLabels[key]?.[value]||value)}
'''
    page=page.replace('function choices(',script+'\nfunction choices(',1)
    page=page.replace("label.append(s);box.append(label)","label.append(s);if(fieldHelp[key])label.title=fieldHelp[key];box.append(label)",1)
    page=re.sub(r'<option>(reviewer_0[12])</option>',lambda m:f'<option value="{m[1]}">{m[1]}＝审核者{m[1][-1]}</option>',page)
    page=page.replace('href="/guide.md"',f'href="{guide_url}"').replace('href="字段说明.md"',f'href="{guide_url}"')
    page=page.replace('<p id="status"></p>',
        '<p class="info">选项显示为“编码＝中文含义”，导出仍保存原编码。强度0→4递增；空白是未完成，unknown是无法判断，na是不适用。已有评分不自动改写。</p>'
        '<label>恢复旧评分CSV <input id="importCsv" type="file" accept=".csv,text/csv"></label>'
        '<p id="importStatus" role="status"></p><p id="status"></p>')
    importer=r'''
function parseCsv(text){text=text.replace(/^\uFEFF/,'');let rows=[],row=[],value='',quoted=false;
for(let i=0;i<text.length;i++){let c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++}else if(quoted)quoted=false;else if(value==='')quoted=true;else throw Error('CSV引号格式无效')}
else if(c===','&&!quoted){row.push(value);value=''}else if((c==='\r'||c==='\n')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(value);if(row.some(x=>x!==''))rows.push(row);row=[];value=''}else value+=c}
if(quoted)throw Error('CSV引号未闭合');if(value!==''||row.length){row.push(value);rows.push(row)}return rows}
function validatedImport(text){const rows=parseCsv(text),header=rows.shift();if(!header||fields.some(k=>!header.includes(k)))throw Error('缺少原评分字段');
const ids=new Map(data.map(x=>[x.item_id,x])),seen=new Set(),pending={},valid={polarity:['-1','0','1'],intensity:['0','1','2','3','4'],confidence:['1','2','3'],clarity:['1','2','3','unknown','na'],speech_words_understood:['0','1','unknown','na'],subtitles_present:['0','1','unknown','na'],subtitles_readable:['0','1','unknown','na']};
for(let values of rows){if(values.length!==header.length)throw Error('CSV列数不一致');let r=Object.fromEntries(header.map((k,i)=>[k,values[i]]));
if(r.reviewer_id!==rid())throw Error('审核者编号不匹配，请选择正确审核者');let item=ids.get(r.item_id);if(!item||r.stage!==item.stage||seen.has(r.item_id))throw Error('项目编号、阶段或重复行异常');seen.add(r.item_id);
for(let k of fields){if(['reviewer_id','item_id','stage','notes'].includes(k))continue;let allowed=valid[k]||['0','1','unknown'];if(r[k]!==''&&!allowed.includes(r[k]))throw Error('非法编码：'+k+'（不会自动转换）')}
pending[r.item_id]={};for(let k of fields){if(['reviewer_id','item_id','stage'].includes(k))continue;let old=answers[r.item_id]?.[k]??'',incoming=r[k];if(old!==''&&incoming!==''&&old!==incoming)throw Error('与当前保存的评分冲突：'+r.item_id+' / '+k+'；未覆盖任何评分');if(incoming!==''||old==='')pending[r.item_id][k]=incoming;else pending[r.item_id][k]=old}}
return pending}
document.querySelector('#importCsv').onchange=async event=>{const status=document.querySelector('#importStatus');try{const file=event.target.files[0];if(!file)return;let pending=validatedImport(await file.text());for(let [id,values]of Object.entries(pending))answers[id]={...(answers[id]||{}),...values};persist();show();status.textContent='已恢复 '+Object.keys(pending).length+' 行；编码和已有非空评分保持不变。'}catch(error){status.textContent='未导入：'+error.message}};
'''
    # Literal newlines/BOM in the JavaScript parser are escaped exactly once.
    return page.replace('</script>',importer+'\n</script>')

def revised_guide(original):
    return '''# 审核选项中文显示说明（显示修订v2）

本版只补充界面文字及解释，编码、360个项目、评分字段、关系/质量标准和已有评分均不改变。

## 字段核对发现与统一说明

- 原指南已定义极性、强度、信心、清晰度、词义/字幕及六种问题标记；原界面只显示数字，现改为“编码＝中文含义”。
- 原强度名称“1＝弱、2＝较弱”未写清相邻档关系。仍沿用原名称，统一按0→4递增：0无，1最低非零档，2高于1而低于3，3较强，4强。没有新增可量化阈值，也不据此回改旧分数。
- 原信心仅有低/中/高名称，补充一致的文字解释：1不确定较多；2基本可判断但仍有疑问；3有明确把握。这是说明，不是新的判定阈值。
- 空白＝尚未填写/未能完成，不能当0；unknown＝已经查看但证据不足；na＝该项不适用。极性/强度/信心没有unknown编码，无法判断时留空并备注；仍按原程序标为未完成。

旧评分不自动重新编码或覆盖。已经填写过时，先从旧页面导出CSV，再在本页选择同一审核者并导入；如有冲突会整体拒绝导入，保留当前评分。

---

'''+original
