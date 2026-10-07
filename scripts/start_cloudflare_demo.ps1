param(
    [int]$Port = 8011,
    [string]$Username = "viewer",
    [string]$Password = "",
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Virtual environment not found. Create .venv and install requirements first."
}

$cloudflaredCommand = Get-Command cloudflared -ErrorAction SilentlyContinue
$cloudflaredExecutable = if ($cloudflaredCommand) {
    $cloudflaredCommand.Source
}
else {
    @(
        "C:\Program Files (x86)\cloudflared\cloudflared.exe",
        "C:\Program Files\cloudflared\cloudflared.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $cloudflaredExecutable) {
    throw "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared --exact"
}

if (-not $Password) {
    $randomBytes = New-Object byte[] 12
    $randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $randomGenerator.GetBytes($randomBytes)
    }
    finally {
        $randomGenerator.Dispose()
    }
    $Password = [System.BitConverter]::ToString($randomBytes).Replace("-", "").ToLowerInvariant()
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:FIN_AGENT_WEB_USERNAME = $Username
$env:FIN_AGENT_WEB_PASSWORD = $Password
$serverArguments = @("-m", "fin_agent.cli", "serve", "--host", "127.0.0.1", "--port", "$Port")
if ($Offline) {
    $serverArguments += "--offline"
}

$serverProcess = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList $serverArguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

try {
    $credentialText = "${Username}:${Password}"
    $credentialBytes = [System.Text.Encoding]::UTF8.GetBytes($credentialText)
    $authorization = "Basic " + [Convert]::ToBase64String($credentialBytes)
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri "http://127.0.0.1:${Port}/health" `
                -Headers @{ Authorization = $authorization } `
                -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "Local service did not become ready on port ${Port}."
    }

    Write-Host ""
    Write-Host "Temporary demo credentials" -ForegroundColor Cyan
    Write-Host "Username: $Username"
    Write-Host "Password: $Password"
    Write-Host ""
    Write-Host "Cloudflare will print a https://*.trycloudflare.com URL below." -ForegroundColor Yellow
    Write-Host "Share the URL and these credentials only with intended reviewers."
    Write-Host "Keep this window open. Press Ctrl+C to stop sharing."
    Write-Host ""

    & $cloudflaredExecutable tunnel --url "http://127.0.0.1:${Port}" --no-autoupdate
}
finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
        $serverProcess.WaitForExit()
    }
    Remove-Item Env:FIN_AGENT_WEB_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:FIN_AGENT_WEB_PASSWORD -ErrorAction SilentlyContinue
}
