# 金融投研智能体提交内容与复现入口

本目录是“上市公司财务报告分析”赛道的完整源代码提交，不包含真实 API Key、访问令牌、第三方模型权重或商业软件源代码。评审方可按 `docs/金融投研智能体运行说明.docx` 在指定 Windows 环境中复现核心流程和输出结果。

评审人员可先双击 `本地运行入口.html` 查看全部配置和复现入口，再双击 `启动本地分析台.cmd` 启动服务。首次运行会自动创建隔离环境并安装锁定依赖；CMD 窗口会保留运行信息，便于定位启动问题。

## 一 提交内容映射

| 竞赛要求 | 主要位置 | 内容 |
|---|---|---|
| 智能体编排框架 | `src/fin_agent/pipeline.py`、`src/fin_agent/deepseek.py` | 固定流程编排；5个专项Agent并行，评级Agent和报告编辑Agent串行；失败隔离与缓存 |
| Tool | `src/fin_agent/ocr.py`、`pdf_parser.py`、`extractor.py`、`calculations.py`、`retrieval.py`、`validation.py`、`anomaly.py` | OCR、PDF解析、字段提取、程序计算、证据检索、逻辑校验和异常识别工具 |
| Prompt | `agent_assets/prompts/` | 系统Prompt、5个专项Agent、评级、报告编辑、编排和证据问答Prompt |
| Skill | `agent_assets/skills/financial_report_analysis/SKILL.md` | 财报分析任务顺序、边界、失败处理和审计要求 |
| MCP | `src/fin_agent/mcp_server.py`、`docs/MCP_INTEGRATION.md` | 受控stdio MCP服务、工具发现、财报分析和白名单产物读取 |
| 数据处理 | `src/fin_agent/models.py`、`document.py`、`business_update.py`、`bundle_io.py`、`config/` | 数据模型、文档分类、结构化字段、业务更新边界、指标字典和规则配置 |
| 日志记录 | `src/fin_agent/audit.py`、每次运行的`manifest.json`、`events.jsonl`、`llm_metadata.json` | 文件哈希、步骤状态、工具调用、模型调用哈希、Token、耗时和错误状态 |
| 结构化成果 | `sample_outputs/` | 脱敏示例HTML、JSON、manifest、日志和API调用元数据 |
| 依赖文件 | `requirements.txt`、`requirements-lock.txt`、`pyproject.toml` | 兼容依赖范围、完全锁定版本和项目元数据 |
| 运行说明 | `docs/金融投研智能体运行说明.docx`、`README.md` | 安装、配置、运行、Web、MCP、日志定位、验收和故障处理 |
| 本地入口 | `本地运行入口.html`、`启动本地分析台.cmd` | 查看配置并一键启动本地分析台 |
| 财报材料 | `财报数据/README.md`、`财报数据/示例/` | 规范命名说明和三份可直接选择的示例财报 |
| 第三方披露 | `THIRD_PARTY.md` | 名称、版本、来源、许可证、调用方式和使用范围 |
| 测试与评测 | `tests/`、`benchmark/`、`docs/EVALUATION.md` | 单元测试、人工金标准、评测方法和适用边界 |

## 二 最短复现路径

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
$env:PYTHONPATH = "$PWD\src"
python -m fin_agent.cli doctor
python -m pytest -q
python -m fin_agent.cli analyze --input "受控财报路径.pdf" --ocr never --llm never
```

联网模式需先将 `.env.example` 复制为 `.env`，再填入评审方有权使用的 DeepSeek 和 AI Studio 凭据。`.env` 不得提交。

## 三 复现验收点

1. `doctor` 能报告依赖、数据根目录、OCR和DeepSeek的脱敏就绪状态。
2. 离线模式无需外部API即可完成文档解析、字段提取、程序计算、校验、评级和报告生成。
3. 联网模式产生每个Agent的独立结构化输出以及请求/响应哈希、模型、Token和耗时记录。
4. 每次运行生成独立目录，至少包含 `analysis_bundle.json`、`manifest.json`、`events.jsonl` 和 `report.html`。
5. 页面报告不展示冗长来源附录，但完整页码、原文、公式和执行轨迹保留在JSON与日志中。
6. `scripts/verify_release.ps1`、`verify_full_corpus.ps1` 和 `verify_submission_package.ps1` 分别用于发布、案例库和提交包验收。

## 四 自主开发与第三方边界

智能体编排、数据模型、字段标准化、确定性计算、校验规则、异常规则、健康评分、报告模板、Web交互、MCP封装和审计日志为本项目实现。第三方库只承担通用运行、PDF解析、图像处理、HTTP访问和MCP协议能力；DeepSeek与PaddleOCR通过外部API调用，模型权重和商业服务源代码均未包含在提交物中。详细边界见 `THIRD_PARTY.md`。
