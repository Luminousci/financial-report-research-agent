# 开发计划追溯矩阵

本表将《章程.xlsx》的任务逐项映射到代码、输出和验收证据。状态分为“已验证”“待外部凭据验证”和“需参赛团队补充”。

| 序号 | 计划任务 | 实现位置 | 主要输出/验收证据 | 状态 |
|---:|---|---|---|---|
| 1 | 财报收集 | `FIN_AGENT_DATA_ROOTS`、`cli list` | 受控目录共21份PDF；文件SHA256写入manifest | 已验证 |
| 2 | OCR+文本摘取 | `pdf_parser.py`、`ocr.py`、`verify_live_integrations.ps1` | 原生文本质量评分；PaddleOCR-VL异步任务上传、轮询和JSONL解析；PaddleX兼容模式；真实验收报告 | 已验证；真实服务返回`done`并完成整链路报告 |
| 3 | 文本检索 | `retrieval.py` | `narrative_evidence.json`，含主题、页码、原文和相关度 | 已验证；Embedding为可选增强 |
| 4 | 数据输出结构 | `models.py`、`docs/DATA_SCHEMA.md` | 事实、计算、验证和推论分层存储 | 已验证 |
| 5 | 结构化提取 | `extractor.py` | A股、银行、港股中英文指标；表格优先、文本回退 | 已验证 |
| 6 | 校验 | `validation.py` | 冲突值、披露同比复算、来源缺失、资产负债勾稽 | 已验证 |
| 7 | 财务计算 | `calculations.py`、`timeline.py` | 同比、环比、现金含量、两年累计现金转化、增速差、营运资金代理、非经常性影响 | 已验证 |
| 8 | AI财报分析 | `deepseek.py`、Prompt、Skill、`verify_live_integrations.ps1` | JSON约束输出、Prompt版本、请求哈希、缓存、证据输入；真实响应ID、Token和响应哈希 | 已验证；真实请求及整链路报告均通过 |
| 9 | 财报生成 | `report.py` | 公司信息、指标、变化、异常、原因证据、健康评级、风险边界 | 已验证；包含真实AI解释样例 |
| 10 | 前端 | `webapp.py`、CLI | 本地Web选取财报、OCR/LLM模式、报告查看 | 已验证 |
| 11 | 计划书+视频 | `SUBMISSION_PLAN.md`、`DEMO_SCRIPT.md`、`DEFENSE_QA.md` | 提交清单、演示分镜和答辩问题 | 文稿已完成；团队名称/成员需补充 |
| 技术要求 | MCP | `mcp_server.py`、`MCP_INTEGRATION.md`、`verify_mcp.py` | 官方SDK stdio握手；4个受控工具；路径穿越单测 | 已验证 |

## 比赛技术要求映射

- 大语言模型：DeepSeek Chat Completions，JSON模式；真实调用已验收，无密钥时仍可运行确定性流程。
- 结构化成果：每次运行同时输出JSON、HTML、manifest和JSONL事件日志，不以对话记录作为成果。
- 受控数据环境：Web和CLI只读取配置的数据根目录，不开放任意文件或网络数据访问。
- 可验证、可追溯、可复现：保存SHA256、页码、表格/行、原文、公式输入、Prompt版本和API元数据。
- 完整源代码：`src`、`agent_assets`、`config`、`tests`、`benchmark`、依赖锁定和运行文档均纳入提交包。
- 工具协议：本地MCP服务只暴露受控财报列表、固定分析编排和白名单产物读取。
- 第三方披露：见 `THIRD_PARTY.md`。

## 尚需外部输入

1. 团队名称、成员分工、作品名称及最终演示时长。
2. 可选：真正无文本层的扫描财报，用于扩充OCR边界样本。
