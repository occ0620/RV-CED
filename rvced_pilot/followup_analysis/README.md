# RV-CED follow-up 当前入口

2026-10-16 已确认 v3 的跨片段单通道替换会产生语词、字幕、口型和时间不同步。v3 冻结清单与预测缓存全部保留，但组合输入人工审核停止；它只能作为“跨模态冲突／通道替换探索”，不能支持纯模态因果结论。问题记录见 [protocol_issue_20261016/report.md](protocol_issue_20261016/report.md)，内容对齐可行性见 [v4_alignment_feasibility/report.md](v4_alignment_feasibility/report.md)。

审核者1已经提交的 v3 单通道评分原样保存在 `review_imports/reviewer_01_received_20261016_742403a5814e/`。不要继续填写 v3 的组合输入，也不要回改旧评分。

## v4 推理前盲审结果（2026-10-16）

已接收两份各 35 行的 v4 表。两位审核者的步骤一均完整且相互独立；审核者 1 的步骤二有 1 个空字段，审核者 2 只独立填写了 1/35 个步骤二，另外 34 个没有重复填写。旧版 360 项表只归档，不参与 v4。

冻结规则要求两人都认定目标极性与官方方向一致。步骤一显示这一条件最多只有 T=7/18、V=2/17；步骤二只能继续排除，不能把上限提高到每模态最低 10 条。因此已冻结状态 `PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES`，不要求为本次停止决定补齐步骤二，也不启动 GPU 推理、训练或 LoRA。未填步骤二不能记为双人一致。

接收报告、原文件快照、缺项表和冻结闸门见 [v4 审核接收目录](v4_natural_conflict/review_intake/received_20261016_gate_v2/README.md)。可重复执行的接收脚本为 [audit_received_reviews.py](v4_natural_conflict/audit_received_reviews.py)。

## v4 盲审材料与原定流程

v4 改用原始同步片段中的自然标签冲突，不再拼接供体。候选、审核页面、固定协议和合并脚本位于 [v4_natural_conflict/README.md](v4_natural_conflict/README.md)。严格候选共有 35 个：T 18、V 17、A 0。每位审核者填写同一批 35 个候选，每个候选依次检查目标通道和原片质量。

本机打开：

```text
rvced_pilot/followup_analysis/v4_natural_conflict/reviewer/打开盲审.html
```

发给不在同一网络的同伴：

```text
rvced_pilot/followup_analysis/v4_natural_conflict/sharing/rvced_v4_review_offline.zip
```

包内含原始视频，不随公开 Git 仓库发布；请私下发送完整 ZIP，或发送两个分卷 `.zip.001`、`.zip.002` 和 `重新合并压缩包.ps1`。同伴收到三者后运行脚本，再解压生成的 `rvced_v4_review_offline.rebuilt.zip`。

两位审核者导出各自 CSV 后运行：

```powershell
D:\installpackage\python.exe rvced_pilot/followup_analysis/v4_natural_conflict/merge_reviews.py `
  --reviewer-1 "C:/reviews/reviewer_01.v4.completed.csv" `
  --reviewer-2 "C:/reviews/reviewer_02.v4.completed.csv"
```

只有输出状态为 `READY_FOR_V4_INFERENCE` 才进入 GPU 推理。若为 `PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES`，保留结果并暂停，不放宽标准、不按模型表现补样本。

## 已完成的 v3 缓存分析

缓存复核、效应分解和描述统计仍在 [report.md](report.md)，核验记录在 [evidence/delivery_verification.json](evidence/delivery_verification.json)。这些产物没有新增推理或训练。其行为结论必须和协议缺陷一起阅读，关系审核仍待完成。

复核脚本：

```powershell
python rvced_pilot/followup_analysis/run_analysis.py
python rvced_pilot/followup_analysis/write_report.py
python rvced_pilot/followup_analysis/verify_delivery.py
```
