param(
    [string]$PythonExecutable = "python",
    [string]$PdfPath = "",
    [int]$Page = 1
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Missing $envPath. Copy .env.example to .env and fill in the credentials first."
}

$arguments = @("-m", "fin_agent.cli", "--env", $envPath, "verify-integrations", "--page", $Page)
if ($PdfPath) {
    $resolvedPdf = [System.IO.Path]::GetFullPath($PdfPath)
    if (-not (Test-Path -LiteralPath $resolvedPdf -PathType Leaf)) {
        throw "OCR test PDF does not exist: $resolvedPdf"
    }
    $arguments += @("--pdf", $resolvedPdf)
}

& $PythonExecutable @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Live integration verification failed. Inspect the reported integration_check.json."
}

Write-Host "DeepSeek and OCR live integration verification passed."
