param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,
    [string]$EnvFile
)

$ErrorActionPreference = "Stop"
$resolvedZip = [System.IO.Path]::GetFullPath($ZipPath)
if (-not (Test-Path -LiteralPath $resolvedZip -PathType Leaf)) {
    throw "Submission archive not found: $resolvedZip"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($resolvedZip)
try {
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace("\\", "/") })
    $requiredPatterns = @(
        "/src/fin_agent/pipeline.py$",
        "/src/fin_agent/ocr.py$",
        "/src/fin_agent/deepseek.py$",
        "/src/fin_agent/business_update.py$",
        "/src/fin_agent/audit.py$",
        "/src/fin_agent/mcp_server.py$",
        "/src/fin_agent/webapp.py$",
        "/agent_assets/prompts/analysis_system.txt$",
        "/agent_assets/ui/market-analysis-background.jpg$",
        "/agent_assets/skills/financial_report_analysis/SKILL.md$",
        "/tests/test_ocr.py$",
        "/tests/test_webapp.py$",
        "/tests/test_business_update.py$",
        "/config/frontend_design.json$",
        "/财报数据/README.md$",
        "/财报数据/示例/000651_格力电器_2026Q1_第一季度报告.pdf$",
        "/财报数据/示例/600887_伊利股份_2026Q1_第一季度报告.pdf$",
        "/财报数据/示例/601127_赛力斯_2026Q1_第一季度报告.pdf$",
        "/scripts/start_cloudflare_demo.ps1$",
        "/启动本地分析台\.cmd$",
        "/requirements-lock.txt$",
        "/docs/金融投研智能体运行说明.docx$",
        "/docs/ARCHITECTURE.md$",
        "/docs/CLOUDFLARE_QUICK_TUNNEL.md$",
        "/docs/COMPLETION_AUDIT.md$",
        "/sample_outputs/benchmark_report.json$",
        "/sample_outputs/full_regression_report.json$",
        "/sample_outputs/live_api_report/report.html$",
        "/sample_outputs/live_api_report/ocr_metadata.json$",
        "/sample_outputs/live_api_report/llm_metadata.json$",
        "/.env.example$"
    )
    $missing = @()
    foreach ($pattern in $requiredPatterns) {
        if (-not ($entryNames | Where-Object { $_ -match $pattern })) {
            $missing += $pattern
        }
    }

    $forbidden = @(
        $entryNames | Where-Object {
            $_ -match '(^|/)\.env$' -or
            $_ -match '(^|/)__pycache__(/|$)' -or
            $_ -match '\.py[co]$' -or
            $_ -match '(^|/)(启动分析台\.exe|导出启动日志\.vbs|Anaconda导出日志\.py|Anaconda启动分析台\.py|启动本地演示\.cmd|启动诊断日志\.txt)$' -or
            $_ -match '(^|/)launcher(/|$)' -or
            $_ -match '(^|/)scripts/anaconda_support\.py$' -or
            $_ -match '(^|/)scripts/export_startup_log\.ps1$' -or
            $_ -match '/docs/(DEFENSE_QA|DEMO_SCRIPT|SUBMISSION_PLAN|PROJECT_PROPOSAL|TEAM_INPUT_TEMPLATE)\.md$'
        }
    )

    $secretValues = @()
    if ($EnvFile -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        Get-Content -LiteralPath $EnvFile | ForEach-Object {
            if ($_ -match '^\s*([^#=]*(KEY|TOKEN|SECRET)[^=]*)=(.*)$') {
                $value = $Matches[3].Trim()
                if ($value.Length -ge 8) {
                    $secretValues += $value
                }
            }
        }
    }

    $leakedEntries = @()
    foreach ($entry in $archive.Entries) {
        if ($entry.Length -le 0 -or $entry.Length -gt 20MB) {
            continue
        }
        try {
            $reader = [System.IO.StreamReader]::new($entry.Open())
            try {
                $content = $reader.ReadToEnd()
            }
            finally {
                $reader.Dispose()
            }
            foreach ($value in $secretValues) {
                if ($content.Contains($value)) {
                    $leakedEntries += $entry.FullName
                    break
                }
            }
        }
        catch {
            # Binary entries are not required for the secret-value text scan.
        }
    }

    $result = [ordered]@{
        archive = $resolvedZip
        size_bytes = (Get-Item -LiteralPath $resolvedZip).Length
        entry_count = $entryNames.Count
        required_artifacts_present = $missing.Count -eq 0
        missing_artifacts = $missing
        forbidden_entry_count = $forbidden.Count
        secret_leak_count = $leakedEntries.Count
        live_api_evidence_present = (
            @($entryNames | Where-Object { $_ -match '/sample_outputs/live_api_report/' }).Count -gt 0
        )
        passed = $missing.Count -eq 0 -and $forbidden.Count -eq 0 -and $leakedEntries.Count -eq 0
    }
    $result | ConvertTo-Json -Depth 4
    if (-not $result.passed) {
        throw "Submission archive verification failed."
    }
}
finally {
    $archive.Dispose()
}
