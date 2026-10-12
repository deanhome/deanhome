# Build SeratoAnalyzer.exe (and optionally an installer) on Windows.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1 -Installer
#
# The architecture of the build matches the Python that runs it (x64 or ARM64).
# The installer needs Inno Setup 6 (iscc.exe on PATH or in its default folder).
param(
    [switch]$Installer,
    [switch]$SkipTests,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

$version = & $Python -c "import sys; sys.path.insert(0, 'src'); import serato_analyzer as s; print(s.__version__)"
$arch = & $Python -c "import platform; m = platform.machine().lower(); print('arm64' if m in ('arm64', 'aarch64') else 'x64')"

& $Python -m pip install --upgrade pip
& $Python -m pip install -e ".[build,dev]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    Remove-Item Env:QT_QPA_PLATFORM
}

$dist = "dist\windows-$arch"
& $Python -m PyInstaller --noconfirm --clean packaging\serato_analyzer.spec --distpath $dist --workpath "build\windows-$arch"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$exe = "$dist\SeratoAnalyzer\SeratoAnalyzer.exe"
# A windowed exe does not attach to the console, so wait for it and check the exit code.
$proc = Start-Process -FilePath $exe -ArgumentList "--self-test" -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) { throw "Self-test of the built executable failed" }
Write-Host "Executable: $exe"

$zip = "dist\SeratoAnalyzer-$version-windows-$arch.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$dist\SeratoAnalyzer" -DestinationPath $zip
Write-Host "Portable zip: $zip"

if ($Installer) {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found; install it or omit -Installer." }
    & $iscc "/DAppVersion=$version" "/DAppArch=$arch" "/DSourceDir=$((Resolve-Path $dist).Path)\SeratoAnalyzer" packaging\windows\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
}
