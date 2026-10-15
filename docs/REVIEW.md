# 人工盲审与合并

**关系审核待完成。** 审核者独立填写，不读取模型输出、标签或私有映射。
代码区包含工具与指南，完整盲审媒体包在[Releases](https://github.com/occ0620/RV-CED/releases/tag/pilot-blind-review-v1)提供；私有映射、模型结果和已填写评分表不公开。原冻结材料仍在本地保留。

审核者2直接[下载完整ZIP](https://github.com/occ0620/RV-CED/releases/download/pilot-blind-review-v1/reviewer_02_offline_v1.zip)，完整解压后打开“打开盲审.html”即可评分。

## 中文选项显示版v2（评分编码不变）

已有v1媒体时，先导出旧CSV，再[下载小补丁](https://github.com/occ0620/RV-CED/releases/download/pilot-blind-review-ui-v2/reviewer_02_options_patch_v2.zip)；首次使用直接[下载完整v2](https://github.com/occ0620/RV-CED/releases/download/pilot-blind-review-ui-v2/reviewer_02_offline_v2.zip)。
选项含中文等级说明，旧评分可以导入，冲突不覆盖；同一项目、媒体和原关系/质量标准保持不变。
核对中发现的强度相邻档说明、信心解释和空白/unknown/na区别，已在[统一字段说明](../rvced_pilot/followup_analysis/reviewer/guide_labeled_v2.md)单独记录。
原v1和其字段指南仍保留，旧分数不自动重编码。

## 组织者操作

恢复私有运行及followup_analysis/reviewer/items.json、private/media_map.json、固定协议后：

```bash
python rvced_pilot/followup_analysis/serve_review_labeled.py --port 8765
```

127.0.0.1仅在运行服务的电脑上可用，不能把该链接发给异地同学。
第二位审核者不在同一网络时，组织者可从私有材料制作离线包：

```bash
python rvced_pilot/followup_analysis/build_offline_review.py
python rvced_pilot/followup_analysis/verify_offline_review.py
```

完整sharing/reviewer_02_offline_v1.zip已上传为[Release资产](https://github.com/occ0620/RV-CED/releases/download/pilot-blind-review-v1/reviewer_02_offline_v1.zip)，并提供[SHA256校验](https://github.com/occ0620/RV-CED/releases/download/pilot-blind-review-v1/reviewer_02_offline_v1.zip.sha256)。该大文件单独下载，不放入Git提交。解压整个包，双击“打开盲审.html”，无需Python。
页面固定reviewer_02，内嵌同一360个盲审项目，用相对路径访问657个媒体文件；不包含模型结果、数据集标签或私有映射。
完成后导出reviewer_02.completed.csv交组织者。浏览器临时保存不替代CSV备份。

## 两步评分

1. 独立通道：极性、强度、信心、清晰度；音频词义是否可懂，视频字幕是否存在且可读。
2. 组合输入：处理错误、语词冲突、字幕泄露、身份/场景变化和同步问题；不确定用unknown。

量表见[审核者指南](../rvced_pilot/followup_analysis/reviewer/guide.md)。音频是完整语音；8帧不足以充分验证唇音同步。
页面没有原始参照，无法判断相对身份/场景变化就填unknown，不猜成0。

360行来自36源×模态单元（26不同源）、72三元组；每单元5对象×2阶段。
通道180行只有177种内容，组合180行只有170种内容。重复展示不是独立证据。

## 合并与缓存重算

```bash
python rvced_pilot/followup_analysis/merge_reviews.py --reviewer-1 /path/to/reviewer_01.completed.csv --reviewer-2 /path/to/reviewer_02.completed.csv --out rvced_pilot/followup_analysis/review_imports/human-v1
```

输出必须是新的目录。程序保留两份原表、校验错误或完整合并表、分歧、重复展示一致性、展示/去重加权一致性与kappa、问题已知分母及关系通过率。
缺评/非法值退出2，不插补、不生成通过状态。实际没有人工评分时，不用模型评分代替。
主高质量标准沿用冻结规则：两位审核者均通过同/反极性、两两强度差≤1、信心≥2，两个编辑均无处理错误、身份与场景变化分别已知且对称。
**不要求被测模型单模态判断正确。** 模型一致性仅作单独诊断；新增清晰度/词义/字幕字段为事后描述，不改变冻结标准。
从已有预测缓存重算全部180、已抽审72、关系通过和高质量范围，先平均每源通过的组再跨源等权，报告1组/2组源数；<10源只报数量。
未抽审108个仍为待审核，不将72个审核结果推广为全部180。后续人工裁定另存版本，不覆盖独立评分。
