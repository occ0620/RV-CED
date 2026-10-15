from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import json

from .common import MODALITIES, ROOT, STATUS, now, read_csv, read_json, read_jsonl


def fmt(value):
    if value is None or value == '':
        return '缺失（未测量）'
    return f'{value:.6g}' if isinstance(value, float) else str(value)


def report(run, c):
    run = Path(run)
    env = read_json(run / 'evidence/environment.json', {})
    inv = read_json(run / 'results/inventory_stats.json', {})
    selection = read_json(run / 'results/selection_stats.json', read_json(run / 'results/selection_stats.main.json', {}))
    state = read_json(run / 'results/analysis_status.json', {})
    frozen = read_json(run / 'manifests/freeze.json', {})
    audit = read_json(run / 'results/audit_summary.json', {})
    usage = read_json(run / 'results/resource_usage.json', {})
    model_usage = read_json(run / 'results/model_usage.json', {})
    media_smoke = read_json(run / 'results/media_smoke_validation.json', {})
    local_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S +08:00')
    failures = read_csv(run / 'results/failures.csv')
    candidate_rows = read_csv(run / 'results/candidate_counts.main.csv') or read_csv(run / 'results/candidate_counts.csv')
    main_pairs = read_jsonl(run / 'manifests/pairs.jsonl')
    proposed = read_jsonl(run / 'manifests/pairs.proposed.jsonl')
    lines = ['# RV-CED pilot 实际执行报告', '', f'生成时间（Asia/Shanghai）：{local_time}。', '',
             f'**{STATUS}。**', '',
             '## 实际完成状态', '',
             f'- 盘点状态：{inv.get("status", "未运行")}；实际观察标签行数 {inv.get("observed_rows", 0)}，媒体可用行数 {inv.get("eligible", "未知")}。',
             '- 若盘点状态为migrated_origin_verified_real_inventory，上述全量媒体检查发生在本地原始数据目录；远程只传入预先选定的冒烟/主pilot片段，其余原始媒体仍在本地，不声称都已上传。迁移清单SHA256和源/供体角色SHA256均核验，参与推理的原始媒体再次检查内容哈希。',
             f'- 清单状态：{frozen.get("status", "未冻结")}；候选三元组 {len(proposed)}，真实冻结三元组 {len(main_pairs)}。',
             f'- 主模型连续结果：完整三元组 {state.get("complete_triplets", 0)}；模型唯一输入 {state.get("unique_model_inputs", 0)}。',
             f'- 真实媒体冒烟：{media_smoke.get("media_only_passed", "未运行")}；每模态检查源数 {media_smoke.get("sources_per_modality", {})}；媒体检查不等于Qwen模型冒烟，真实评分状态另见smoke_validation.json。',
             f'- 人工审核：完整关系评分三元组 {audit.get("relation_assessed_triplets", 0)}；高质量三元组 {audit.get("high_quality_triplets", 0)}。',
             '- 没有训练或 LoRA；程序未购买算力，未提交 Git，未公开上传原始数据或权重。远程运行使用用户提供的租用实例，必要片段经SSH私有传输。', '',
             '真实 Qwen 冒烟与主 pilot 只有在 CUDA、权重、官方处理器及候选打分验证成功后才算完成。'
             '代码单元测试、媒体处理检查和候选清单均不能代替真实模型验证。', '',
             '## 数据来源、字段与限制', '',
             f'标签文件：`{c["dataset"]["labels_path"]}`。原始媒体根目录：`{c["dataset"]["media_root"]}`。', '',
             '使用实际 CSV 字段 mode/text/label_T/label_A/label_V/label；ID 为 video_id/clip_id，'
             '媒体路径由同样两个实际字段构造并逐一检查。speaker_id 和 scene_id 缺失时为 unknown，'
             'video_id 仅作为原视频分组，不当作说话人身份。两份本地 meta.csv 字节相同。', '',
             '本地 CSV 为 4,403 条，而论文描述有标签数据为 4,402 条；按实际文件盘点，不删除一行凑论文数。'
             '原项目 download_manifest.json 记载特征 pickle 与 CSV 有标签差异，故本轮预先选择配套 CSV 标签，'
             '不加载特征 pickle、不混用标签，也不依据 Qwen 输出选择版本。该标签版本差异仍是数据解释限制。', '',
             '原项目文档说明该test集已用于此前E2探索，本轮不声称是新的盲测或未来确认性实验的未见测试集。'
             '已有E2输出不参与本轮供体、阈值、标签版本或样本选择。', '',
             f'实际媒体排除 {inv.get("observed_rows", 0) - inv.get("eligible", 0)} 条；本地盘点的135条均因实际解码帧数与容器标记不一致，'
             '其中test=29、train=97、valid=9；test媒体可用1005/1034。原始文件不修改。'
             'T/A/V及整体标签均为4403/4403可用，按τ=0.2得到全量负/中/正分布：'
             'T=1654/1385/1364，A=1649/1456/1298，V=1615/1378/1410，整体=1818/1094/1491。'
             '细节与哈希见 inventory_stats.json、evidence/data_audit.json 和 inventory.csv。', '',
             f'标签变换：{c["dataset"]["label_scale"]}；倍率 {c["dataset"]["label_multiplier"]}，偏置 {c["dataset"]["label_offset"]}。', '',
             '数据来源：[官方仓库](https://github.com/thuiar/ch-sims-v2) 提供有标签/无标签数据的 Google Drive 与百度网盘入口；'
             '[官方主页](https://thuiar.github.io/sims.github.io/chsims) 说明提供原始视频、特征和含 ID/划分/标签的 CSV。'
             '可见页面未确认完整数据使用许可；本地记录为用户下载，使用条款状态 unconfirmed，未用第三方镜像许可证代替原条款。', '',
             '[CH-SIMS v2 §3.2](https://arxiv.org/html/2209.02604v1#S3.SS2) 说明音频标注时模糊语词、视觉标注时去除声音与文字。'
             '本轮未证明原始音轨经过相同处理：音频输入保留完整语音；字幕状态没有可靠元数据时 unknown。'
             '八帧预览不足以全面验证唇音同步，相关人工评分应保留未知与说明。', '',
             '## 产出率与预算', '',
             '| 模态 | test 强度达标源 | 满足局部独立供体条件源 | 全局严格分配源 | 目标源 |',
             '|---|---:|---:|---:|---:|']
    for m in MODALITIES:
        cand = [r for r in candidate_rows if r['modality'] == m]
        eligible = sum(str(r['source_strength_eligible']).lower() == 'true' for r in cand)
        feasible = sum(str(r['independent_groups_feasible']).lower() == 'true' for r in cand)
        selected = selection.get('source_counts', {}).get(m)
        lines.append(f'| {m} | {eligible if cand else "未测"} | {feasible if cand else "未测"} | {fmt(selected)} | {c["selection"]["sources_per_modality"]} |')
    lines += ['', '局部可行数量未施加全局供体复用和源/供体不重叠约束；严格分配采用确定性启发式，'
              '不是最大基数求解器。因此分配不足不自动证明不存在更大可行匹配。所有阈值保持预设值。', '',
              f'实际严格候选源数：`{json.dumps(selection.get("source_counts", {}), ensure_ascii=False)}`。'
              f'共有三模态源数：{len(selection.get("common_sources", []))}。', '',
              '满额主 pilot 原始输入最多 90，加 360 编辑输入，主评估上界 450；若三模态共有 30 源则为 390。'
              '单模态源/供体检查最多 450；冒烟最多 18 主输入 + 18 单模态输入；少量代码映射与重复诊断另列。'
              '共享账本限制主模型最多 1,100 个唯一评分输入，优先保留原始/A/B；候选序列前向次数独立记录。', '',
              f'本次候选清单预计主评估输入数：{frozen.get("estimated_main_unique_inputs", frozen.get("unique_main_inputs", "未产生"))}。'
              '该数字是清单预算，不是已经完成的推理数。', '',
              '## 匹配、隔离、冻结与审核', '',
              '固定构造种子 20261015，bootstrap 种子 20261016。源强度≥0.4，标签强度差≤0.2；'
              '时长比[0.8,1.25]且A/B比≤1.15；字符数比[0.5,2]且A/B比≤1.5。'
              '供体全局最多使用两次，单元两组供体不共享，源与供体集合不重叠。身份/原视频/场景状态必须对称，未知单列。', '',
              f'实际复用分布：`{json.dumps(selection.get("donor_reuse_histogram", {}), ensure_ascii=False)}`。'
              f'源/供体重叠：`{selection.get("source_donor_overlap", [])}`。', '',
              '实际匹配变量均值、差异、标准化差异与源相对比见 results/matching_balance.csv。'
              '说话人和场景等缺失字段见各三元组match_missingness，unknown不当作已验证匹配。'
              '这些描述量不能单独证明所有非情感因素平衡。', '',
              '音轨解码为处理器要求的 16kHz 单声道 float32，不作逐片段音量归一化、变速、拉伸或强制截断；'
              '视频均匀取八个不同解码帧，保持原比例。模型接收独立文本、数组音频、无声帧列表，'
              '官方预处理和模型前向均设置 use_audio_in_video=false。保存处理内容哈希，'
              '推理后还核对非目标音频/视频处理器张量哈希。', '',
              '未满足真实冒烟门槛时只保存 pairs.proposed.jsonl、处理缓存和候选配置校验和；freeze.json 标记 blocked_not_frozen，'
              '不能将其当作已冻结主 pilot。真实冻结文件包括清单、输入、配置、提示、权重指纹和盲审选择；'
              '修改配置或代码后必须使用新运行目录与修订原因，不覆盖旧结果。', '',
              '盲审选择在预测前按种子固定，每模态最多12个单元，页面和评分表隐藏A/B、标签和预测；'
              '私有映射保存在 key.private.json。两位人工独立评分，不由模型冒充。'
              '高质量规则要求关系通过、无处理错误、A/B身份和场景变化对称；未知不当作通过。', '',
              f'当前人工审核：极性一致率 {fmt(audit.get("polarity_agreement"))}；kappa {fmt(audit.get("cohen_kappa"))}；'
              f'关系通过率 {fmt(audit.get("relation_pass_rate"))}。', '',
              '## 原始/A/B 分数与主指标', '',
              'p 为固定候选答案内归一化分数，不是校准后的情感概率；s=p_+−p_-。d=−sign(y_m)，'
              'delta_A=d(s_A−s_0)，delta_B=d(s_B−s_0)，D=delta_B−delta_A，称为“匹配方向差值”。'
              '候选代码必须在真实聊天模板的助手起点验证为单个下一 token；否则新版本整次统一使用序列对数似然，不混用。', '',
              '| 模态 | 完整源 | s_original | s_A | s_B | IR_A | mean delta_B | B正向比例 | mean D |',
              '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    summary = read_csv(run / 'results/summary.csv')
    for m in MODALITIES:
        vals = {r['metric']: r for r in summary if r['modality'] == m}
        count = next(iter(vals.values()), {}).get('unique_complete_sources', '0')
        line = [m, count] + [fmt(vals.get(k, {}).get('value')) for k in ('s_original', 's_a', 's_b', 'IR_A', 'delta_B', 'B_positive', 'D')]
        lines.append('| ' + ' | '.join(map(str, line)) + ' |')
    lines += ['', '| 模态 | mean delta_A | mean absolute A shift | B零偏移比例 | B负偏移比例 | mean D的95%描述区间 |',
              '|---|---:|---:|---:|---:|---|']
    for m in MODALITIES:
        vals = {r['metric']: r for r in summary if r['modality'] == m}
        drow = vals.get('D', {})
        lines.append('| ' + ' | '.join([m, *[fmt(vals.get(k, {}).get('value')) for k in ('delta_A', 'abs_shift_A', 'B_zero', 'B_negative')],
                                      '[' + fmt(drow.get('ci_low')) + ', ' + fmt(drow.get('ci_high')) + ']']) + ' |')
    lines += ['', '缺失没有填为零。所有主连续比较要求原始/A/B完整；失败分母和意向样本数见 summary.csv。'
              '每个 source×modality 先平均供体组，再跨源等权；2,000 次 source cluster bootstrap 保留全部模态与供体组。'
              '少于10源仅报告可行性与个例。未对编辑输入继承原始整体标签计算准确率。', '',
              f'D>0 且 delta_B<0 的完整三元组：{state.get("positive_D_negative_delta_B", {}).get("count", "未测") }；'
              'D>0 不代表B沿预期方向移动，更不等于扣净伪影的因果效应。', '',
              '## 独立供体、共有源与辅助诊断', '',
              '两组供体的 delta_B 方向一致率、D符号一致率与幅度差保存在 donor_stability_summary.json；'
              '仅供体各使用一次和已审核高质量子集保存在 subsets.json。没有真实评分时全部连续量缺失。'
              '供体复用相关性不声称已经被源 bootstrap 完全处理。', '',
              '模态差优先在共有源内配对 bootstrap，见 common_source_comparisons.csv；非共有源只作探索，不宣布排名。'
              '高层原视频/说话人敏感性仅使用真实元数据，少于10可靠簇不估计。'
              '单模态检查、答案映射交换与重复评分只用于诊断，不用于重新挑供体或主样本。', '',
              '## 资源与失败记录', '',
              '关键逻辑测试记录见 evidence/unit-tests.final.txt/XML；早期临时目录ACL错误的日志单独保留。'
              '单元测试的合成夹具仅位于 tests/.runtime，不计入真实结果。', '',
              f'环境：Python `{env.get("python", "未记录")}`；GPU `{env.get("gpus", [])}`；CUDA `{env.get("cuda_available", False)}`。', '',
              f'序列前向次数 {model_usage.get("sequence_forwards", 0)}；GPU前向墙钟秒 {model_usage.get("gpu_forward_seconds", 0)}；'
              f'峰值已分配显存字节 {model_usage.get("peak_allocated_bytes", 0)}；'
              f'峰值保留显存字节 {model_usage.get("peak_reserved_bytes", 0)}。GPU能耗与实际费用未测。', '',
              f'阶段累计墙钟秒：{sum(e.get("wall_seconds", 0) for e in usage.get("events", [])):.3f}；'
              f'CPU进程秒：{sum(e.get("cpu_seconds", 0) for e in usage.get("events", [])):.3f}。'
              '早期三次中断的开发盘点未被最终阶段日志计时，因此该墙钟累计是本次会话消耗的下界，'
              '不是完整会话耗时。外部FFmpeg进程CPU未计入Python进程CPU；GPU前向时间不包含模型加载和CPU预处理。', '',
              f'本地缓存 {fmt(usage.get("cache_bytes"))} 字节；已缓存片段 {usage.get("input_clips_cached", "未记录")}。' +
              ('GPU推理前向尚未成功执行，模型效应与吞吐缺失。' if not model_usage.get('sequence_forwards') else
               'GPU前向计数与时间为真实运行记录；处理器、依赖、权重及缓存指纹见冻结清单。'), '',
              f'失败事件记录 {len(failures)} 条（含重复尝试和环境阻塞，不能当作样本失败率）；详情见 results/failures.csv。', '',
              '具体阻塞：', '']
    for blocker in env.get('blockers', []):
        lines.append('- ' + blocker)
    for reason in frozen.get('reasons', []):
        lines.append('- ' + reason)
    literature = ROOT / 'evidence/related_work.md'
    lines += ['', '## 相关工作核查', '', literature.read_text(encoding='utf-8') if literature.exists() else '相关工作尚未记录。', '',
              '## 结论边界与继续建议', '',
              '当前不能宣布协议有效、模型不依赖某模态或任何模态排名。完整语音包含语词，画面可能包含字幕；'
              '只能在验证条件下讨论输入通道替换响应。模型没有变化也不能证明未使用该通道。'
              '样本小或区间包含零不等于无效应，区间不含零也不能单独验证协议。', '',
              ('主pilot技术推理与完整三元组统计已运行；下一步完成人工关系审核，暂不进入训练。' if state.get('complete_triplets') else
               '建议继续技术验证与人工关系审核，暂不进入训练。先通过真实冒烟，再冻结主清单并运行主pilot。') +
              '人工表补齐后只重算统计，不需重跑模型。第二模型VITA-1.5-8B尚未运行，仅在已有环境可用时复用冻结子清单；'
              '跨模型新增诊断主张保持未检验。关系失败最多做一轮基于人工审核和数据条件的版本修订，不按模型效果调参。', '',
              '## 继续执行', '',
              '项目内 README.md 和 GPU_SETUP.md 提供本地检查、Linux GPU环境安装、数据路径修改、'
              '真实冒烟、主冻结、断点续跑及人工评分重算命令。数据和模型路径变更时在新目录重新生成输入缓存，'
              '不能直接改写已经冻结的路径或搬动冻结输入后忽略校验。', '',
              '图：', '',
              '![effect distributions](figures/effect_distributions.png)', '',
              '![independent donors](figures/donor_stability.png)', '',
              '![matching balance](figures/matching_balance.png)', '']
    (run / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
