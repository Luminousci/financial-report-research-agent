param(
    [string]$PythonExecutable,
    [int]$Port = 8000,
    [switch]$Offline,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$requirementsLock = Join-Path $projectRoot "requirements-lock.txt"
$createdVirtualEnvironment = $false

# A second launcher invocation should reuse an already-running local service
# instead of failing with an opaque address-in-use error.
try {
    $existingHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($existingHealth.status -eq "ok") {
        Write-Host "The financial analysis service is already running at http://127.0.0.1:$Port"
        if (-not $NoBrowser) {
            Start-Process "http://127.0.0.1:$Port"
        }
        return
    }
}
catch {
    # No compatible service is listening; continue with normal startup.
}

if (-not $PythonExecutable) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $basePython = Get-Command python -ErrorAction SilentlyContinue
        $bootstrapExecutable = $null
        $bootstrapArguments = @()
        if ($basePython) {
            $bootstrapExecutable = $basePython.Source
        }
        else {
            $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
            if ($pyLauncher) {
                $bootstrapExecutable = $pyLauncher.Source
                $bootstrapArguments = @("-3")
            }
        }
        if (-not $bootstrapExecutable) {
            $condaCandidates = [System.Collections.Generic.List[string]]::new()
            if ($env:CONDA_PREFIX) {
                $condaCandidates.Add((Join-Path $env:CONDA_PREFIX "python.exe"))
            }
            $condaCommand = Get-Command conda -ErrorAction SilentlyContinue
            if ($condaCommand) {
                try {
                    $condaBase = (& $condaCommand.Source info --base 2>$null | Select-Object -Last 1).Trim()
                    if ($condaBase) {
                        $condaCandidates.Add((Join-Path $condaBase "python.exe"))
                    }
                }
                catch {
                    # Continue with common Anaconda and Miniconda locations.
                }
            }
            foreach ($candidate in @(
                (Join-Path $env:USERPROFILE "anaconda3\python.exe"),
                (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
                (Join-Path $env:LOCALAPPDATA "anaconda3\python.exe"),
                (Join-Path $env:LOCALAPPDATA "miniconda3\python.exe"),
                (Join-Path $env:ProgramData "Anaconda3\python.exe"),
                (Join-Path $env:ProgramData "Miniconda3\python.exe"),
                "C:\Anaconda3\python.exe",
                "C:\Miniconda3\python.exe"
            )) {
                if ($candidate) {
                    $condaCandidates.Add($candidate)
                }
            }
            $bootstrapExecutable = $condaCandidates |
                Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
                Select-Object -First 1
            if ($bootstrapExecutable) {
                Write-Host "Using Anaconda/Miniconda Python: $bootstrapExecutable"
            }
        }
        if (-not $bootstrapExecutable) {
            throw "Python 3.10+ was not found. Open Anaconda Prompt once or add Anaconda to PATH, then retry; alternatively install Python and enable Add Python to PATH."
        }
        & $bootstrapExecutable @bootstrapArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -ne 0) {
            throw "The detected Python is older than 3.10. Update the Anaconda environment or install Python 3.10+."
        }
        Write-Host "First run: creating an isolated Python environment at $projectRoot\.venv"
        & $bootstrapExecutable @bootstrapArguments -m venv (Join-Path $projectRoot ".venv")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Failed to create the local Python virtual environment."
        }
        $createdVirtualEnvironment = $true
    }
    $PythonExecutable = $venvPython
}
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

if ($createdVirtualEnvironment) {
    if (-not (Test-Path -LiteralPath $requirementsLock -PathType Leaf)) {
        throw "Dependency lock file was not found: $requirementsLock"
    }
    Write-Host "First run: installing locked dependencies. Internet access may be required."
    & $PythonExecutable -m pip install --disable-pip-version-check -r $requirementsLock
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Check the network connection and retry."
    }
}

& $PythonExecutable -m fin_agent.cli doctor
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path -LiteralPath $requirementsLock -PathType Leaf)) {
        throw "Environment check failed and requirements-lock.txt was not found."
    }
    Write-Host "Required packages are incomplete; repairing the local environment."
    & $PythonExecutable -m pip install --disable-pip-version-check -r $requirementsLock
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency repair failed. Check the network connection and retry."
    }
    & $PythonExecutable -m fin_agent.cli doctor
    if ($LASTEXITCODE -ne 0) {
        throw "Environment check failed; demo server was not started."
    }
}

$arguments = @("-m", "fin_agent.cli", "serve", "--host", "127.0.0.1", "--port", "$Port")
if ($Offline) {
    $arguments += "--offline"
}
if (-not $NoBrowser) {
    $arguments += "--open-browser"
}

Write-Host "Starting demo at http://127.0.0.1:$Port"
& $PythonExecutable @arguments
