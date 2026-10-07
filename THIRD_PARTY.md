# Third-party components

| Component | Version policy | Source | License | Use |
|---|---:|---|---|---|
| Python | 3.12.14（已验证） | https://www.python.org/ | PSF | Runtime |
| pydantic | 2.13.5（已验证） | https://github.com/pydantic/pydantic | MIT | Data validation |
| pypdf | 6.10.0（已验证） | https://github.com/py-pdf/pypdf | BSD-3-Clause | PDF text and metadata |
| pdfplumber | 0.11.9（已验证） | https://github.com/jsvine/pdfplumber | MIT | PDF tables and page geometry |
| Pillow | 12.3.0（已验证） | https://python-pillow.org/ | HPND | Image handling |
| Poppler | Runtime-provided | https://poppler.freedesktop.org/ | GPL/LGPL components | PDF page rendering for OCR |
| Model Context Protocol Python SDK | 2.2.0（已验证） | https://github.com/modelcontextprotocol/python-sdk | MIT | Local stdio MCP server and tool schemas |
| Requests | 2.32.5（已验证） | https://github.com/psf/requests | Apache-2.0 | AI Studio multipart upload, polling and JSONL download |
| DeepSeek API | Project default model: `deepseek-flash`; actual deployed model is recorded per run | https://platform.deepseek.com/ | DeepSeek commercial service terms | Called through the OpenAI-compatible HTTPS Chat Completions API. Used only for five specialist analyses, rating synthesis, report editing and optional evidence-grounded Q&A; it does not calculate or overwrite base financial values |
| PaddleOCR-VL | `PaddleOCR-VL-1.6` | https://aistudio.baidu.com/paddleocr | Baidu AI Studio service terms and model-specific license | Called through AI Studio PaddleOCR Jobs API v2 by multipart upload, asynchronous polling and JSONL result download. Used only as page-level OCR fallback |
| Baidu Intelligent Cloud OCR | User-configured optional provider | https://ai.baidu.com/tech/ocr | Baidu Intelligent Cloud service terms | Optional high-accuracy OCR adapter; not required when AI Studio is configured |
| Cloudflare Tunnel | `cloudflared 2026.9.3` in the verified demo environment | https://github.com/cloudflare/cloudflared | Apache-2.0; Cloudflare service terms apply | Optional transport for temporary Web demonstration. The executable and service source code are not included |
| Market analysis background photo | User-provided asset; creator: Jakub Zerdzicki | https://www.pexels.com/ | Pexels License | Web interface background (`agent_assets/ui/market-analysis-background.jpg`) |
| Public company financial reports | Issuer-disclosed report versions supplied in the controlled competition dataset | Shanghai/Shenzhen/Hong Kong exchange or issuer disclosure channels | Rights remain with the respective issuers and disclosure platforms | Input data for parsing, calculation and evaluation. Original reports are not redistributed in the source-code submission unless separately authorized by the organizer |

Model weights, commercial service source code and third-party executables are not redistributed. API access is performed with credentials supplied by the evaluator through local environment variables. Exact model names, endpoints, prompt versions, request/response hashes, token use and call scope are recorded in each run manifest and `llm_metadata.json`.

本项目没有把上述第三方库、模型、服务、数据或图片作为自主研发成果。项目自主实现范围包括智能体编排、任务裁剪、财务字段标准化、确定性计算、校验与异常规则、健康评分、报告生成、Web交互、MCP封装及审计日志。

可复现安装优先使用 `requirements-lock.txt`。`requirements.txt` 和 `pyproject.toml` 保留兼容版本范围，便于在评审环境中处理镜像可用性差异。
