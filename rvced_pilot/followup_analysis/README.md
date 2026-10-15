# Cache-only follow-up

run_analysis.py复核本地私有runs/main-a800-v3，不进行模型推理。
源级表、逐项预测和审核映射不包含在公开仓库；运行分析须先在本机恢复私有冻结目录。
tables/与figures/只包含已经完成的可公开汇总结果。

详见 [结果](../../docs/RESULTS.md) 与 [审核和合并](../../docs/REVIEW.md)。
prepare_review.py只应在人工评分开始前运行；评分开始后不得修改固定协议。

## 中文选项显示版v2

当前审核页面用serve_review_labeled.py，底层冻结的serve_review.py保持原样。
所有选项显示为“编码＝中文含义”，保存值、项目、旧评分和冻结标准不变。
字段定义核对与统一说明见reviewer/guide_labeled_v2.md；新增说明已单独记录，旧分数不回改。
旧CSV可在新页面恢复，发现与当前非空评分冲突时整体拒绝导入。

```bash
python rvced_pilot/followup_analysis/serve_review_labeled.py --port 8765
```
