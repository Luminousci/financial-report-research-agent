param(
    [string]$PythonExecutable = "python",
    [string]$InputDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $InputDirectory) {
    $InputDirectory = Split-Path -Parent $projectRoot
}
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$summaryPath = Join-Path $projectRoot "outputs\full_regression_report.json"

& $PythonExecutable -m fin_agent.cli batch `
    --input-dir $InputDirectory `
    --ocr never `
    --llm never `
    --summary-output $summaryPath `
    --fail-on-validation-error
if ($LASTEXITCODE -ne 0) {
    throw "Full-corpus verification failed with exit code ${LASTEXITCODE}."
}
Write-Host "Full-corpus verification completed: $summaryPath"
