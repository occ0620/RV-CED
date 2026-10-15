# Cache-only follow-up

run_analysis.py复核本地私有runs/main-a800-v3，不进行模型推理。
源级表、逐项预测和审核映射不包含在公开仓库；运行分析须先在本机恢复私有冻结目录。
tables/与figures/只包含已经完成的可公开汇总结果。

详见 [结果](../../docs/RESULTS.md) 与 [审核和合并](../../docs/REVIEW.md)。
prepare_review.py只应在人工评分开始前运行；评分开始后不得修改固定协议。
