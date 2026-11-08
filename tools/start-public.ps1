param(
    [int]$EnginePort = 0,
    [string]$Cloudflared,
    [string]$Archive
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logDir = Join-Path $root 'backend/data/public-tools'
$null = New-Item -ItemType Directory -Force -Path $logDir
if (!$Cloudflared) { $Cloudflared = Join-Path $logDir 'cloudflared-windows-amd64.exe' }
if (!$Archive) { $Archive = Join-Path $root 'ml/data/compare-friend-v7/submitted.zip' }
$python = Join-Path $root 'ml/.venv/Scripts/python.exe'
$asrPython = Join-Path $root 'ml/asr/.venv/Scripts/python.exe'
foreach ($file in @($Cloudflared,$Archive,$python,$asrPython)) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required local file missing: $file" }
}

function Get-Health([int]$Port) {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 } catch { return $null }
}

function Start-EvaProcess($File, $Arguments, $WorkingDirectory, $Log) {
    $options = @{
        FilePath = $File; ArgumentList = $Arguments; WorkingDirectory = $WorkingDirectory
        RedirectStandardOutput = (Join-Path $logDir "$Log-out.log")
        RedirectStandardError = (Join-Path $logDir "$Log.log")
        WindowStyle = 'Hidden'; PassThru = $true
    }
    return Start-Process @options
}

function Start-EvaService($Port, $Service, $File, $Arguments, $Directory, $Log) {
    $status = Get-Health $Port
    if ($status) {
        if ($status.service -ne $Service) { throw "Port $Port is occupied by a different service." }
        return
    }
    $process = Start-EvaProcess $File $Arguments $Directory $Log
    for ($try = 0; $try -lt 120; $try++) {
        if ($process.HasExited) { throw "Failed to start $Service. See $logDir/$Log.log." }
        if ((Get-Health $Port).service -eq $Service) { return }
        Start-Sleep -Milliseconds 1000
    }
    throw "$Service startup timed out. See $logDir/$Log.log."
}

$env:VITE_API_URL = '/'
$env:VITE_API_MODE = 'http'
$env:VITE_API_TIMEOUT_MS = '120000'
Push-Location (Join-Path $root 'frontend')
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }

$env:HF_HUB_OFFLINE = '1'
$env:EVA_V7_MODE = 'evidence'
$env:EVA_V7_ARCHIVE = (Resolve-Path -LiteralPath $Archive).Path
$env:EVA_CONSULTATION_VARIANT = 'consultation-v1.1'
$env:PUBLIC_ML_URL = 'http://127.0.0.1:18721'
$env:PUBLIC_ASR_URL = 'http://127.0.0.1:18602'
$mlArgs = 'ml/integration/audit_stack.py ml --port 18721 --run-dir ml/data/public-serve'
if ($EnginePort -gt 0) { $mlArgs += " --engine-port $EnginePort" }
Start-EvaService 18602 'eva-asr' $asrPython '-m uvicorn eva_asr.app:app --host 127.0.0.1 --port 18602 --no-access-log' (Join-Path $root 'ml') 'asr'
Start-EvaService 18721 'eva-ml' $python $mlArgs $root 'ml'
Start-EvaService 18720 'backend' $python '-m uvicorn app.public:public_app --factory --host 127.0.0.1 --port 18720 --no-access-log' (Join-Path $root 'backend') 'gateway'
# Verify session isolation even when the port was already occupied by a compatible backend.
& $python (Join-Path $PSScriptRoot 'smoke_public.py') 'http://127.0.0.1:18720' --isolation-only
if ($LASTEXITCODE -ne 0) { throw 'Public workspace isolation failed.' }
$pattern = 'https://[a-z0-9-]+\.trycloudflare\.com'
$logPath = Join-Path $logDir 'tunnel.log'
if (Test-Path -LiteralPath $logPath) {
    $previous = [regex]::Match((Get-Content -LiteralPath $logPath -Raw), $pattern).Value
    if ($previous) {
        try { $live = Invoke-RestMethod "$previous/health" -TimeoutSec 5 } catch { $live = $null }
        if ($live.service -eq 'backend') { Write-Output $previous; return }
    }
}
$tunnel = Start-EvaProcess $Cloudflared 'tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:18720' $logDir 'tunnel'
for ($try = 0; $try -lt 45; $try++) {
    if ($tunnel.HasExited) { throw "Tunnel failed. See $logPath." }
    if (Test-Path -LiteralPath $logPath) {
        $url = [regex]::Match([string](Get-Content -LiteralPath $logPath -Raw), $pattern).Value
        if ($url) {
            try { $live = Invoke-RestMethod "$url/health" -TimeoutSec 5 } catch { $live = $null }
            if ($live.service -eq 'backend') { Write-Output $url; return }
        }
    }
    Start-Sleep -Seconds 1
}
throw "Public URL did not become ready. See $logPath."
