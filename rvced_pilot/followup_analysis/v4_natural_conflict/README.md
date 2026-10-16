# v4 自然冲突小规模实验：审核与冻结阶段

v4 使用每个原始片段自身同步的 T/A/V，不再跨片段拼接。严格自然冲突候选由 `tau=0.2` 的数据标签关系预先确定：T 目标与 A、V 方向相反且 A=V；或 V 目标与 T、A 方向相反且 T=A。A 严格候选为 0，因此本轮不放宽定义。

冻结后只做一项配对检验：T 候选比较完整同步 TAV 与 AV-only；V 候选比较完整同步 TAV 与 TA-only。主量为 `E_m = sign(y_m) × [s(full) − s(without target)]`，正值表示加入目标通道后预测向目标标签移动。最多 24 个来源、48 个唯一主输入；这不是多轮消融，也不解释为供体替换的因果效应。

当前阶段没有读取新旧模型预测，也没有运行模型推理。先让两位审核者分别打开 `reviewer/打开盲审.html`，选择各自编号并填写，导出：

- `reviewer_01.v4.completed.csv`
- `reviewer_02.v4.completed.csv`

审核完成后运行：

```powershell
D:\installpackage\python.exe rvced_pilot/followup_analysis/v4_natural_conflict/merge_reviews.py --reviewer-1 <审核者1CSV> --reviewer-2 <审核者2CSV>
```

脚本会校验完整性、保存独立评分与分歧、按预定规则生成最多 12 个 T 和 12 个 V 的冻结清单。若任一模态少于 10 个合格候选，状态写为 `PAUSE_INFERENCE_INSUFFICIENT_REVIEWED_SOURCES`，不得自行放宽标准。

离线包在 `sharing/rvced_v4_review_offline.zip`。包内含原始视频，不随公开 Git 仓库发布；请通过私下文件传输交给审核者。本地若需分卷传递，可使用同目录的小于 80 MiB 分卷和 `重新合并压缩包.ps1`。媒体仅限项目审核者使用。
