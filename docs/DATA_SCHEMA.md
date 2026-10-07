# 数据与证据结构

核心事实字段包括：

- `metric_code`：稳定的标准指标编码；
- `metric_name`：展示名称；
- `value`、`unit`、`unit_scale`、`currency`：原始数值和标准化尺度；
- `period_label`、`period_type`：单季度、累计、年度或时点；
- `comparison_kind`：本期值、上年同期值或披露同比；
- `statement_scope`：合并、母公司或未知；
- `restated`：是否追溯调整；
- `source`：文件ID、页码、表格序号、行号、原始标签和值、证据文本、提取方式和置信度。

计算结果必须保留公式和全部输入；规则发现必须保存适用行业、证据、反向核验条件和严重程度。

补充输出：

- `business_metrics`：业务更新公告中的区间型经营指标，保留指标编码、业务范围、上下限、同比口径、期间、页码和OCR原文，不擅自取中点；
- `nonrecurring_items`：项目名称、单季/累计金额、单位、页码和原始行；
- `narrative_evidence`：主题、相关度、页码、原文和提取方式；
- `health_assessment`：行业配置、总分、等级、状态、覆盖率、方法、评分项和局限；
- `calculated_metrics`中的现金流和营运资金指标均保存公式、输入、期间和不可用原因。
- `agent_outputs.json`：保存业绩、非经常性损益、会计口径、现金流背离、其他风险、评级和报告编辑Agent的独立结构化输出；失败Agent以显式状态与回退文本保留，不覆盖程序结果。
- `llm_metadata.json`：保存编排摘要及每个Agent的角色、请求/响应哈希、模型、Token、耗时、缓存和状态。

页面展示压缩为两页研报，不直接呈现来源附录；页码、原文、公式、请求哈希和Agent执行轨迹仍保存在上述JSON及`events.jsonl`中，可供复核与复现。

页面将“原生文本率”与“OCR覆盖率”分开展示：前者表示PDF原有文本层质量，后者表示本次有多少页通过OCR补全。

## 报告问答记录

`chat/<session_id>.jsonl`每行是一轮不可覆盖的会话记录，包含：

- `run_id`、`session_id`、`trace_id`和UTC时间；
- `question`及用于承接追问的`search_question`；
- `answer`和`mode`（`deepseek`或`retrieval_fallback`）；
- `citations`：证据ID、类型、标签、页码、文件名、原文及检索分数；
- `retrieval_hash`、脱敏模型调用元数据和总耗时。

问答引用可来自财报原文页块、结构化事实、经营指标、程序计算、非经常性损益、异常规则
和叙事证据。DeepSeek只负责对已经检索出的财报证据进行组织解释，不拥有任意文件访问能力。
