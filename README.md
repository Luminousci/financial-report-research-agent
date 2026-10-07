# 金融投研智能体：上市公司财报分析

这是一个证据驱动、程序化计算、可追溯和可复现的财报分析系统。核心流程为：

```text
文件登记与哈希 → 文档分类 → 原生解析/OCR回退 → 表格提取
→ 文本回退与叙事证据检索 → 期间与单位标准化 → 程序化计算 → 勾稽与冲突校验
→ 异常规则 → 专项分析Agent并行执行 → 评级Agent → 报告编辑Agent
→ 两页可视化HTML/PDF研报、结构化JSON与审计日志
```

## 已实现能力

- 自动登记文件SHA256、公司、报告类型、会计准则和行业配置；
- 原生PDF文本质量评分，低质量页面才进入OCR；
- 支持AI Studio部署服务和百度智能云OCR两种鉴权；
- 从A股、银行及港股中英文披露提取核心指标，并保存页码、表格、行及原文；
- 单独提取非经常性损益明细，并检索业绩驱动、现金流、会计口径和风险叙事证据；
- 支持单季度、累计、年度及时点数据的期间语义；
- 支持跨Q1、H1、Q3、年报推导Q2/Q4单季度值及环比；
- 程序计算同比、现金含量和非经常性损益影响；
- 程序计算利润/现金流增速差、两年累计现金转化和营运资金占用代理；
- 对普通工商企业和银行采用不同规则边界；
- 输出透明财务健康评级，逐项展示观测值、权重、阈值和覆盖率；
- DeepSeek采用“5个专项Agent并行 → 评级Agent → 报告编辑Agent”的受控编排；每个调用独立记录Prompt版本、输入/响应哈希、Token、耗时、状态及缓存；
- 单次模型上下文按任务裁剪，程序负责全部数值计算；任一专项Agent失败时保留其他结果并输出明确回退状态；
- 报告页内置多轮“证据问答”，混合检索全文页块、结构化事实、程序计算和异常规则；
- 问答回答返回页码与原文，DeepSeek不可用时自动切换为纯检索模式，会话和检索过程写入JSONL审计记录；
- 每次运行输出结构化JSON、最多两页的可视化HTML研报、manifest和JSONL事件日志；
- 内置人工金标准和回归评测入口；
- 提供受控本地stdio MCP服务，支持工具发现、财报分析和白名单产物读取；
- 不依赖前端框架的本地 Web 界面；当前“麦穗终端”主题采用一次性随机种子生成并固化，设计参数见 `config/frontend_design.json`。
- Web分析请求采用“快速提交后台任务 → 独立进度页轮询 → 完成后替换跳转”，避免Cloudflare因长连接等待产生524超时。
- Web分析台支持从本地文件夹选择PDF，按“股票代码_公司简称_报告期_报告类型.pdf”规范复制到项目内受控目录并立即加入下拉列表。

## 1. 安装

```powershell
cd E:\金融人工智能\fin_research_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
python -m fin_agent.cli doctor
```

也可以用已经包含PDF依赖的Python运行，但正式提交应按 `requirements.txt` 新建干净环境验证。
评审复现优先使用完全锁定的 `requirements-lock.txt`。

## 2. 密钥文件格式

在项目根目录复制 `.env.example` 为 `.env`。`.env` 是UTF-8纯文本文件，文件名就叫 `.env`，不要命名为 `apikey.txt` 或 `.env.txt`。

DeepSeek至少填写：

```dotenv
DEEPSEEK_API_KEY=你的DeepSeek密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

### AI Studio OCR

根据当前PaddleOCR-VL Jobs API调用样例填写：

```dotenv
OCR_PROVIDER=aistudio
AISTUDIO_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
AISTUDIO_ACCESS_TOKEN=AI Studio访问令牌
AISTUDIO_AUTH_SCHEME=bearer
AISTUDIO_REQUEST_STYLE=paddleocr_job
AISTUDIO_MODEL=PaddleOCR-VL-1.6
AISTUDIO_POLL_INTERVAL_SECONDS=5
AISTUDIO_POLL_TIMEOUT_SECONDS=1800
AISTUDIO_TIMEOUT_SECONDS=180
```

适配器使用`requests`执行multipart上传，随后轮询`jobs/{jobId}`，下载结果JSONL并
合并`layoutParsingResults[].markdown.text`。每次调用记录任务ID、模型、轮询次数、
请求/响应哈希和耗时，但不记录Token或带签名的结果URL。旧的同步PaddleX请求方式
仍保留为兼容选项，但当前比赛配置应使用`paddleocr_job`。

### 百度智能云OCR

如果使用百度智能云“通用文字识别高精度版”，填写：

```dotenv
OCR_PROVIDER=baidu_cloud
BAIDU_OCR_API_KEY=你的API Key
BAIDU_OCR_SECRET_KEY=你的Secret Key
```

两种OCR方式二选一即可。真实 `.env` 已加入 `.gitignore`，运行日志只记录 `configured/missing`，不会记录密钥。

当前比赛环境已按上述格式完成真实联调：AI Studio OCR与DeepSeek均返回成功，且已
通过从`.env`读取真实秘密值后的运行目录泄漏扫描。评审复现时只需替换为其授权凭据。

### 财报文件导入

项目内置受控目录 `财报数据/`，并提供三份规范命名的示例财报。Web分析台把入口合并为“选择示例”和“从本地选择文件”两种来源。本地选择PDF后，系统立即读取前8页，自动回填股票代码、公司简称、报告期和报告类型，同时显示识别置信度及来源页码。用户可以修改识别结果，再点击“确认并使用”。系统会把文件复制到 `财报数据/本地导入/` 并立即选中，不会改动原始文件；导入接口还会对缺失字段执行一次服务端识别兜底。扫描型PDF若没有可提取文本，页面会提示需要OCR并要求人工核对字段。

统一命名格式为：

```text
股票代码_公司简称_报告期_报告类型.pdf
```

例如 `600519_贵州茅台_2025FY_年度报告.pdf`、`601398_工商银行_2025H1_半年度报告.pdf`、`000651_格力电器_2026Q1_第一季度报告.pdf`。也可以手动将按此规则命名的PDF复制到 `财报数据/` 的任意子目录，刷新页面后选择。完整说明见 `财报数据/README.md`。

## 3. 运行

Windows 本地启动方式：双击根目录的 `启动本地分析台.cmd`。脚本会自动寻找 Python 3.10 及以上版本（包括常见 Anaconda/Miniconda 安装位置）、创建 `.venv`、安装锁定依赖并打开分析台。联网模式会读取根目录 `.env`；未配置或不调用 DeepSeek API 时无法生成详细分析报告。使用期间请保持 CMD 窗口开启。

以下命令仅供开发和自动化验收使用，普通用户不需要执行。

检查环境和21份案例：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m fin_agent.cli doctor
python -m fin_agent.cli list
```

不使用外部API进行确定性分析：

```powershell
python -m fin_agent.cli analyze `
  --input "..\年报\贵州茅台年报\第三季度报告.pdf" `
  --ocr never --llm never
```

启用已配置的OCR和DeepSeek：

```powershell
python -m fin_agent.cli analyze `
  --input "..\财报分析\赛力斯：2026年半年度报告.pdf" `
  --ocr auto --llm auto
```

放置真实`.env`后，先用一条命令完成最小化联网验收：

```powershell
.\scripts\verify_live_integrations.ps1 -PythonExecutable ".\.venv\Scripts\python.exe" `
  -PdfPath "..\财报分析\赛力斯：2026年半年度报告.pdf" -Page 1
```

该命令只向DeepSeek发送一个低Token的JSON测试请求，并只向AI Studio发送指定的
单页图像。验收结果写入`outputs/integration_check_*/integration_check.json`，包含
响应ID、模型、Token用量、请求哈希、OCR字符数和源文件SHA256，但不记录密钥。

启动本地Web界面：

```powershell
python -m fin_agent.cli serve --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`。打开任一历史报告后，点击右下角“证据问答”即可
针对当前财报继续提问。网页与问答接口同源运行，无需另开Streamlit或FastAPI端口。

现场展示可使用一键脚本，脚本会先检查环境并自动打开浏览器：

```powershell
.\scripts\start_demo.ps1
```

网络不稳定时使用`.\scripts\start_demo.ps1 -Offline`。该模式在服务端强制关闭
OCR和DeepSeek，不能通过修改网页表单绕过；报告页问答仍可使用纯检索证据模式。
Web状态接口为`/health`，只返回脱敏状态。

临时通过互联网分享页面时，使用带随机密码保护的 Cloudflare Quick Tunnel：

```powershell
winget install --id Cloudflare.cloudflared --exact
.\scripts\start_cloudflare_demo.ps1 -Offline
```

脚本会显示临时 `https://*.trycloudflare.com` 地址、用户名和随机密码；窗口保持打开时
链接有效，按 `Ctrl+C` 会同时关闭隧道和本地服务。确认离线演示无误后，去掉
`-Offline` 即可启用已配置的 OCR 与 DeepSeek。完整说明见
`docs/CLOUDFLARE_QUICK_TUNNEL.md`。

启动MCP服务：

```powershell
python -m fin_agent.mcp_server
```

MCP宿主配置及安全边界见`docs/MCP_INTEGRATION.md`。发布校验会通过官方SDK完成
stdio协议握手、工具枚举和`financial_agent_status`调用。

串联一个目录中的多期报告并生成公司时间轴：

```powershell
python -m fin_agent.cli timeline `
  --input-dir "..\年报\贵州茅台年报" --ocr never
```

## 4. 回归评测

```powershell
python -m fin_agent.cli benchmark
```

基准包含贵州茅台2025年三季报、工商银行2025年报和泡泡玛特2025年报，覆盖A股非金融、银行及港股/IFRS三类模板。当前三例人工金标准的字段准确率、异常召回率和来源覆盖率均为100%。当前44项单元测试覆盖计算、抽取、来源链、AI Studio异步任务、DeepSeek多Agent编排基础能力、外部API适配、MCP路径边界、问答检索与会话审计、失败运行审计、Web后台任务状态、离线模式、重复滚动动画及两页研报。继续比赛开发时，应扩充盲测金标准，而不是用模型输出作为标准答案。

完整案例库回归：

```powershell
.\scripts\verify_full_corpus.ps1 -PythonExecutable ".\.venv\Scripts\python.exe"
```

该命令对受控目录内全部PDF运行离线确定性流程，将汇总写入
`outputs/full_regression_report.json`，并在任一案例运行失败或出现
`error`级结构化校验问题时返回非零状态。当前21份案例全部完成，流程失败、
校验错误和校验警告均为0；其中两份泡泡玛特季度文件被正确识别为不含完整
财务报表的业务更新，因而不补造结构化财务事实。

## 5. 每次运行的输出

每份报告生成独立目录：

```text
outputs/<run_id>/
  manifest.json
  events.jsonl
  document.json
  ocr_metadata.json
  financial_facts.json
  nonrecurring_items.json
  narrative_evidence.json
  calculated_metrics.json
  health_assessment.json
  validation_issues.json
  findings.json
  llm_metadata.json        # 含请求/响应哈希、模型、耗时和Token用量
  llm_analysis.json        # 启用DeepSeek时
  agent_outputs.json       # 各专项、评级与编辑Agent的独立结构化结果
  analysis_bundle.json
  report.html              # 最多两页的可视化研报
  chat/<session_id>.jsonl # 每轮问题、回答、引用、模型元数据和耗时
```

其中 `financial_facts.json` 是事实层，`calculated_metrics.json` 是程序计算层，`findings.json` 是规则推论层，三者不混写。跨期命令另行生成 `company_timeline.json` 与对应HTML报告。

## 6. 当前边界

- 当前字段字典覆盖核心财务指标及银行关键指标；更细的三张报表科目和行业专属附注仍需随盲测集持续扩充。
- 港股业务公告不会被伪装成完整季度财报。
- 银行不会机械触发普通工商企业的现金流质量规则。
- 当前已适配并实测PaddleOCR-VL Jobs API；若服务端未来变更字段，需以部署页最新调用样例为准。
- 系统输出用于研究辅助，不构成投资建议。

完整安装、配置、运行、MCP 接入、日志定位与验收步骤见
`docs/金融投研智能体运行说明.docx`。技术细节另见 `docs/ARCHITECTURE.md`、
`docs/DATA_SCHEMA.md`、`docs/EVALUATION.md`、`docs/MCP_INTEGRATION.md` 和
`docs/OCR_INTEGRATION.md`。
