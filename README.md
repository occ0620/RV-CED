# RV-CED

逐输入通道匹配替换的情感分析预实验：固定其他通道，将目标通道替换为同极性或反极性供体，比较模型输出响应。

**状态：真实 GPU pilot 已完成；标签规则构造，关系审核待完成。** 本阶段没有训练或 LoRA。

## 实际运行

- 数据：CH-SIMS v2.0 有标签CSV，实际4,403条；4,268条通过全量媒体检查，test可用1,005条。
- 模型：Qwen2.5-Omni-7B，官方文本输出Thinker接口，权重固定 `ae9e1690543ffd5c0221dc27f79834d0294cba00`。
- 单卡A800 80GB；Python3.12.3、Torch2.3.0+cu121、Transformers4.53.3、qwen-omni-utils0.0.4、bf16。
- 同一批30源覆盖T/A/V，每源每模态2组供体，180/180三元组完整；390主输入、429单通道诊断、2代码映射诊断，共821评分任务，主运行失败0。
- 主运行180组非目标通道隔离全部通过，两次重复检查最大候选概率差0。源和供体不重叠，295个不同供体最多复用2次。

| 通道 | mean delta_A | mean delta_B | mean D [95%描述区间] |
|---|---:|---:|---|
| T | 0.135 | 0.260 | 0.125 [0.065,0.199] |
| A | 0.045 | 0.278 | 0.232 [0.153,0.330] |
| V | -0.032 | 0.287 | 0.319 [0.239,0.401] |

`s=p_positive-p_negative`，`d=-sign(y_target)`，`delta_A=d(s_A-s_0)`，`delta_B=d(s_B-s_0)`，`D=delta_B-delta_A`。
先源内平均两组供体，再跨源等权；2,000次按源聚类bootstrap。D不是净因果依赖，排序不是模态依赖排名。

相同30源上V−T的D差0.194，主要伴随A对照差异−0.166；B响应差只有0.027且区间宽。30源中27个标签极性全一致，冲突覆盖有限。
语音保留词义、字幕状态未知。审核和供体稳定性尚不足以验证协议，暂不启动第二模型或训练。

## 代码与说明

- [运行与部署](docs/RUNNING.md)
- [已有缓存复核、效应分解与统计单位](docs/RESULTS.md)
- [人工盲审、离线包与合并](docs/REVIEW.md)
- [相关工作核查](rvced_pilot/evidence/related_work.md)
- [可公开汇总表](rvced_pilot/followup_analysis/tables/)
- [验证与执行来源](docs/PROVENANCE.json)

![A/B/D效应分解](rvced_pilot/followup_analysis/figures/effect_decomposition.png)

## 快速检查

```bash
python -m pip install -r rvced_pilot/requirements-cpu.txt
python -m pytest rvced_pilot/tests -q -p no:tmpdir -p no:cacheprovider
python rvced_pilot/pilot.py --help
```

这是代码、方法和汇总统计发布。数据、原始媒体、模型权重、逐项标签/预测、私有审核映射、访问凭据和离线媒体包保留在本地。
因此克隆仓库可运行单元测试，但无法仅凭仓库恢复原始逐项实验；运行需要自行准备数据/权重，复核原实验需要私有冻结产物。
不要用示例配置冒充已经执行的冻结配置。数据与模型的使用条件以各自官方来源为准；本仓库没有附带原始数据或权重。
审核者只接收专用盲审页面或私下传递的离线包，不应查看分析报告。
