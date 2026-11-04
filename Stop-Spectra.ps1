param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$remoteStopCommand = 'cd /home/sribd/spectrum_agent && .venv/bin/python scripts/stop-ui-stack.py'
if ($CheckOnly) { $remoteStopCommand += ' --check' }
ssh -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 Jetson-server-new $remoteStopCommand
if ($LASTEXITCODE -ne 0) { throw 'Jetson services were not fully stopped; the tunnel is retained. See the message above.' }
$platformTunnels = @(Get-CimInstance Win32_Process -Filter "name='ssh.exe'" | Where-Object {
    $_.CommandLine -match '(?i)(^|\s)"?Jetson-server-new"?(\s|$)' -and
    $_.CommandLine -match '(^|\s)-N(\s|$)' -and
    $_.CommandLine -match '(^|\s)-L\s+"?(?:127\.0\.0\.1:)?[0-9]+:127\.0\.0\.1:8780"?(\s|$)'
})
if ($CheckOnly) { Write-Host ('Matching local platform tunnels: '+$platformTunnels.Count); return }
foreach ($platformTunnel in $platformTunnels) { Stop-Process -Id $platformTunnel.ProcessId -ErrorAction Stop }
Write-Host 'Platform services and matching local SSH tunnel stopped.'
Write-Host 'Editor-managed port forwarding is retained; close port 8780 in the editor Ports panel if needed.'
