# MCP服务接入

项目提供本地stdio MCP服务，源代码位于`src/fin_agent/mcp_server.py`。服务只访问
`FIN_AGENT_DATA_ROOTS`中的PDF和`FIN_AGENT_OUTPUT_DIR`下的白名单产物，不提供任意
文件读取或任意命令执行能力。

## 工具

- `financial_agent_status`：返回脱敏配置、财报数量和接口就绪状态；
- `list_financial_reports`：列出受控目录内的PDF；
- `analyze_financial_report`：执行固定编排的财报分析；
- `read_analysis_artifact`：读取指定运行的白名单JSON、JSONL或HTML产物。

## 启动

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m fin_agent.mcp_server
```

stdio的标准输出是协议通道，不应写入调试信息。宿主配置模板见
`config/mcp_server.example.json`，使用时将`<PROJECT_ROOT>`替换为项目绝对路径。

## 安全边界

- 输入PDF必须位于配置的数据根目录；
- 运行目录必须是输出目录的直接子目录；
- 可读取产物使用固定白名单，单次读取上限2 MB；
- API Key只由服务进程从`.env`读取，不进入工具参数或工具结果；
- 每次分析仍生成manifest、JSONL事件日志、文件哈希和外部调用元数据。
