param(
    [switch]$SkipInfrastructure,
    [switch]$SkipFrontend,
    [switch]$Setup,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "apps\backend"
$FrontendDir = Join-Path $ProjectRoot "apps\frontend"
$BackendEnv = Join-Path $BackendDir ".env"
$BackendEnvTemplate = Join-Path $ProjectRoot "config\backend.env.example"
$FrontendEnv = Join-Path $FrontendDir ".env.local"
$FrontendEnvTemplate = Join-Path $ProjectRoot "config\frontend.env.example"
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$DockerCandidates = @(
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    "docker"
)

function Test-Command {
    param([Parameter(Mandatory)][string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Initialize-EnvironmentFile {
    param(
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$Template
    )

    if (-not (Test-Path -LiteralPath $Target)) {
        Copy-Item -LiteralPath $Template -Destination $Target
        Write-Host "Created environment file: $Target" -ForegroundColor Yellow
    }
}

function Start-DevTerminal {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Command
    )

    $escapedTitle = $Title.Replace("'", "''")
    $escapedDirectory = $WorkingDirectory.Replace("'", "''")
    $script = @"
`$Host.UI.RawUI.WindowTitle = '$escapedTitle'
Set-Location -LiteralPath '$escapedDirectory'
$Command
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit",
        "-NoProfile",
        "-EncodedCommand",
        $encoded
    ) -WindowStyle Normal | Out-Null
}

function Wait-TcpPort {
    param(
        [Parameter(Mandatory)][string]$HostName,
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutSeconds = 30
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $connect = $client.ConnectAsync($HostName, $Port)
            if ($connect.Wait(500) -and $client.Connected) {
                return
            }
        }
        catch {
            # Keep waiting while the container is starting.
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for ${HostName}:$Port."
}

function Resolve-DockerCommand {
    foreach ($candidate in $DockerCandidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    return $null
}

function Test-DockerEngine {
    param([Parameter(Mandatory)][string]$DockerCommand)

    $previousErrorActionPreference = $ErrorActionPreference
    $exitCode = -1
    try {
        # Windows PowerShell 5.1 turns native stderr into an ErrorRecord. Keep a
        # failed readiness probe from terminating the script before its exit code
        # can be inspected.
        $ErrorActionPreference = "Continue"
        & $DockerCommand info --format "{{.ServerVersion}}" 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return $exitCode -eq 0
}

function Wait-DockerEngine {
    param(
        [Parameter(Mandatory)][string]$DockerCommand,
        [int]$TimeoutSeconds = 120
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-DockerEngine -DockerCommand $DockerCommand) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for Docker Engine. Check Docker Desktop."
}

if ($ValidateOnly) {
    Write-Host "Startup script syntax is valid." -ForegroundColor Green
    exit 0
}

Initialize-EnvironmentFile -Target $BackendEnv -Template $BackendEnvTemplate
Initialize-EnvironmentFile -Target $FrontendEnv -Template $FrontendEnvTemplate

if (-not $SkipInfrastructure) {
    $DockerCommand = Resolve-DockerCommand
    if ($null -eq $DockerCommand) {
        throw "Docker CLI was not found. Check Docker Desktop or use -SkipInfrastructure."
    }
    # Docker resolves its credential helper through PATH even when the CLI uses an absolute path.
    $dockerBin = Split-Path -Parent $DockerCommand
    if (($env:PATH -split ";") -notcontains $dockerBin) {
        $env:PATH = "$dockerBin;$env:PATH"
    }
    if (-not (Test-DockerEngine -DockerCommand $DockerCommand)) {
        if (-not (Test-Path -LiteralPath $DockerDesktop)) {
            throw "Docker Engine is not running and Docker Desktop was not found."
        }
        Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
        Start-Process -FilePath $DockerDesktop -WindowStyle Hidden | Out-Null
        Wait-DockerEngine -DockerCommand $DockerCommand
    }
    Write-Host "Starting Redis, Qdrant, and the secured Playwright MCP service..." -ForegroundColor Cyan
    & $DockerCommand compose --project-directory $ProjectRoot up -d redis qdrant playwright-mcp-gateway
    if ($LASTEXITCODE -ne 0) {
        throw "Infrastructure startup failed. Check Docker Desktop and docker compose logs."
    }
    Wait-TcpPort -HostName "127.0.0.1" -Port 6379
    Wait-TcpPort -HostName "127.0.0.1" -Port 6333
    Wait-TcpPort -HostName "127.0.0.1" -Port 8931
}

$UvCommand = Get-Command "uv" -ErrorAction SilentlyContinue
if ($null -eq $UvCommand) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/ and run this script again."
}
$UvExe = $UvCommand.Source

Push-Location -LiteralPath $BackendDir
try {
    if ($Setup) {
        Write-Host "Installing backend dependencies with uv..." -ForegroundColor Cyan
        & $UvExe sync --extra dev
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependency installation failed."
        }
        Write-Host "Installing Playwright Chromium for PDF export..." -ForegroundColor Cyan
        & $UvExe run playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            throw "Playwright Chromium installation failed."
        }
    }
    else {
        & $UvExe run python -c "import arq, fastapi, qdrant_client" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependencies are missing. Run .\start-dev.ps1 -Setup first."
        }
    }
}
finally {
    Pop-Location
}

if (-not $SkipFrontend) {
    if (-not (Test-Command "npm.cmd")) {
        throw "npm was not found. Install Node.js 22+."
    }
    if ($Setup -or -not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
        Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
        & npm.cmd --prefix $FrontendDir ci
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend dependency installation failed."
        }
    }
}

$escapedUv = $UvExe.Replace("'", "''")
$BackendCommand = "& '$escapedUv' run python -m app.main"
$MemoryWorkerCommand = "& '$escapedUv' run arq app.ai_chat.memory.worker.WorkerSettings"
$IndexWorkerCommand = "& '$escapedUv' run arq app.resume_generation.index_worker.WorkerSettings"

Start-DevTerminal -Title "ResumeAgent - Backend" -WorkingDirectory $BackendDir -Command $BackendCommand
Start-DevTerminal -Title "ResumeAgent - Memory Worker" -WorkingDirectory $BackendDir -Command $MemoryWorkerCommand
Start-DevTerminal -Title "ResumeAgent - Resume Index Worker" -WorkingDirectory $BackendDir -Command $IndexWorkerCommand

if (-not $SkipFrontend) {
    Start-DevTerminal `
        -Title "ResumeAgent - Frontend" `
        -WorkingDirectory $FrontendDir `
        -Command "& npm.cmd run dev"
}

Write-Host "Development environment started." -ForegroundColor Green
if (-not $SkipFrontend) {
    Write-Host "Frontend: http://localhost:3000"
}
Write-Host "Backend: http://localhost:8000"
Write-Host "Qdrant: http://localhost:6333/dashboard"
Write-Host "Playwright MCP: http://localhost:8931/mcp"
Write-Host "The first indexing run downloads dense/sparse models and may take some time." -ForegroundColor Yellow
