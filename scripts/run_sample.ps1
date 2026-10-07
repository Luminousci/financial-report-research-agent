$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
$Python = if ($env:FIN_AGENT_PYTHON) { $env:FIN_AGENT_PYTHON } else { 'python' }
$Sample = Join-Path (Split-Path -Parent $ProjectRoot) '年报\贵州茅台年报\第三季度报告.pdf'
& $Python -m fin_agent.cli analyze --input $Sample --ocr never --llm never

