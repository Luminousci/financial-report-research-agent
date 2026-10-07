# Cloudflare 临时分享使用说明

本项目使用 Cloudflare Quick Tunnel 将本机页面临时映射为公网 HTTPS 地址。该方式
适合短期演示和内部评审，不保证长期在线，且每次启动通常会生成新的随机网址。

> **需要生成完整财报细节时，请不要带 `-Offline`。** `-Offline` 只用于检查页面、
> 分享链路和确定性计算，会在服务端强制关闭 AI Studio OCR 与 DeepSeek，因此页面
> 显示“离线兜底已启用”是预期行为。

## 1. 安装 cloudflared

在 PowerShell 中执行：

```powershell
winget install --id Cloudflare.cloudflared --exact
cloudflared --version
```

Windows 也可从 Cloudflare 官方下载页安装 64 位 MSI：
<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/>

## 2. 第一次测试：离线模式

进入项目目录并运行：

```powershell
cd E:\金融人工智能\fin_research_agent
.\scripts\start_cloudflare_demo.ps1 -Offline
```

脚本将自动：

1. 生成一次性随机密码；
2. 在本机 `127.0.0.1:8011` 启动财报分析页面；
3. 启动 Cloudflare Quick Tunnel；
4. 在终端显示用户名、密码和 `https://*.trycloudflare.com` 地址。

把网址和登录信息分别发给指定体验者。浏览器会先弹出用户名/密码输入框，验证后
才能进入页面。`-Offline` 模式不会调用 AI Studio OCR 或 DeepSeek。

## 3. 启用 OCR 和 DeepSeek

确认离线页面可正常打开后，关闭当前隧道，再执行：

```powershell
.\scripts\start_cloudflare_demo.ps1
```

联网模式会读取项目根目录的 `.env`，但不会把 API Key 发送到浏览器、打印到日志
或写入分享网址。访问者提交分析任务时可能消耗 OCR 和 DeepSeek 额度。

点击“生成报告”后，服务器会立即返回独立进度页，分析在后台继续执行；页面通过短轮询读取状态，完成后自动打开研报。这样即使OCR或多Agent分析超过Cloudflare单次请求等待窗口，也不会触发524。若服务进程在任务期间被关闭或重启，内存中的任务状态会失效，需要从分析台重新提交。

## 4. 指定登录信息或端口

```powershell
.\scripts\start_cloudflare_demo.ps1 `
  -Username reviewer `
  -Password "请替换为至少16位随机密码" `
  -Port 8015 `
  -Offline
```

未指定 `-Password` 时，脚本使用系统密码学随机数生成 24 位十六进制临时密码，
只显示在当前终端，不写入项目文件。

## 5. 停止分享

在运行隧道的 PowerShell 窗口按 `Ctrl+C`。脚本会关闭 Cloudflare Tunnel、本地 Web
服务，并清除当前进程中的临时用户名和密码。Quick Tunnel 地址随后失效。

不要直接关闭后台 Python 进程后继续保留隧道，也不要把 PowerShell 窗口长期无人
值守地保持打开。

## 6. 安全边界

- Quick Tunnel 只用于测试和短期演示，不用于正式生产环境；
- 不要在聊天群或公开网页同时发送网址和密码；
- 联网演示前确认 DeepSeek 和 AI Studio 账户具有合理额度；
- 不要把真实 `.env`、API Key 或访问密码提交到压缩包；
- 演示完成后立即按 `Ctrl+C` 关闭分享；
- 需要固定域名、稳定在线和按用户授权时，应改用 Cloudflare Named Tunnel 与
  Cloudflare Access，或部署到受控云服务器。

Cloudflare Quick Tunnel 官方说明：
<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/>

## 7. 常见问题

### 找不到 cloudflared

关闭并重新打开 PowerShell，再运行 `cloudflared --version`。脚本也会自动检查两个
常见安装路径。如果仍找不到，重新执行 Winget 安装命令。

### 页面显示 401

认证保护正常工作。使用脚本启动时打印的用户名和密码登录；重新启动脚本后，随机
密码会变化。

### 页面显示 502

通常表示本地 Web 服务没有正常启动或端口冲突。先执行：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m fin_agent.cli doctor
```

然后换一个端口，例如 `-Port 8015`。

### 页面一直显示“离线兜底已启用”

停止当前脚本，然后从项目目录重新启动，并确保命令末尾没有 `-Offline`：

```powershell
cd E:\金融人工智能\fin_research_agent
.\scripts\start_cloudflare_demo.ps1
```

联网模式的主页应显示 OCR 与 DeepSeek“已就绪”，且不再出现离线兜底提示。若仍未
就绪，先运行 `python -m fin_agent.cli doctor` 检查 `.env` 配置。

### 链接突然失效

Quick Tunnel 依赖当前电脑、PowerShell 窗口和网络连接。电脑休眠、断网、关闭窗口或
按下 `Ctrl+C` 都会使链接失效。重新运行脚本会得到新的地址。
