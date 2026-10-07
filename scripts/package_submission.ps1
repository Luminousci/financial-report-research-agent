$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$distRoot = Join-Path $projectRoot "dist"
$stageRoot = Join-Path $distRoot ("fin-research-agent-submission-" + $timestamp)
$zipPath = $stageRoot + ".zip"

New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
$include = @(
    "agent_assets", "benchmark", "config", "scripts", "src", "tests", "财报数据",
    ".env.example", ".gitignore", "pyproject.toml", "README.md", "SUBMISSION_CONTENTS.md", "本地运行入口.html", "启动本地分析台.cmd", "requirements.txt",
    "requirements-lock.txt", "THIRD_PARTY.md"
)
foreach ($item in $include) {
    $source = Join-Path $projectRoot $item
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $stageRoot -Recurse -Force
    }
}

# Only ship technical documentation requested for the source-code handoff.
# Defense scripts, presentation plans and team-input templates intentionally
# remain outside the submission archive.
$stageDocs = Join-Path $stageRoot "docs"
New-Item -ItemType Directory -Path $stageDocs -Force | Out-Null
$technicalDocs = @(
    "ARCHITECTURE.md",
    "CLOUDFLARE_QUICK_TUNNEL.md",
    "COMPLETION_AUDIT.md",
    "DATA_SCHEMA.md",
    "EVALUATION.md",
    "MCP_INTEGRATION.md",
    "OCR_INTEGRATION.md",
    "TRACEABILITY_MATRIX.md",
    "金融投研智能体运行说明.docx"
)
foreach ($docName in $technicalDocs) {
    $sourceDoc = Join-Path (Join-Path $projectRoot "docs") $docName
    if (Test-Path -LiteralPath $sourceDoc -PathType Leaf) {
        Copy-Item -LiteralPath $sourceDoc -Destination $stageDocs -Force
    }
}

# Remove interpreter caches from the staged copy only. Validate every resolved
# target remains inside the timestamped staging directory before deletion.
$resolvedDistRoot = [System.IO.Path]::GetFullPath($distRoot)
$resolvedStageRoot = [System.IO.Path]::GetFullPath($stageRoot)
$stagePrefix = $resolvedStageRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$distPrefix = $resolvedDistRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedStageRoot.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging directory: $resolvedStageRoot"
}
Get-ChildItem -LiteralPath $resolvedStageRoot -Directory -Filter "__pycache__" -Recurse | ForEach-Object {
    $target = [System.IO.Path]::GetFullPath($_.FullName)
    if (-not $target.StartsWith($stagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove cache outside staging directory: $target"
    }
    Remove-Item -LiteralPath $target -Recurse -Force
}
Get-ChildItem -LiteralPath $resolvedStageRoot -File -Recurse |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
    ForEach-Object {
        $target = [System.IO.Path]::GetFullPath($_.FullName)
        if (-not $target.StartsWith($stagePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove bytecode outside staging directory: $target"
        }
        Remove-Item -LiteralPath $target -Force
    }

$sampleRoot = Join-Path $stageRoot "sample_outputs"
New-Item -ItemType Directory -Path $sampleRoot -Force | Out-Null
$visualReport = Join-Path $projectRoot "output\pdf\financial-analysis-report-preview.pdf"
if (Test-Path -LiteralPath $visualReport -PathType Leaf) {
    Copy-Item -LiteralPath $visualReport -Destination (Join-Path $sampleRoot "visual_financial_report_preview.pdf") -Force
}
$benchmark = Join-Path $projectRoot "outputs\benchmark_report.json"
if (Test-Path -LiteralPath $benchmark) {
    Copy-Item -LiteralPath $benchmark -Destination $sampleRoot -Force
}
$fullRegression = Join-Path $projectRoot "outputs\full_regression_report.json"
if (Test-Path -LiteralPath $fullRegression) {
    Copy-Item -LiteralPath $fullRegression -Destination $sampleRoot -Force
}
$latestReport = Get-ChildItem -LiteralPath (Join-Path $projectRoot "outputs") -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "report.html") } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($latestReport) {
    $latestDestination = Join-Path $sampleRoot "latest_report"
    New-Item -ItemType Directory -Path $latestDestination -Force | Out-Null
    foreach ($fileName in @(
        "report.html", "analysis_bundle.json", "manifest.json", "events.jsonl",
        "document.json", "financial_facts.json", "business_metrics.json", "nonrecurring_items.json",
        "narrative_evidence.json", "calculated_metrics.json", "health_assessment.json",
        "validation_issues.json", "findings.json",
        "ocr_metadata.json", "llm_metadata.json", "llm_analysis.json", "agent_outputs.json"
    )) {
        $sourceFile = Join-Path $latestReport.FullName $fileName
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination $latestDestination -Force
        }
    }
    $chatDirectory = Join-Path $latestReport.FullName "chat"
    if (Test-Path -LiteralPath $chatDirectory -PathType Container) {
        Copy-Item -LiteralPath $chatDirectory -Destination $latestDestination -Recurse -Force
    }
}

# Preserve one fully redacted live API example even when a later offline
# regression becomes the newest output directory.
$liveReport = Get-ChildItem -LiteralPath (Join-Path $projectRoot "outputs") -Directory |
    Where-Object {
        $llmPath = Join-Path $_.FullName "llm_metadata.json"
        $ocrPath = Join-Path $_.FullName "ocr_metadata.json"
        if (-not (Test-Path -LiteralPath $llmPath) -or -not (Test-Path -LiteralPath $ocrPath)) {
            return $false
        }
        try {
            $llm = Get-Content -LiteralPath $llmPath -Raw | ConvertFrom-Json
            $ocr = @(Get-Content -LiteralPath $ocrPath -Raw | ConvertFrom-Json)
            return $llm.status -eq "passed" -and $ocr.Count -gt 0 -and
                @($ocr | Where-Object { $_.status -eq "passed" }).Count -eq $ocr.Count
        }
        catch {
            return $false
        }
    } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($liveReport) {
    $liveDestination = Join-Path $sampleRoot "live_api_report"
    New-Item -ItemType Directory -Path $liveDestination -Force | Out-Null
    foreach ($fileName in @(
        "report.html", "analysis_bundle.json", "manifest.json", "events.jsonl",
        "document.json", "financial_facts.json", "business_metrics.json", "nonrecurring_items.json",
        "narrative_evidence.json", "calculated_metrics.json", "health_assessment.json",
        "validation_issues.json", "findings.json",
        "ocr_metadata.json", "llm_metadata.json", "llm_analysis.json", "agent_outputs.json"
    )) {
        $sourceFile = Join-Path $liveReport.FullName $fileName
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination $liveDestination -Force
        }
    }
    $chatDirectory = Join-Path $liveReport.FullName "chat"
    if (Test-Path -LiteralPath $chatDirectory -PathType Container) {
        Copy-Item -LiteralPath $chatDirectory -Destination $liveDestination -Recurse -Force
    }
}

if (Test-Path -LiteralPath (Join-Path $stageRoot ".env")) {
    throw "Refusing to package a real .env file."
}
Compress-Archive -LiteralPath $stageRoot -DestinationPath $zipPath -CompressionLevel Optimal
& (Join-Path $PSScriptRoot "verify_submission_package.ps1") `
    -ZipPath $zipPath `
    -EnvFile (Join-Path $projectRoot ".env")
Write-Host $zipPath
