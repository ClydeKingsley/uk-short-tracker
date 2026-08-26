#Requires -Version 7.2

[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$Version,

    [switch]$AllowMissingLicense,

    [switch]$Force,

    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$buildParent = Join-Path $repoRoot 'build'
$distRoot = Join-Path $repoRoot 'dist'
$configuredPython = $env:SHORT_TRACKER_BUILD_PYTHON
$venvPython = Join-Path $repoRoot '.build-venv\Scripts\python.exe'
if ($configuredPython) {
    $pythonExe = [IO.Path]::GetFullPath($configuredPython)
}
elseif (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $pythonExe = $venvPython
}
else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'No build Python was found. Create .build-venv or set SHORT_TRACKER_BUILD_PYTHON.'
    }
    $pythonExe = $pythonCommand.Source
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Parent
    )
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $resolvedPath.StartsWith($resolvedParent + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing unsafe path outside $resolvedParent`: $resolvedPath"
    }
    return $resolvedPath
}

function Remove-SafeTree {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Parent
    )
    $resolved = Assert-ChildPath -Path $Path -Parent $Parent
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

function Invoke-WindowedApplication {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$Arguments
    )
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WorkingDirectory = Split-Path -Parent $FilePath
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($startInfo)
    if (-not $process) {
        throw "Failed to start $FilePath"
    }
    try {
        if (-not $process.WaitForExit(60000)) {
            throw "Launcher command timed out: $FilePath $($Arguments -join ' ')"
        }
        return $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
}

function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Test-LocalPort {
    param([int]$Port)
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return $task.Wait(700) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Invoke-LoopbackGet {
    param([Parameter(Mandatory)] [string]$Uri)
    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.Proxy = $null
    $request.Timeout = 5000
    $request.ReadWriteTimeout = 5000
    $request.UserAgent = 'ShortTrackerReleaseSmoke/1'
    $response = $request.GetResponse()
    try {
        $stream = $response.GetResponseStream()
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
        try {
            return [pscustomobject]@{
                StatusCode = [int]$response.StatusCode
                Content = $reader.ReadToEnd()
            }
        }
        finally {
            $reader.Dispose()
            $stream.Dispose()
        }
    }
    finally {
        $response.Dispose()
    }
}

function Get-DirectoryFingerprint {
    param([Parameter(Mandatory)] [string]$Root)
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    return @(
        Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force -File |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    Path = $_.FullName.Substring($resolvedRoot.Length + 1).Replace('\', '/')
                    Bytes = $_.Length
                    SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            }
    ) | ConvertTo-Json -Compress
}

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Build Python does not exist: $pythonExe"
}

Push-Location -LiteralPath $repoRoot
try {
    $pythonProbe = & $pythonExe -c "import json,pathlib,platform,struct,sys; p=pathlib.Path(sys.base_prefix,'BUILD'); print(json.dumps({'version':list(sys.version_info[:3]),'build':p.read_text().strip() if p.is_file() else None,'bits':struct.calcsize('P')*8,'machine':platform.machine()}))"
    if ($LASTEXITCODE -ne 0) {
        throw 'Build Python probe failed.'
    }
    $pythonInfo = $pythonProbe | ConvertFrom-Json
    if (($pythonInfo.version -join '.') -ne '3.11.15' -or $pythonInfo.build -ne '20260623') {
        throw "Windows release builds require the locked uv-managed Python 3.11.15 build 20260623; found $($pythonInfo.version -join '.') build $($pythonInfo.build)."
    }
    if ([int]$pythonInfo.bits -ne 64) {
        throw "Windows release builds require a 64-bit Python; found $($pythonInfo.bits)-bit."
    }
    if (-not [Environment]::Is64BitOperatingSystem -or -not $IsWindows) {
        throw 'Windows x64 release builds must run on Windows x64.'
    }

    $pyinstallerVersion = (& $pythonExe -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $pyinstallerVersion -ne '6.22.2') {
        throw "PyInstaller 6.22.2 is required; found '$pyinstallerVersion'. Install requirements-build.lock."
    }
    $desktopDependencyProbe = & $pythonExe -c "import json; from importlib.metadata import version; print(json.dumps({'pywebview':version('pywebview'),'pythonnet':version('pythonnet')}))"
    if ($LASTEXITCODE -ne 0) {
        throw 'Desktop dependency probe failed. Install requirements-build.lock.'
    }
    $desktopDependencies = $desktopDependencyProbe | ConvertFrom-Json
    if ($desktopDependencies.pywebview -ne '6.2.1' -or $desktopDependencies.pythonnet -ne '3.1.0') {
        throw "pywebview 6.2.1 and pythonnet 3.1.0 are required; found pywebview=$($desktopDependencies.pywebview), pythonnet=$($desktopDependencies.pythonnet)."
    }

    $sourceVersion = (& $pythonExe -m tools.verify_version --root $repoRoot).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Version verification failed.'
    }
    if (-not $Version) {
        $Version = $sourceVersion
    }
    & $pythonExe -m tools.verify_version --root $repoRoot --expected $Version
    if ($LASTEXITCODE -ne 0) {
        throw 'Requested release version does not match source/changelog.'
    }

    $licensePath = Join-Path $repoRoot 'LICENSE'
    $hasLicense = Test-Path -LiteralPath $licensePath -PathType Leaf
    if (-not $hasLicense -and -not $AllowMissingLicense) {
        throw 'LICENSE is missing. Choose a project licence before a public build, or use -AllowMissingLicense only for a private preview.'
    }
    if ($hasLicense) {
        $publicationArguments = @(
            '-m', 'tools.verify_publication_config', '--root', $repoRoot
        )
        if ($env:GITHUB_REPOSITORY) {
            $publicationArguments += @(
                '--github-repository', $env:GITHUB_REPOSITORY
            )
        }
        & $pythonExe @publicationArguments
        if ($LASTEXITCODE -ne 0) {
            throw 'Public-release configuration failed; no stable release was built.'
        }
    }
    $channel = if ($hasLicense) { 'stable' } else { 'preview' }
    $nameSuffix = if ($channel -eq 'stable') { '' } else { '-preview' }
    $packageName = "Short-Tracker-v$Version$nameSuffix-windows-x64"
    $archiveName = "$packageName.zip"
    $archivePath = Join-Path $distRoot $archiveName
    $hashPath = "$archivePath.sha256"
    $externalManifestPath = Join-Path $distRoot "release-manifest-v$Version$nameSuffix.json"

    if (-not $SkipTests) {
        & $pythonExe -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw 'Unit tests failed; no release was built.'
        }
    }
    & $pythonExe -m tools.audit_public_tree $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Public-tree privacy audit failed; no release was built.'
    }

    New-Item -ItemType Directory -Path $buildParent -Force | Out-Null
    New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
    $workRoot = Join-Path $buildParent "windows-$Version$nameSuffix"
    Remove-SafeTree -Path $workRoot -Parent $buildParent
    New-Item -ItemType Directory -Path $workRoot -Force | Out-Null

    foreach ($output in @($archivePath, $hashPath, $externalManifestPath)) {
        if (Test-Path -LiteralPath $output) {
            if (-not $Force) {
                throw "Output already exists: $output. Use -Force to replace these exact release files."
            }
            Remove-Item -LiteralPath $output -Force
        }
    }

    $generatedRoot = Join-Path $workRoot 'generated'
    $iconPath = Join-Path $generatedRoot 'short-tracker.ico'
    $versionInfoPath = Join-Path $generatedRoot 'version_info.txt'
    & $pythonExe -m tools.generate_icon $iconPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Application icon generation failed.'
    }
    & $pythonExe -m tools.generate_version_info --root $repoRoot --output $versionInfoPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows version-resource generation failed.'
    }

    $pyiDist = Join-Path $workRoot 'pyinstaller-dist'
    $pyiWork = Join-Path $workRoot 'pyinstaller-work'
    $oldIcon = $env:SHORT_TRACKER_ICON
    $oldVersionInfo = $env:SHORT_TRACKER_VERSION_INFO
    try {
        $env:SHORT_TRACKER_ICON = $iconPath
        $env:SHORT_TRACKER_VERSION_INFO = $versionInfoPath
        & $pythonExe -m PyInstaller `
            --noconfirm `
            --clean `
            --log-level WARN `
            --distpath $pyiDist `
            --workpath $pyiWork `
            (Join-Path $repoRoot 'packaging\ShortTracker.spec')
        if ($LASTEXITCODE -ne 0) {
            throw 'PyInstaller build failed.'
        }
    }
    finally {
        $env:SHORT_TRACKER_ICON = $oldIcon
        $env:SHORT_TRACKER_VERSION_INFO = $oldVersionInfo
    }

    $bundleRoot = Join-Path $pyiDist 'Short Tracker'
    $startExe = Join-Path $bundleRoot 'Short Tracker.exe'
    if (-not (Test-Path -LiteralPath $startExe -PathType Leaf)) {
        throw "Built executable is missing: $startExe"
    }

    $packageParent = Join-Path $workRoot 'package'
    $packageRoot = Join-Path $packageParent $packageName
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $bundleRoot -Force | Copy-Item -Destination $packageRoot -Recurse -Force
    $packageStartExe = Join-Path $packageRoot 'Short Tracker.exe'
    Copy-Item -LiteralPath (Join-Path $repoRoot 'packaging\README-Windows.txt') -Destination (Join-Path $packageRoot 'README.txt')
    Copy-Item -LiteralPath (Join-Path $repoRoot 'PRIVACY.md') -Destination (Join-Path $packageRoot 'PRIVACY.txt')
    Copy-Item -LiteralPath (Join-Path $repoRoot 'THIRD-PARTY-NOTICES.md') -Destination (Join-Path $packageRoot 'THIRD-PARTY-NOTICES.txt')
    # .NET reads process-wide startup policy only from the executable's sibling
    # config. Keep the same reviewed file inside _internal for the dedicated
    # pythonnet AppDomain and beside the EXE for CLR startup itself.
    Copy-Item `
        -LiteralPath (Join-Path $repoRoot 'launcher\pythonnet-netfx.config') `
        -Destination (Join-Path $packageRoot 'Short Tracker.exe.config')
    $pyzTocPath = Join-Path $pyiWork 'ShortTracker\PYZ-00.toc'
    & $pythonExe -m tools.collect_third_party_licenses `
        --repo $repoRoot `
        --release-root $packageRoot `
        --pyz-toc $pyzTocPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Complete third-party licence collection failed.'
    }
    if ($hasLicense) {
        Copy-Item -LiteralPath $licensePath -Destination (Join-Path $packageRoot 'LICENSE.txt')
    }
    else {
        Copy-Item -LiteralPath (Join-Path $repoRoot 'packaging\PREVIEW-NOT-LICENSED.txt') -Destination (Join-Path $packageRoot 'PREVIEW-NOT-LICENSED.txt')
    }

    & $pythonExe -m tools.verify_pe_subsystem $packageStartExe
    if ($LASTEXITCODE -ne 0) {
        throw 'The Windows executables are not windowed GUI applications.'
    }

    $manifestPath = Join-Path $packageRoot 'release-manifest.json'
    & $pythonExe -m tools.generate_release_manifest `
        --repo $repoRoot `
        --release-root $packageRoot `
        --output $manifestPath `
        --version $Version `
        --pyinstaller $pyinstallerVersion `
        --channel $channel
    if ($LASTEXITCODE -ne 0) {
        throw 'Release-manifest generation failed.'
    }
    Copy-Item -LiteralPath $manifestPath -Destination $externalManifestPath

    Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveVerificationArguments = @(
        '-m', 'tools.verify_release_archive', $archivePath,
        '--forbid-string', $repoRoot
    )
    if ($env:USERPROFILE) {
        $archiveVerificationArguments += @('--forbid-string', $env:USERPROFILE)
    }
    & $pythonExe @archiveVerificationArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Final release ZIP verification failed.'
    }

    $archiveDigest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $hashPath -Value "$archiveDigest  $archiveName" -Encoding ascii

    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    $smokeRoot = Join-Path $temporaryBase ("short-tracker-release-" + [guid]::NewGuid().ToString('N'))
    $smokeRoot = Assert-ChildPath -Path $smokeRoot -Parent $temporaryBase
    $smokeStarted = $false
    try {
        $smokeExtract = Join-Path $smokeRoot 'Short Tracker 发布测试'
        $smokeData = Join-Path $smokeRoot 'isolated user data'
        New-Item -ItemType Directory -Path $smokeExtract -Force | Out-Null
        Expand-Archive -LiteralPath $archivePath -DestinationPath $smokeExtract
        $smokePackage = Join-Path $smokeExtract $packageName
        $smokeStartExe = Join-Path $smokePackage 'Short Tracker.exe'
        $beforeFingerprint = Get-DirectoryFingerprint -Root $smokePackage
        # Reproduce Explorer's treatment of an Internet-downloaded ZIP. The
        # resulting alternate data stream used to make .NET Framework reject
        # Python.Runtime.dll on a clean PC, even though the same bundle passed
        # on its build runner.
        $motwRuntimeDll = Join-Path $smokePackage '_internal\pythonnet\runtime\Python.Runtime.dll'
        if (-not (Test-Path -LiteralPath $motwRuntimeDll -PathType Leaf)) {
            throw 'Bundled Python.Runtime.dll is missing before Mark-of-the-Web smoke.'
        }
        Set-Content `
            -LiteralPath $motwRuntimeDll `
            -Stream Zone.Identifier `
            -Value "[ZoneTransfer]`r`nZoneId=3`r`nHostUrl=https://github.com/" `
            -Encoding ascii
        $zoneMarker = Get-Content -LiteralPath $motwRuntimeDll -Stream Zone.Identifier -Raw
        if ($zoneMarker -notmatch 'ZoneId=3') {
            throw 'Could not apply the Mark-of-the-Web regression fixture.'
        }
        $smokePort = Get-FreePort
        $cliStdout = Join-Path $smokeRoot 'launcher-cli.out.log'
        $cliStderr = Join-Path $smokeRoot 'launcher-cli.err.log'
        $bundleSelfTestPath = Join-Path $smokeRoot 'bundle-self-test.json'
        $bundleSelfTestExit = Invoke-WindowedApplication `
            -FilePath $smokeStartExe `
            -Arguments @('--bundle-self-test', '--result-json', $bundleSelfTestPath)
        if ($bundleSelfTestExit -ne 0 -or -not (Test-Path -LiteralPath $bundleSelfTestPath)) {
            $bundleSelfTestDetail = if (Test-Path -LiteralPath $bundleSelfTestPath) {
                Get-Content -LiteralPath $bundleSelfTestPath -Raw
            }
            else {
                'No self-test result was written.'
            }
            throw "Bundled openpyxl/SQLite/SSL/WebView2 self-test failed with exit code $bundleSelfTestExit. $bundleSelfTestDetail"
        }
        $bundleSelfTest = Get-Content -LiteralPath $bundleSelfTestPath -Raw | ConvertFrom-Json
        if (-not $bundleSelfTest.ok -or $bundleSelfTest.webview_renderer -ne 'edgechromium') {
            throw "Bundled dependency self-test failed: $($bundleSelfTest | ConvertTo-Json -Compress)"
        }
        $internalArgs = @('--launcher-cli', '--stdout-log', $cliStdout, '--stderr-log', $cliStderr)
        $startArgs = $internalArgs + @(
            'start', '--data-dir', $smokeData, '--port', [string]$smokePort,
            '--no-open', '--skip-startup-sync'
        )
        $startExit = Invoke-WindowedApplication -FilePath $smokeStartExe -Arguments $startArgs
        if ($startExit -ne 0) {
            throw "Extracted bundle start failed with exit code $startExit. CLI log: $cliStderr"
        }
        $smokeStarted = $true
        $statePath = Join-Path $smokeData "runtime\desktop-service-$smokePort.json"
        if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
            throw 'Extracted bundle did not create authenticated runtime state.'
        }
        $firstState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $healthResponse = Invoke-LoopbackGet -Uri "http://127.0.0.1:$smokePort/api/health"
        $health = $healthResponse.Content | ConvertFrom-Json
        if (
            $healthResponse.StatusCode -ne 200 -or
            $health.service -ne 'UK Short Tracker' -or
            $health.mode -ne 'local_read_only_research' -or
            $health.version -ne $Version -or
            [int]$health.protocol -ne 1 -or
            $health.instance_id -ne $firstState.instance_id
        ) {
            throw 'Extracted bundle health identity/version contract failed.'
        }
        foreach ($asset in @('', 'styles.css', 'i18n.js', 'app.js')) {
            $response = Invoke-LoopbackGet -Uri "http://127.0.0.1:$smokePort/$asset"
            if ($response.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($response.Content)) {
                throw "Static asset smoke failed: /$asset"
            }
        }

        $duplicateExit = Invoke-WindowedApplication -FilePath $smokeStartExe -Arguments $startArgs
        if ($duplicateExit -ne 0) {
            throw "Duplicate-start smoke failed with exit code $duplicateExit."
        }
        $secondState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($firstState.pid -ne $secondState.pid) {
            throw "Duplicate start created another service process: $($firstState.pid) / $($secondState.pid)"
        }

        $stopArgs = $internalArgs + @('stop', '--data-dir', $smokeData, '--port', [string]$smokePort)
        $stopExit = Invoke-WindowedApplication -FilePath $smokeStartExe -Arguments $stopArgs
        if ($stopExit -ne 0) {
            throw "Extracted bundle stop failed with exit code $stopExit."
        }
        $smokeStarted = $false
        $deadline = [DateTime]::UtcNow.AddSeconds(8)
        while ([DateTime]::UtcNow -lt $deadline -and (Test-LocalPort -Port $smokePort)) {
            Start-Sleep -Milliseconds 100
        }
        if ((Test-LocalPort -Port $smokePort) -or (Test-Path -LiteralPath $statePath)) {
            throw 'Extracted bundle did not fully stop or clean runtime state.'
        }
        $remainingStopRequests = @(Get-ChildItem -LiteralPath (Join-Path $smokeData 'runtime') -Filter 'stop-*.request' -File -ErrorAction SilentlyContinue)
        if ($remainingStopRequests.Count -gt 0) {
            throw 'Extracted bundle left a stop request behind.'
        }
        $afterFingerprint = Get-DirectoryFingerprint -Root $smokePackage
        if ($beforeFingerprint -ne $afterFingerprint) {
            throw 'The extracted program directory changed during runtime; writable state escaped its data directory.'
        }
    }
    finally {
        if ($smokeStarted) {
            try {
                $forceArgs = $internalArgs + @(
                    'stop', '--data-dir', $smokeData, '--port', [string]$smokePort,
                    '--force-during-sync'
                )
                [void](Invoke-WindowedApplication -FilePath $smokeStartExe -Arguments $forceArgs)
            }
            catch {
                Write-Warning "Smoke cleanup could not confirm a safe service stop: $_"
            }
        }
        if (Test-Path -LiteralPath $smokeRoot) {
            Remove-SafeTree -Path $smokeRoot -Parent $temporaryBase
        }
    }

    Write-Output "Release ZIP: $archivePath"
    Write-Output "SHA-256: $archiveDigest"
    Write-Output "Manifest: $externalManifestPath"
    Write-Output "Channel: $channel"
    Write-Output 'Bundled personal/local data: no'
    Write-Output 'Third-party licence inventory and hashes: passed'
    Write-Output 'Downloaded-ZIP pythonnet/WebView2 self-test: passed'
    Write-Output 'Extracted start/duplicate/assets/stop smoke: passed'
    if (-not $hasLicense) {
        Write-Warning 'This is an internal preview without a project licence. Do not publish or redistribute it.'
    }
}
finally {
    Pop-Location
}
