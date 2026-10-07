param(
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

function Invoke-CheckedPython {
    param([string[]]$Arguments)
    & $PythonExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

Invoke-CheckedPython -Arguments @("-m", "fin_agent.cli", "doctor")
Invoke-CheckedPython -Arguments @("-m", "unittest", "discover", "-s", (Join-Path $projectRoot "tests"), "-v")
Invoke-CheckedPython -Arguments @((Join-Path $PSScriptRoot "verify_mcp.py"))
Invoke-CheckedPython -Arguments @("-m", "fin_agent.cli", "benchmark")

Write-Host "Release verification completed."
