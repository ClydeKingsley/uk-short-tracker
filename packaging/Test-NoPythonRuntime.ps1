#Requires -Version 7.2

[CmdletBinding()]
param(
    [string]$Archive = (Join-Path $PSScriptRoot '..\dist\Short-Tracker-v0.2.0-preview-windows-x64.zip')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot = Join-Path $temporaryBase ('short-tracker-no-python-' + [guid]::NewGuid().ToString('N'))
$testRoot = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
if (-not $testRoot.StartsWith($temporaryBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe temporary test root: $testRoot"
}

function Invoke-Frozen {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [string]$LocalAppData
    )
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WorkingDirectory = Split-Path -Parent $FilePath
    foreach ($key in @('PYTHONHOME', 'PYTHONPATH', 'SHORT_TRACKER_DATA_DIR', 'SHORT_TRACKER_PYTHON')) {
        [void]$startInfo.Environment.Remove($key)
    }
    $startInfo.Environment['PATH'] = Join-Path $env:WINDIR 'System32'
    $startInfo.Environment['LOCALAPPDATA'] = $LocalAppData
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($startInfo)
    if (-not $process) {
        throw "Could not start $FilePath"
    }
    try {
        if (-not $process.WaitForExit(60000)) {
            throw "Frozen command timed out: $FilePath"
        }
        return $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
}

$started = $false
try {
    $extractRoot = Join-Path $testRoot 'No Python 路径测试 Install A'
    $secondExtractRoot = Join-Path $testRoot 'No Python 路径测试 Install B'
    $localAppData = Join-Path $testRoot 'Local AppData'
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $secondExtractRoot -Force | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    Expand-Archive -LiteralPath $archivePath -DestinationPath $secondExtractRoot
    $archiveRootEntries = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
    $secondRootEntries = @(Get-ChildItem -LiteralPath $secondExtractRoot -Directory)
    if ($archiveRootEntries.Count -ne 1 -or $secondRootEntries.Count -ne 1) {
        throw 'Release archive must contain exactly one root directory.'
    }
    $packageRoot = $archiveRootEntries[0].FullName
    $secondPackageRoot = $secondRootEntries[0].FullName
    $startExe = Join-Path $packageRoot 'Short Tracker.exe'
    # Use the single application executable from a second installation folder
    # for the internal stop command, exercising the upgrade contract without a
    # separate user-visible Stop executable:
    # frozen ownership follows stable Local AppData state and verified process
    # identity, not the current resource directory.
    $secondExe = Join-Path $secondPackageRoot 'Short Tracker.exe'
    $releaseManifest = Get-Content -LiteralPath (Join-Path $packageRoot 'release-manifest.json') -Raw | ConvertFrom-Json
    $expectedVersion = [string]$releaseManifest.version

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }

    $stdoutLog = Join-Path $testRoot 'launcher-cli.out.log'
    $stderrLog = Join-Path $testRoot 'launcher-cli.err.log'
    $prefix = @('--launcher-cli', '--stdout-log', $stdoutLog, '--stderr-log', $stderrLog)
    $startArguments = $prefix + @(
        'start', '--port', [string]$port, '--no-open', '--skip-startup-sync'
    )
    $startExit = Invoke-Frozen -FilePath $startExe -Arguments $startArguments -LocalAppData $localAppData
    if ($startExit -ne 0) {
        $detail = Get-Content -LiteralPath $stderrLog -Raw -ErrorAction SilentlyContinue
        throw "No-Python start failed with exit code $startExit. $detail"
    }
    $started = $true

    $statePath = Join-Path $localAppData "ShortTracker\data\runtime\desktop-service-$port.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        throw 'Frozen default did not place runtime state below temporary Local AppData.'
    }
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$port/api/health")
    $request.Proxy = $null
    $request.Timeout = 5000
    $response = $request.GetResponse()
    try {
        $reader = [IO.StreamReader]::new($response.GetResponseStream(), [Text.Encoding]::UTF8)
        try {
            $health = $reader.ReadToEnd() | ConvertFrom-Json
        }
        finally {
            $reader.Dispose()
        }
    }
    finally {
        $response.Dispose()
    }
    if ($health.service -ne 'UK Short Tracker' -or $health.version -ne $expectedVersion -or [int]$health.protocol -ne 1) {
        throw 'No-Python health identity/version verification failed.'
    }

    $stopArguments = $prefix + @('stop', '--port', [string]$port)
    $stopExit = Invoke-Frozen -FilePath $secondExe -Arguments $stopArguments -LocalAppData $localAppData
    if ($stopExit -ne 0) {
        throw "No-Python stop failed with exit code $stopExit."
    }
    $started = $false
    if (Test-Path -LiteralPath $statePath) {
        throw 'No-Python runtime state remained after stop.'
    }

    [pscustomobject]@{
        NoPythonOnPath = 'passed'
        DefaultLocalAppData = 'passed'
        CrossInstallStop = 'passed'
        HealthVersion = $health.version
        Service = $health.service
        RandomPort = $port
    }
}
finally {
    if ($started) {
        try {
            $forceArguments = $prefix + @('stop', '--port', [string]$port, '--force-during-sync')
            [void](Invoke-Frozen -FilePath $secondExe -Arguments $forceArguments -LocalAppData $localAppData)
        }
        catch {
            Write-Warning "Safe smoke cleanup could not confirm service stop: $_"
        }
    }
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedCleanup = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
        if (-not $resolvedCleanup.StartsWith($temporaryBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a path outside the system temporary directory: $resolvedCleanup"
        }
        Remove-Item -LiteralPath $resolvedCleanup -Recurse -Force
    }
}
