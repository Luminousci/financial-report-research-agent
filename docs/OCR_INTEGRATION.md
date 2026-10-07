# AI Studio OCR接入与验收

## 密钥文件

在项目根目录创建UTF-8纯文本 `.env`，不要使用`.txt`扩展名：

```dotenv
OCR_PROVIDER=aistudio
AISTUDIO_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
AISTUDIO_ACCESS_TOKEN=访问令牌
AISTUDIO_AUTH_SCHEME=bearer
AISTUDIO_REQUEST_STYLE=paddleocr_job
AISTUDIO_MODEL=PaddleOCR-VL-1.6
AISTUDIO_POLL_INTERVAL_SECONDS=5
AISTUDIO_POLL_TIMEOUT_SECONDS=1800
AISTUDIO_TIMEOUT_SECONDS=180
```

当前适配器严格对应已提供的异步API样例：本地文件以multipart方式提交到`jobs`，
使用`bearer`鉴权，按配置间隔轮询`jobs/{jobId}`，任务完成后下载JSONL结果，并
读取`result.layoutParsingResults[].markdown.text`。默认关闭文档方向分类、图像
矫正和图表识别，与调用样例一致。

`AISTUDIO_TIMEOUT_SECONDS`是单次HTTP请求超时；`AISTUDIO_POLL_TIMEOUT_SECONDS`
是整个OCR任务最长等待时间。任务ID、模型、轮询次数、请求/响应哈希和耗时会
写入`ocr_metadata.json`，Token和结果下载URL不会写入日志。

## 验收步骤

1. `python -m fin_agent.cli doctor`应显示`ocr_ready: true`。
2. 执行`.\scripts\verify_live_integrations.ps1 -PythonExecutable ".\.venv\Scripts\python.exe" -PdfPath "测试财报.pdf" -Page 1`。该步骤只提交一页以控制时间和费用。
3. 检查`integration_check.json`中DeepSeek和OCR均为`passed`，并人工检查`ocr_recognized_text.txt`。
4. 选择泡泡玛特两页业务公告，以`--ocr force --llm never`运行。
5. `document.json`中的页面方法应为`ocr:aistudio`，`manifest.json`应记录OCR供应商和页码。
6. 检查OCR文本中公司名、报告期间、币种和关键百分比。
7. 重复运行并比较结构化输出；若服务存在随机性，记录差异并固定部署模型版本。

## 安全要求

- `.env`不得进入提交包、截图或录屏。
- 日志只显示`configured/missing`。
- 不把访问令牌写入URL、Prompt或错误报告。
- 仅上传比赛允许处理的页面。
