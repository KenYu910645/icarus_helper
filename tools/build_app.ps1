param([switch]$Shortcut, [string]$OutputRoot = 'dist')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = (Get-Command python.exe -ErrorAction Stop).Source
$taskBuild = (python -c 'from icarus_helper.catalog import CATALOG_BUILD; print(CATALOG_BUILD)').Trim()
$taskCache = Join-Path $taskRoot "icarus_helper\atlas\$taskBuild"
$taskOutput = [IO.Path]::GetFullPath((Join-Path $taskRoot $OutputRoot))
$taskAllowedOutput = [IO.Path]::GetFullPath((Join-Path $taskRoot 'dist'))
if ($taskOutput -ne $taskAllowedOutput -and -not $taskOutput.StartsWith($taskAllowedOutput + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be inside this project''s dist directory' }
$taskBundle = Join-Path $taskOutput 'ICARUS Helper'
$taskExePath = Join-Path $taskBundle 'ICARUS Helper.exe'
if (Get-Process -Name 'ICARUS Helper' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskExePath }) { throw 'This build is running. Choose another OutputRoot or close it before retrying' }
$taskAtlas = Join-Path $taskRoot "build\atlas\$taskBuild"
New-Item -ItemType Directory -Path (Join-Path $taskAtlas 'game_data\World') -Force | Out-Null
$taskRuntimeFiles = @('prometheus_nodes.json','foliage_lookup.json','prometheus_public.json','prometheus_public_provenance.json','prometheus.jpg','olympus_public.json','olympus_public_provenance.json','olympus.jpg','styx_public.json','styx_public_provenance.json','styx.jpg')
foreach ($taskName in $taskRuntimeFiles) {
    $taskSource = Join-Path $taskCache $taskName
    if (-not (Test-Path -LiteralPath $taskSource)) { throw "Map data missing: $taskSource" }
    Copy-Item -LiteralPath $taskSource -Destination (Join-Path $taskAtlas $taskName) -Force
}
foreach ($taskName in @('D_VoxelDistributionRegion.json','D_VoxelSetupData.json')) {
    Copy-Item -LiteralPath (Join-Path $taskCache "game_data\World\$taskName") -Destination (Join-Path $taskAtlas "game_data\World\$taskName") -Force
}
$taskPreviousPath = $env:PATH
try {
    # Native DLL discovery must not search unrelated Codex/Poppler/Conda tools.
    $taskPythonRoot = Split-Path -Parent $taskPython
    $taskBuildPaths = @($taskPythonRoot, (Join-Path $taskPythonRoot 'Scripts'), (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot, (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0'))
    $env:PATH = ($taskBuildPaths | Where-Object { Test-Path -LiteralPath $_ }) -join ';'
    & $taskPython -m PyInstaller --clean --noconfirm --windowed --name 'ICARUS Helper' --distpath $taskOutput --icon 'icarus_helper/assets/app.ico' --add-data 'icarus_helper/assets/app.ico;icarus_helper/assets' --add-data 'icarus_helper/assets/items;icarus_helper/assets/items' --add-data 'icarus_helper/assets/animals;icarus_helper/assets/animals' --add-data 'icarus_helper/data;icarus_helper/data' --add-data "$taskAtlas;icarus_helper/atlas/$taskBuild" --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets --exclude-module PySide6.QtVirtualKeyboard main.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop app build failed' }
} finally {
    $env:PATH = $taskPreviousPath
}
& $taskPython tools/prune_qt_runtime.py --bundle-dir $taskBundle --report 'artifacts/qt-pruned.json'
if ($LASTEXITCODE -ne 0) { throw 'Could not safely remove unused Qt components' }
& $taskPython tools/check_qt_runtime.py --bundle-dir $taskBundle --report 'artifacts/qt-runtime.json'
if ($LASTEXITCODE -ne 0) { throw 'Qt runtime verification failed; delivery checks did not pass' }
$taskSelfTestPath = Join-Path $taskRoot 'artifacts\packaged-shared-startup.json'
$taskPreviousPlatform = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    $taskProbeArgs = @('--self-test-shared', '--self-test-report', ('"' + $taskSelfTestPath + '"'))
    $taskProbe = Start-Process -FilePath $taskExePath -ArgumentList $taskProbeArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru
    if (-not $taskProbe.WaitForExit(75000)) {
        Stop-Process -Id $taskProbe.Id -ErrorAction SilentlyContinue
        throw 'Packaged EXE startup verification timed out'
    }
    $taskProbe.Refresh()
    if ($taskProbe.ExitCode -ne 0) { throw "Packaged EXE startup verification failed; see $taskSelfTestPath" }
    $taskProbeResult = Get-Content -LiteralPath $taskSelfTestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $taskProbeResult.success -or -not $taskProbeResult.frozen -or -not $taskProbeResult.shared_map) { throw 'Packaged EXE did not pass shared map and base loading verification' }
} finally {
    $env:QT_QPA_PLATFORM = $taskPreviousPlatform
}
if ($Shortcut) {
    $taskShell = New-Object -ComObject WScript.Shell
    $taskDesktop = [Environment]::GetFolderPath('Desktop')
    $taskLink = $taskShell.CreateShortcut((Join-Path $taskDesktop 'ICARUS Helper.lnk'))
    $taskExpectedTarget = $taskExePath
    if ($taskLink.TargetPath -and $taskLink.TargetPath -ne $taskExpectedTarget) { throw 'A desktop shortcut with this name already exists. It was preserved; choose another name' }
    $taskLink.TargetPath = $taskExpectedTarget
    $taskLink.WorkingDirectory = $taskRoot
    $taskLink.Description = 'ICARUS maps, resources, missions and world save companion'
    $taskBundledIcon = Join-Path $taskBundle '_internal\icarus_helper\assets\app.ico'
    $taskIconHash = (Get-FileHash -LiteralPath $taskBundledIcon -Algorithm SHA256).Hash.ToLowerInvariant()
    # Keep Explorer's icon source outside Store-app LocalAppData virtualization.
    $taskIconCache = Join-Path $taskBundle '.icons'
    New-Item -ItemType Directory -Path $taskIconCache -Force | Out-Null
    $taskShortcutIcon = Join-Path $taskIconCache ('helper-' + $taskIconHash.Substring(0, 16) + '.ico')
    Copy-Item -LiteralPath $taskBundledIcon -Destination $taskShortcutIcon -Force
    $taskLink.IconLocation = $taskShortcutIcon + ',0'
    $taskLink.Save()
}
