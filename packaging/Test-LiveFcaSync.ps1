#Requires -Version 7.2

[CmdletBinding()]
param(
    [string]$Archive = (Join-Path $PSScriptRoot '..\dist\Short-Tracker-v0.2.0-preview-windows-x64.zip'),
    [switch]$KeepTemporaryData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot = Join-Path $temporaryBase ('short-tracker-live-fca-' + [guid]::NewGuid().ToString('N'))
$testRoot = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
if (-not $testRoot.StartsWith($temporaryBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe temporary test root: $testRoot"
}

function Invoke-FrozenCommand {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [string]$LocalAppData,
        [int]$TimeoutMilliseconds = 300000
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
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            throw "Frozen command timed out after $TimeoutMilliseconds ms."
        }
        return $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
}

function Get-CommandDiagnostic {
    param(
        [Parameter(Mandatory)] [string]$StdoutPath,
        [Parameter(Mandatory)] [string]$StderrPath
    )
    $stdout = Get-Content -LiteralPath $StdoutPath -Raw -ErrorAction SilentlyContinue
    $stderr = Get-Content -LiteralPath $StderrPath -Raw -ErrorAction SilentlyContinue
    return "stdout=[$stdout] stderr=[$stderr]"
}

try {
    $extractRoot = Join-Path $testRoot 'Live FCA 同步测试'
    $localAppData = Join-Path $testRoot 'Local AppData'
    $dataRoot = Join-Path $testRoot 'isolated data'
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $rootEntries = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
    if ($rootEntries.Count -ne 1) {
        throw 'Release archive must contain exactly one root directory.'
    }
    $application = Join-Path $rootEntries[0].FullName 'Short Tracker.exe'

    $syncOut = Join-Path $testRoot 'sync.out.log'
    $syncErr = Join-Path $testRoot 'sync.err.log'
    $syncArguments = @(
        '--service-child', '--stdout-log', $syncOut, '--stderr-log', $syncErr,
        '--data-dir', $dataRoot, 'sync'
    )
    $syncExit = Invoke-FrozenCommand -FilePath $application -Arguments $syncArguments -LocalAppData $localAppData
    if ($syncExit -ne 0) {
        $detail = Get-CommandDiagnostic -StdoutPath $syncOut -StderrPath $syncErr
        throw "Live FCA sync failed with exit code $syncExit. $detail"
    }
    $syncResult = Get-Content -LiteralPath $syncOut -Raw | ConvertFrom-Json
    $sourceNames = @($syncResult.sources.PSObject.Properties.Name)
    if (-not $syncResult.ok -or $sourceNames.Count -ne 6) {
        throw "Unexpected live sync result: $($syncResult | ConvertTo-Json -Depth 8 -Compress)"
    }

    $verifyOut = Join-Path $testRoot 'verify.out.log'
    $verifyErr = Join-Path $testRoot 'verify.err.log'
    $verifyArguments = @(
        '--service-child', '--stdout-log', $verifyOut, '--stderr-log', $verifyErr,
        '--data-dir', $dataRoot, 'verify'
    )
    $verifyExit = Invoke-FrozenCommand -FilePath $application -Arguments $verifyArguments -LocalAppData $localAppData
    if ($verifyExit -ne 0) {
        $detail = Get-CommandDiagnostic -StdoutPath $verifyOut -StderrPath $verifyErr
        throw "Live FCA verification failed with exit code $verifyExit. $detail"
    }
    $verification = Get-Content -LiteralPath $verifyOut -Raw | ConvertFrom-Json
    if (-not $verification.ok) {
        throw "Live FCA verification report is not OK: $($verification | ConvertTo-Json -Depth 8 -Compress)"
    }

    $heads = @(
        $verification.checks |
            Where-Object name -eq 'four_active_datasets' |
            Select-Object -ExpandProperty detail
    )
    $archiveCheck = $verification.checks | Where-Object name -eq 'latest_official_archives'
    $archiveNames = @($archiveCheck.detail.PSObject.Properties.Name)
    if (-not $archiveCheck.passed -or $archiveNames.Count -ne 6) {
        throw "Expected six verified FCA source archives: $($archiveCheck | ConvertTo-Json -Depth 8 -Compress)"
    }
    [pscustomobject]@{
        LiveFcaSync = 'passed'
        FrozenVerify = 'passed'
        DownloadedSources = $sourceNames.Count
        VerifiedArchives = $archiveNames.Count
        ActiveDatasets = ($heads -join ', ')
        DataBytes = (Get-ChildItem -LiteralPath $dataRoot -Recurse -File | Measure-Object Length -Sum).Sum
        TemporaryRoot = if ($KeepTemporaryData) { $testRoot } else { '<removed>' }
    }
}
finally {
    if ($KeepTemporaryData) {
        Write-Warning "Temporary live-sync data retained for review: $testRoot"
    }
    elseif (Test-Path -LiteralPath $testRoot) {
        $resolvedCleanup = [IO.Path]::GetFullPath($testRoot).TrimEnd('\')
        if (-not $resolvedCleanup.StartsWith($temporaryBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a path outside the system temporary directory: $resolvedCleanup"
        }
        Remove-Item -LiteralPath $resolvedCleanup -Recurse -Force
    }
}
