param(
    [switch]$NoBrowser,
    [ValidateRange(1024,65525)][int]$LocalPort = 8780
)
$ErrorActionPreference = 'Stop'
$launcherState = Join-Path $PSScriptRoot '.spectra-launcher.json'
$sshProgram = (Get-Command ssh.exe -ErrorAction Stop).Source

function Test-SpectraEndpoint([int]$Port) {
    $response = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/edge/health")
        $request.Proxy = $null
        $request.Timeout = 1200
        $request.ReadWriteTimeout = 1200
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        try { $health = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        return ($health.connected -eq $true -and $health.device -eq 'Jetson-server-new')
    } catch { return $false } finally { if ($response) { $response.Dispose() } }
}

function Test-LocalPortBusy([int]$Port) {
    if (@([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $Port }).Count -gt 0) { return $true }
    # Windows may deny binding even when no process appears in the listener list.
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try { $probe.Server.ExclusiveAddressUse = $true; $probe.Start(); return $false }
    catch { return $true }
    finally { $probe.Stop() }
}

$startupMutex = New-Object System.Threading.Mutex($false, 'Local\SpectraLauncher')
$startupLocked = $false
try {
    try { $startupLocked = $startupMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $startupLocked = $true }
    if (-not $startupLocked) { throw 'Another Spectra launcher is starting. Wait for its result.' }
    Write-Host '[1/3] Checking Jetson services over SSH...'
    & $sshProgram -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 Jetson-server-new 'bash /home/sribd/spectrum_agent/scripts/ensure-ui-stack.sh'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start Jetson services. Check Wi-Fi and SSH to Jetson-server-new.' }

    Write-Host '[2/3] Checking the local connection...'
    $selectedPort = $null
    if (Test-SpectraEndpoint $LocalPort) { $selectedPort = $LocalPort }
    if (-not $selectedPort -and (Test-Path -LiteralPath $launcherState)) {
        try {
            $previous = Get-Content -LiteralPath $launcherState -Raw | ConvertFrom-Json
            if ($previous.device -eq 'Jetson-server-new' -and $previous.remote_port -eq 8780 -and
                $previous.preferred_port -eq $LocalPort -and $previous.local_port -ge 1024 -and
                $previous.local_port -le 65535 -and (Test-SpectraEndpoint ([int]$previous.local_port))) {
                $selectedPort = [int]$previous.local_port
            }
        } catch {}
    }
    if (-not $selectedPort) {
        $candidatePorts = @($LocalPort..($LocalPort + 10)) + @(18780..18790)
        foreach ($candidatePort in $candidatePorts) {
            if (-not (Test-LocalPortBusy $candidatePort)) { $selectedPort = $candidatePort; break }
        }
        if (-not $selectedPort) { throw 'No bindable local port in the preferred or fallback ranges. Existing applications were left running.' }
        $sshErrorLog = Join-Path ([System.IO.Path]::GetTempPath()) ('spectra-ssh-' + [guid]::NewGuid().ToString('N') + '.log')
        $forward = "127.0.0.1:${selectedPort}:127.0.0.1:8780"
        $tunnelProcess = Start-Process -FilePath $sshProgram -ArgumentList @('-N','-L',$forward,
            '-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ConnectionAttempts=1',
            '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
            'Jetson-server-new') -WindowStyle Hidden -PassThru -RedirectStandardError $sshErrorLog
        $ready = $false
        for ($attempt = 0; $attempt -lt 24; $attempt++) {
            if ($tunnelProcess.HasExited) { break }
            if (Test-SpectraEndpoint $selectedPort) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) {
            if (-not $tunnelProcess.HasExited) { $tunnelProcess.Kill() }
            $sshDetail = if (Test-Path -LiteralPath $sshErrorLog) { Get-Content -LiteralPath $sshErrorLog -Raw } else { '' }
            throw "SSH tunnel did not become ready. $sshDetail"
        }
    }
    $spectraUrl = "http://127.0.0.1:$selectedPort/"
    @{ device='Jetson-server-new'; remote_port=8780; preferred_port=$LocalPort; local_port=$selectedPort; url=$spectraUrl } |
        ConvertTo-Json | Set-Content -LiteralPath $launcherState -Encoding UTF8
    if ($selectedPort -ne $LocalPort) {
        Write-Host "Local port $LocalPort is unavailable; using $selectedPort. Existing editor connections are retained."
    }
    Write-Host "[3/3] Ready: $spectraUrl"
    if (-not $NoBrowser) {
        try { Start-Process -FilePath $spectraUrl -ErrorAction Stop }
        catch {
            $edgeProgram = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
            if (-not (Test-Path -LiteralPath $edgeProgram)) { throw "Cannot launch the browser. Open $spectraUrl manually." }
            Start-Process -FilePath $edgeProgram -ArgumentList $spectraUrl -ErrorAction Stop
        }
    }
} finally {
    if ($startupLocked) { $startupMutex.ReleaseMutex() }
    $startupMutex.Dispose()
}
