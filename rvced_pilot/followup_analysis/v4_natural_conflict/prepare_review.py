"""Prepare the prediction-blind v4 natural-conflict review package.

This program reads only the frozen inventory and raw media. It must never read model
predictions. It creates all 35 strict natural-conflict candidates, exact eight-frame
previews, an offline review page, two blank CSVs, and a private label/path mapping.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import numpy as np


HERE = Path(__file__).resolve().parent
PILOT = HERE.parents[1]
INVENTORY = PILOT / "runs/main-a800-v3/manifests/inventory.csv"
PREDICTIONS = PILOT / "runs/main-a800-v3/results/predictions.jsonl"
FFMPEG = next((PILOT / "tools").glob("*/bin/ffmpeg.exe"))
FFPROBE = next((PILOT / "tools").glob("*/bin/ffprobe.exe"))
SEED = 20261016
TAU = 0.2
N_FRAMES = 8

PRIVATE = HERE / "private"
REVIEW = HERE / "reviewer"
MEDIA = REVIEW / "media"
SHARING = HERE / "sharing"

FIELDS = [
    "reviewer_id", "item_id", "target_modality", "target_polarity",
    "target_confidence", "target_clarity", "speech_words_understood",
    "speech_text_match", "subtitles_present", "subtitles_readable",
    "sync_issue", "processing_error", "notes",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def polarity(value: str) -> int:
    x = float(value)
    return 1 if x > TAU else -1 if x < -TAU else 0


def strict_target(row: dict[str, str]) -> str | None:
    t, a, v = (polarity(row[k]) for k in ("y_t", "y_a", "y_v"))
    if t != 0 and a == v != 0 and t == -a:
        return "T"
    if v != 0 and t == a != 0 and v == -t:
        return "V"
    return None


def frame_indices(total: int) -> list[int]:
    # Use the exact v3 preprocessing expression so review frame times and later
    # inference frame times are identical.
    return np.linspace(0, total - 1, N_FRAMES).round().astype(int).tolist()


def extract_frames(source: Path, total: int, target_dir: Path) -> tuple[list[str], list[int]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    indices = frame_indices(total)
    if len(set(indices)) != N_FRAMES:
        raise RuntimeError(f"Fewer than {N_FRAMES} distinct indices for {source}")
    expected = [target_dir / f"frame_{i + 1:02d}.jpg" for i in range(N_FRAMES)]
    if not all(p.exists() for p in expected):
        selector = "+".join(f"eq(n\\,{i})" for i in indices)
        subprocess.run([
            str(FFMPEG), "-nostdin", "-y", "-v", "error", "-xerror",
            "-threads", "2", "-i", str(source), "-map", "0:v:0", "-an",
            "-vf", "select=" + selector, "-fps_mode", "passthrough", "-frames:v", str(N_FRAMES),
            "-q:v", "2", "-threads", "1", str(target_dir / "frame_%02d.jpg"),
        ], check=True, timeout=180)
    if not all(p.exists() and p.stat().st_size > 0 for p in expected):
        raise RuntimeError(f"Frame extraction incomplete for {source}")
    return [p.name for p in expected], indices


def browser_video(source: Path, target: Path) -> str:
    """Keep ordinary H.264/AAC files byte-identical; transcode unsupported 10-bit copies."""
    probe = subprocess.run([
        str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", str(source),
    ], check=True, capture_output=True, text=True, timeout=30)
    stream = json.loads(probe.stdout)["streams"][0]
    if stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p":
        shutil.copyfile(source, target)
        return "byte-identical-source-copy"
    temp = target.with_name(target.stem + ".tmp.mp4")
    subprocess.run([
        str(FFMPEG), "-nostdin", "-y", "-v", "error", "-xerror", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(temp),
    ], check=True, timeout=180)
    temp.replace(target)
    return "browser-review-transcode-h264-yuv420p-aac; inference-uses-original"


def review_wav(source: Path, target: Path) -> None:
    """Decode a universal speech track using the model's 16 kHz mono convention."""
    subprocess.run([
        str(FFMPEG), "-nostdin", "-y", "-v", "error", "-xerror", "-i", str(source),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
    ], check=True, timeout=180)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def html(items: list[dict[str, object]]) -> str:
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    return r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RV-CED v4 自然冲突盲审</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f4f6fa;color:#172033}.top{position:sticky;top:0;z-index:5;background:white;border-bottom:1px solid #ccd5e3;padding:12px 4vw}.wrap{max-width:1100px;margin:auto}.card{background:white;border:1px solid #d9e0ea;border-radius:12px;padding:18px;margin:18px 0;box-shadow:0 2px 8px #17203312}.notice{background:#fff7da;border-left:5px solid #e5a400;padding:10px 14px}.target{font-size:1.35rem;padding:18px;background:#f2f6ff;border-radius:8px}.frames{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.frames img{width:100%;height:170px;object-fit:contain;background:#111}.fields{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:12px;margin-top:14px}.field{border:1px solid #dfe5ef;border-radius:8px;padding:10px}.field label{font-weight:650;display:block;margin-bottom:7px}select,textarea{width:100%;box-sizing:border-box;padding:8px;border:1px solid #aeb9c9;border-radius:6px;background:white}video{width:100%;max-height:520px;background:#111}audio{width:100%;margin:8px 0}.buttons{display:flex;gap:8px;flex-wrap:wrap}.buttons button,.buttons a{border:1px solid #63718a;background:white;border-radius:7px;padding:8px 12px;text-decoration:none;color:#172033;cursor:pointer}.buttons .primary{background:#235bd8;color:white}.muted{color:#5f6c7e}.stage{border-top:2px solid #dbe3ef;margin-top:18px;padding-top:12px}.hidden{display:none}@media(max-width:750px){.frames{grid-template-columns:repeat(2,1fr)}.fields{grid-template-columns:1fr}.frames img{height:130px}}
</style></head><body><div class="top"><div class="wrap"><b>RV-CED v4 自然冲突候选盲审</b>
<div class="buttons"><label>审核者 <select id="reviewer"><option value="">请选择</option><option value="reviewer_01">reviewer_01＝审核者1</option><option value="reviewer_02">reviewer_02＝审核者2</option></select></label><button id="prev">上一项</button><button id="next">下一项</button><button class="primary" id="export">导出当前评分 CSV</button><label>恢复评分 <input id="import" type="file" accept=".csv"></label><a href="guide.md">审核指南</a></div><div id="status" class="muted"></div></div></div>
<main class="wrap"><p class="notice">先独立判断目标通道，再查看原始同步片段。页面不含数据标签、模型预测或预期方向。无法判断请选择 unknown，不要猜。浏览器会自动保存到本机，但仍请经常导出 CSV 备份。若视频显示 0:00，请不要使用 IDE 内置预览，运行“启动盲审.ps1”后在 Chrome/Edge 中填写。</p><div id="root"></div></main>
<script>
const items=__ITEMS__;
const schema={
target_polarity:[["","未填写"],["-1","-1＝负向"],["0","0＝中性"],["1","1＝正向"],["unknown","unknown＝无法判断"]],
target_confidence:[["","未填写"],["1","1＝低信心"],["2","2＝中等信心"],["3","3＝高信心"]],
target_clarity:[["","未填写"],["1","1＝难以辨认"],["2","2＝受限但可辨"],["3","3＝清楚"],["unknown","unknown＝无法判断"]],
speech_words_understood:[["","未填写"],["0","0＝不能听懂词义"],["1","1＝能听懂词义"],["unknown","unknown＝无法判断"]],
speech_text_match:[["","未填写"],["0","0＝语音与转写不一致"],["1","1＝语音与转写一致"],["unknown","unknown＝无法判断"]],
subtitles_present:[["","未填写"],["0","0＝8帧均未见字幕/文字"],["1","1＝至少1帧存在字幕/文字"],["unknown","unknown＝无法判断"]],
subtitles_readable:[["","未填写"],["0","0＝存在但不可读"],["1","1＝存在且可读"],["unknown","unknown＝无法判断"],["na","na＝确认无字幕，不适用"]],
sync_issue:[["","未填写"],["0","0＝无明显音画同步问题"],["1","1＝有明显音画同步问题"],["unknown","unknown＝无法判断"]],
processing_error:[["","未填写"],["0","0＝无明显解码/处理错误"],["1","1＝有明显解码/处理错误"],["unknown","unknown＝无法判断"]]};
const labels={target_polarity:"目标通道极性",target_confidence:"目标判断信心",target_clarity:"目标内容清晰度",speech_words_understood:"完整语音能否听懂词义",speech_text_match:"语音与页面转写是否一致",subtitles_present:"模型8帧中是否出现字幕/文字",subtitles_readable:"字幕/文字是否可读",sync_issue:"原片是否有明显音画同步问题",processing_error:"是否有解码、卡帧或缺声等错误"};
let pos=0,answers={}; const $=s=>document.querySelector(s); const rid=()=>$('#reviewer').value; const key=()=>`rvced-v4-natural-conflict-${rid()}`;
function el(tag,attrs={},text=''){let x=document.createElement(tag);Object.assign(x,attrs);if(text)x.textContent=text;return x}
function load(){answers={};if(rid())try{answers=JSON.parse(localStorage.getItem(key())||'{}')}catch(e){};render()}
function save(){if(rid())localStorage.setItem(key(),JSON.stringify(answers));progress()}
function select(field){let s=el('select');for(let [v,t] of schema[field])s.append(el('option',{value:v},t));return s}
function frameGrid(item){let box=el('div',{className:'frames'});item.frames.forEach((p,i)=>{let im=el('img',{src:p,alt:`固定抽样帧 ${i+1}`});box.append(im)});return box}
function fieldBox(item,field){let box=el('div',{className:'field'}),s=select(field);s.value=(answers[item.item_id]||{})[field]||'';s.onchange=()=>{answers[item.item_id]??={};answers[item.item_id][field]=s.value;save()};box.append(el('label',{},labels[field]),s);return box}
function render(){let root=$('#root');root.innerHTML='';if(!rid()){let prompt=el('section',{className:'card'});prompt.append(el('h2',{},'请先在页面顶部选择审核者编号'));prompt.append(el('p',{},'选择后页面才会载入该审核者的独立本地进度。'));root.append(prompt);progress();return}let item=items[pos],card=el('section',{className:'card'});card.append(el('h2',{},`候选 ${pos+1}/${items.length} · ${item.item_id} · 目标通道 ${item.target_modality}`));
let s1=el('div');s1.append(el('h3',{},'步骤1：只看目标通道'));if(item.target_modality==='T')s1.append(el('div',{className:'target'},item.text));else s1.append(frameGrid(item));let f1=el('div',{className:'fields'});['target_polarity','target_confidence','target_clarity'].forEach(f=>f1.append(fieldBox(item,f)));s1.append(f1);card.append(s1);
let s2=el('div',{className:'stage'});s2.append(el('h3',{},'步骤2：查看原始同步片段与模型8帧'));s2.append(el('p',{className:'muted'},'点击视频播放；声音由下方WAV轨同步输出。也可单独播放WAV检查语音。再核对页面转写和固定8帧；字幕判断只以固定8帧为准。'));let v=el('video',{src:item.video,controls:true,preload:'metadata',muted:true}),a=el('audio',{src:item.audio,controls:true,preload:'metadata'});let sync=()=>{if(Math.abs((a.currentTime||0)-(v.currentTime||0))>.18)a.currentTime=v.currentTime||0};v.onplay=()=>{sync();a.playbackRate=v.playbackRate;a.play().catch(()=>{})};v.onpause=()=>a.pause();v.onseeking=sync;v.ontimeupdate=sync;v.onratechange=()=>a.playbackRate=v.playbackRate;v.onended=()=>{a.pause();a.currentTime=0};s2.append(v,el('p',{className:'muted'},'同步WAV声音轨（视频控件显示静音属于正常现象）'),a,el('p',{className:'target'},item.text),frameGrid(item));let f2=el('div',{className:'fields'});['speech_words_understood','speech_text_match','subtitles_present','subtitles_readable','sync_issue','processing_error'].forEach(f=>f2.append(fieldBox(item,f)));let nb=el('div',{className:'field'}),ta=el('textarea',{rows:3,placeholder:'可选：记录歧义、画面文字位置或问题'});ta.value=(answers[item.item_id]||{}).notes||'';ta.oninput=()=>{answers[item.item_id]??={};answers[item.item_id].notes=ta.value;save()};nb.append(el('label',{},'备注'),ta);f2.append(nb);s2.append(f2);card.append(s2);root.append(card);progress();scrollTo(0,0)}
function progress(){let done=items.filter(x=>{let a=answers[x.item_id]||{};return Object.keys(schema).every(k=>a[k])}).length;$('#status').textContent=`自动保存：${rid()||'尚未选择审核者'}；完整 ${done}/${items.length}`}
function csvCell(v){v=String(v??'');return /[",\r\n]/.test(v)?'"'+v.replaceAll('"','""')+'"':v}
function exportCsv(){if(!rid())return alert('请先选择审核者');let fields=['reviewer_id','item_id','target_modality',...Object.keys(schema),'notes'];let rows=[fields.join(',')];for(let x of items){let a=answers[x.item_id]||{};rows.push(fields.map(k=>csvCell(k==='reviewer_id'?rid():k==='item_id'?x.item_id:k==='target_modality'?x.target_modality:a[k])).join(','))}let b=new Blob(['\ufeff'+rows.join('\r\n')],{type:'text/csv;charset=utf-8'}),u=URL.createObjectURL(b),a=el('a',{href:u,download:`${rid()}.v4.completed.csv`});a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
function parseCsv(text){let rows=[],row=[],v='',q=false;text=text.replace(/^\uFEFF/,'');for(let i=0;i<text.length;i++){let c=text[i];if(c==='"'){if(q&&text[i+1]==='"'){v+='"';i++}else q=!q}else if(c===','&&!q){row.push(v);v=''}else if((c==='\r'||c==='\n')&&!q){if(c==='\r'&&text[i+1]==='\n')i++;row.push(v);if(row.some(Boolean))rows.push(row);row=[];v=''}else v+=c}if(v||row.length){row.push(v);rows.push(row)}return rows}
async function importCsv(file){let rows=parseCsv(await file.text()),head=rows.shift(),idix=head.indexOf('item_id'),rix=head.indexOf('reviewer_id');if(idix<0||rix<0)throw Error('缺少字段');let incoming={};for(let row of rows){if(row[rix]!==rid())throw Error('审核者编号不匹配');let id=row[idix];if(!items.some(x=>x.item_id===id)||incoming[id])throw Error('项目编号异常或重复');incoming[id]={};head.forEach((k,i)=>{if(schema[k]||k==='notes')incoming[id][k]=row[i]||''})}if(Object.keys(incoming).length!==items.length)throw Error('行数不完整');answers=incoming;save();render()}
$('#reviewer').onchange=load;$('#prev').onclick=()=>{pos=(pos+items.length-1)%items.length;render()};$('#next').onclick=()=>{pos=(pos+1)%items.length;render()};$('#export').onclick=exportCsv;$('#import').onchange=async e=>{try{await importCsv(e.target.files[0]);alert('评分已恢复')}catch(x){alert('未导入：'+x.message)}};render();
</script></body></html>'''.replace("__ITEMS__", data)


GUIDE = """# RV-CED v4 自然冲突候选盲审指南

本轮只审核原始同步片段，用于在模型推理前冻结 v4 样本。页面不含模型结果、官方目标极性或预期方向。两位审核者应独立填写，不能查看对方答案。

请用 Chrome 或 Edge。若视频显示 `0:00`，说明 `file://` 或 IDE 内置预览阻止了本地媒体；运行同目录的 `启动盲审.ps1`，通过它打开的 `http://127.0.0.1:8774/` 页面继续。若旧页面已有评分，先导出 CSV，再在新页面选择同一审核者并导入。

视频控件固定静音，声音由页面下方的 16 kHz PCM WAV 轨同步播放。点击视频播放时 WAV 会跟随；也可以单独点击 WAV 控件试听。正式推理仍从原始 MP4 解码音频。

每人审核 35 个候选，每个候选有两个连续步骤，不是 70 个独立样本：

1. **目标通道**：T 候选只看文字；V 候选只看模型将使用的 8 帧。判断极性、信心和清晰度。
2. **原始同步质量**：播放同一原片并核对转写、8 帧字幕、音画同步和解码质量。

## 统一编码

- `target_polarity`：`-1＝负向`、`0＝中性`、`1＝正向`、`unknown＝无法判断`。只按所展示目标通道判断，不能综合另外两个通道。
- `target_confidence`：`1＝低信心`、`2＝中等信心`、`3＝高信心`。
- `target_clarity`：`1＝难以辨认`、`2＝受限但可辨`、`3＝清楚`、`unknown＝无法判断`。T 指文字含义是否明确；V 指表情/动作情感线索是否可辨。
- `speech_words_understood`：完整原片语音能否听懂词义。
- `speech_text_match`：听到的语词是否与页面转写一致；只因语气不同不要判为不一致。
- `subtitles_present`：只检查页面列出的固定 8 帧。任何可见字幕、台词或能传递语义的画面文字均记 1。
- `subtitles_readable`：确认无字幕时选 `na`；出现文字但无法辨认选 0；可以辨认选 1。
- `sync_issue`：播放原片时是否存在明显唇音错位、画面和说话时序不一致。
- `processing_error`：是否存在缺声、黑屏、异常重复、严重卡帧或解码损坏。

拿不准时选 `unknown` 并写备注，不要用“中性”代替“不知道”。页面会自动保存在当前浏览器，但缓存可能被清理；每完成一部分都导出 CSV 备份。

## 推理前固定的通过规则

两位审核者都必须满足：目标极性与数据标签方向一致、信心≥2、清晰度≥2、语音可懂、语音与转写一致、8帧无字幕/文字、无同步问题、无处理错误。任何 `unknown` 均保留在原始审核记录中，但候选不通过。通过后每模态按固定种子最多抽取 12 个；模型输出不能参与筛选。若通过量不足，不放宽规则补样本。
"""


README = """# v4 自然冲突小规模实验：审核与冻结阶段

v4 使用每个原始片段自身同步的 T/A/V，不再跨片段拼接。严格自然冲突候选由 `tau=0.2` 的数据标签关系预先确定：T 目标与 A、V 方向相反且 A=V；或 V 目标与 T、A 方向相反且 T=A。A 严格候选为 0，因此本轮不放宽定义。

冻结后只做一项配对检验：T 候选比较完整同步 TAV 与 AV-only；V 候选比较完整同步 TAV 与 TA-only。主量为 `E_m = sign(y_m) × [s(full) − s(without target)]`，正值表示加入目标通道后预测向目标标签移动。最多 24 个来源、48 个唯一主输入；这不是多轮消融，也不解释为供体替换的因果效应。

当前阶段没有读取新旧模型预测，也没有运行模型推理。先让两位审核者分别打开 `reviewer/打开盲审.html`，选择各自编号并填写，导出：

- `reviewer_01.v4.completed.csv`
- `reviewer_02.v4.completed.csv`

审核完成后运行：

```powershell
D:\\installpackage\\python.exe rvced_pilot/followup_analysis/v4_natural_conflict/merge_reviews.py --reviewer-1 <审核者1CSV> --reviewer-2 <审核者2CSV>
```

脚本会校验完整性、保存独立评分与分歧、按预定规则生成最多 12 个 T 和 12 个 V 的冻结清单。若任一模态少于 10 个合格候选，状态写为 `PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES`，不得自行放宽标准。

离线包在 `sharing/rvced_v4_review_offline.zip`。包内含原始视频，不随公开 Git 仓库发布；请通过私下文件传输交给审核者。本地若需分卷传递，可使用同目录的小于 80 MiB 分卷和 `重新合并压缩包.ps1`。媒体仅限项目审核者使用。
"""

QUICKSTART = """RV-CED v4 盲审包使用方法

1. 先完整解压 ZIP，不要直接在压缩包预览窗口里打开网页。
2. 首选右键运行“启动盲审.ps1”，它会在 Chrome/Edge 默认浏览器打开本地页面；也可直接双击“打开盲审.html”。若视频显示0:00，必须使用启动脚本。
3. 在页面顶部选择自己的编号：一人固定 reviewer_01，另一人固定 reviewer_02。
4. 每人填写 35 个候选。每个候选先单独看目标通道，再播放同一段原始同步视频检查质量。视频本身固定静音，声音由下方WAV控件同步输出；也可单独播放WAV。
5. 页面会自动保存在当前浏览器；仍请经常点击“导出当前评分 CSV”备份。
6. 全部完成后，把 reviewer_01.v4.completed.csv 或 reviewer_02.v4.completed.csv 发给项目组织者。

两人必须独立填写，不查看对方评分。页面不含模型预测和官方情感方向。拿不准请选择 unknown 并写备注，不要猜。
"""


def main() -> None:
    if not INVENTORY.exists() or not FFMPEG.exists():
        raise SystemExit("Frozen inventory or ffmpeg is missing")
    if PRIVATE.exists() or REVIEW.exists():
        raise SystemExit("Output already exists; frozen preparation will not be overwritten")
    PRIVATE.mkdir(parents=True)
    MEDIA.mkdir(parents=True)
    SHARING.mkdir(parents=True, exist_ok=True)

    with INVENTORY.open(encoding="utf-8-sig", newline="") as f:
        inventory = list(csv.DictReader(f))
    candidates = [(r, strict_target(r)) for r in inventory if r["eligible"] == "True" and r["split"] == "test"]
    candidates = [(r, m) for r, m in candidates if m]
    counts = {m: sum(x[1] == m for x in candidates) for m in ("T", "V")}
    if counts != {"T": 18, "V": 17}:
        raise RuntimeError(f"Strict candidate count changed: {counts}")

    rng = random.Random(SEED)
    rng.shuffle(candidates)
    public, private, media_rows = [], [], []
    for rank, (row, modality) in enumerate(candidates, 1):
        item_id = "NC-" + digest([SEED, row["clip_id"], modality])[:10].upper()
        opaque = digest(["media", SEED, row["media_sha256"]])[:20]
        item_media = MEDIA / opaque
        frames, indices = extract_frames(Path(row["origin_media_path"]), int(row["video_frames"]), item_media)
        video_name = opaque + ".mp4"
        audio_name = opaque + ".wav"
        video_target = MEDIA / video_name
        source_media = Path(row["origin_media_path"])
        if sha(source_media) != row["media_sha256"]:
            raise RuntimeError(f"Source media hash mismatch: {row['clip_id']}")
        review_media_mode = browser_video(source_media, video_target)
        audio_target = MEDIA / audio_name
        review_wav(source_media, audio_target)
        pub = {
            "item_id": item_id,
            "target_modality": modality,
            "text": row["text"],
            "video": f"media/{video_name}",
            "audio": f"media/{audio_name}",
            "frames": [f"media/{opaque}/{name}" for name in frames],
        }
        public.append(pub)
        private.append({
            "item_id": item_id, "display_rank": rank, "clip_id": row["clip_id"],
            "target_modality": modality, "official_target_polarity": polarity(row["y_" + modality.lower()]),
            "y_t": row["y_t"], "y_a": row["y_a"], "y_v": row["y_v"], "y_multi": row["y_multi"],
            "text": row["text"], "duration": row["duration"], "video_frames": row["video_frames"],
            "sampled_frame_indices": json.dumps(indices), "origin_media_path": row["origin_media_path"],
            "media_sha256": row["media_sha256"], "public_video": pub["video"], "review_media_mode": review_media_mode,
        })
        media_rows.append({"relative_path": pub["video"], "sha256": sha(video_target), "bytes": video_target.stat().st_size})
        media_rows.append({"relative_path": pub["audio"], "sha256": sha(audio_target), "bytes": audio_target.stat().st_size})
        for rel in pub["frames"]:
            p = REVIEW / rel
            media_rows.append({"relative_path": rel, "sha256": sha(p), "bytes": p.stat().st_size})
        print(f"PREPARED {rank}/{len(candidates)} {item_id}", flush=True)

    write_csv(PRIVATE / "candidate_mapping.csv", list(private[0]), private)
    write_csv(PRIVATE / "media_hashes.csv", ["relative_path", "sha256", "bytes"], media_rows)
    (REVIEW / "items.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REVIEW / "打开盲审.html").write_text(html(public), encoding="utf-8")
    (REVIEW / "guide.md").write_text(GUIDE, encoding="utf-8")
    (REVIEW / "先读我.txt").write_text(QUICKSTART, encoding="utf-8")
    (REVIEW / "serve_review.py").write_text('''from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler\nfrom pathlib import Path\nimport functools, threading, urllib.parse, webbrowser\nroot=Path(__file__).resolve().parent\nurl="http://127.0.0.1:8774/"+urllib.parse.quote("打开盲审.html")\nhandler=functools.partial(SimpleHTTPRequestHandler,directory=str(root))\nserver=ThreadingHTTPServer(("127.0.0.1",8774),handler)\nprint("盲审地址：",url,flush=True)\nthreading.Timer(0.5,lambda:webbrowser.open(url)).start()\ntry:server.serve_forever()\nexcept KeyboardInterrupt:pass\nfinally:server.server_close()\n''', encoding="utf-8")
    (REVIEW / "启动盲审.ps1").write_text('''$here=Split-Path -Parent $MyInvocation.MyCommand.Path\n$preferred='D:\\installpackage\\python.exe'\nif(Test-Path -LiteralPath $preferred){& $preferred (Join-Path $here 'serve_review.py')}else{python (Join-Path $here 'serve_review.py')}\n''', encoding="utf-8-sig")
    blank = [{"reviewer_id": rid, "item_id": x["item_id"], "target_modality": x["target_modality"]} for x in public for rid in []]
    for rid in ("reviewer_01", "reviewer_02"):
        rows = [{"reviewer_id": rid, "item_id": x["item_id"], "target_modality": x["target_modality"]} for x in public]
        write_csv(REVIEW / f"{rid}.blank.csv", FIELDS, rows)
    (HERE / "README.md").write_text(README, encoding="utf-8")

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": "v4-natural-conflict-paired-target-removal-preinference-review",
        "candidate_rule": "eligible test; tau=0.2; target polarity nonzero and opposite two agreeing nonneutral other modalities",
        "seed": SEED, "tau": TAU, "frame_rule": "rounded-linspace-first-to-last-decoded-frame",
        "review_frame_encoding": "JPEG q:v=2 at source resolution; same indices as inference; inference re-decodes original media",
        "review_video_compatibility": "source H.264 yuv420p copied byte-identically; unsupported 10-bit review copies transcoded to H.264 yuv420p/AAC; inference always uses original media",
        "review_audio_compatibility": "PCM s16le mono 16 kHz WAV synchronized by the page; inference always decodes original media",
        "browser_review_transcodes": sum(x["review_media_mode"].startswith("browser-review-transcode") for x in private),
        "candidate_counts": counts, "total_candidates": len(public), "items_sha256": sha(REVIEW / "items.json"),
        "private_mapping_sha256": sha(PRIVATE / "candidate_mapping.csv"), "inventory_sha256": sha(INVENTORY),
        "predictions_path_existence_recorded_only": PREDICTIONS.exists(),
        "predictions_read": False, "model_inference_run": False, "training_run": False,
        "selection_cap_per_modality": 12, "minimum_to_proceed_per_modality": 10,
        "selection_after_review_only": True,
    }
    (PRIVATE / "preparation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive = SHARING / "rvced_v4_review_offline.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for p in sorted(REVIEW.rglob("*")):
            if p.is_file():
                z.write(p, "rvced_v4_review_offline/" + p.relative_to(REVIEW).as_posix())
    archive_sha = sha(archive)
    (archive.with_suffix(".zip.sha256")).write_text(f"{archive_sha}  {archive.name}\n", encoding="ascii")
    chunk_size = 75 * 1024 * 1024
    with archive.open("rb") as f:
        idx = 1
        while data := f.read(chunk_size):
            (SHARING / f"{archive.name}.{idx:03d}").write_bytes(data)
            idx += 1
    (SHARING / "重新合并压缩包.ps1").write_text(
        "$parts=Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'rvced_v4_review_offline.zip.*' | Where-Object {$_.Name -match '\\.\\d{3}$'} | Sort-Object Name\n"
        "$out=Join-Path $PSScriptRoot 'rvced_v4_review_offline.rebuilt.zip'\n"
        "$dest=[IO.File]::Create($out); try { foreach($p in $parts){$src=[IO.File]::OpenRead($p.FullName); try {$src.CopyTo($dest)} finally {$src.Dispose()}} } finally {$dest.Dispose()}\n"
        "Get-FileHash -Algorithm SHA256 $out\n",
        encoding="utf-8-sig",
    )
    manifest.update({"archive_sha256": archive_sha, "archive_bytes": archive.stat().st_size, "archive_parts": idx - 1})
    (PRIVATE / "preparation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
