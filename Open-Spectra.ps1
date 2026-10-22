param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$spectraUrl = 'http://127.0.0.1:8780'
ssh -o BatchMode=yes Jetson-server-new 'bash /home/sribd/spectrum_agent/scripts/ensure-ui-stack.sh'
if ($LASTEXITCODE -ne 0) { throw 'Cannot start Jetson services. Check Wi-Fi and SSH.' }
$spectraReady = $false
try { $spectraReady = (Invoke-RestMethod "$spectraUrl/edge/health" -TimeoutSec 3).connected } catch {}
if (-not $spectraReady) {
    Start-Process -FilePath ssh.exe -ArgumentList @('-N','-L','8780:127.0.0.1:8780','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','Jetson-server-new') -WindowStyle Hidden
    for ($spectraTry=0; $spectraTry -lt 20; $spectraTry++) {
        Start-Sleep -Milliseconds 500
        try { if ((Invoke-RestMethod "$spectraUrl/edge/health" -TimeoutSec 2).connected) { $spectraReady=$true; break } } catch {}
    }
}
if (-not $spectraReady) { throw 'Tunnel unavailable. Check local port 8780 and SSH.' }
if (-not $NoBrowser) { Start-Process $spectraUrl }
