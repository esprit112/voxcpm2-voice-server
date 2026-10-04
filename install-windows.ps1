<#
.SYNOPSIS
  VoxCPM2 Voice Server installer for Windows 10 / 11.

.DESCRIPTION
  Installs everything into this folder, without admin rights and without touching any other Python:
    1. checks Windows, disk space, memory and your GPU
    2. installs uv (a fast Python manager) if needed
    3. creates a private Python 3.11 environment in .venv
    4. installs PyTorch built for your hardware (NVIDIA CUDA, Intel Arc XPU or CPU) + the VoxCPM2 engine
    5. downloads the VoxCPM2 model (~5 GB) into models\voxcpm2
    6. creates .env, runs a health check, and (optionally) a desktop shortcut / autostart

  Safe to run again: it repairs or updates an existing install and skips finished steps.

.EXAMPLE
  .\install-windows.ps1                       # recommended: automatic everything
  .\install-windows.ps1 -Cpu                  # no GPU / force the CPU build
  .\install-windows.ps1 -TorchBackend cu128   # pick a CUDA build yourself
  .\install-windows.ps1 -SkipModel            # install now, download the model later
  .\install-windows.ps1 -ModelDir D:\models\voxcpm2 -DesktopShortcut -Autostart
#>
[CmdletBinding()]
param(
    [string]$TorchBackend = "auto",
    [switch]$Cpu,
    [switch]$SkipModel,
    [string]$ModelDir = "",
    [int]$Port = 0,
    [string]$PythonVersion = "3.11",
    [switch]$DesktopShortcut,
    [switch]$Autostart,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is 10x slower with the progress bar
Set-Location -LiteralPath $PSScriptRoot
$Root = $PSScriptRoot
$LogFile = Join-Path $Root "install.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }

function Step($n, $text) { Write-Host ""; Write-Host "[$n/7] $text" -ForegroundColor Cyan }
function Ok($text)       { Write-Host "  OK   $text" -ForegroundColor Green }
function Info($text)     { Write-Host "       $text" }
function Warn($text)     { Write-Host "  WARN $text" -ForegroundColor Yellow }
function Fail($text, $fix) {
    Write-Host ""
    Write-Host "  FAILED: $text" -ForegroundColor Red
    if ($fix) { Write-Host "  Fix:    $fix" -ForegroundColor Yellow }
    Write-Host "  Full log: $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
function Ask($question) {
    if ($Yes) { return $true }
    $a = Read-Host "$question [Y/n]"
    return ($a -eq "" -or $a -match "^[Yy]")
}
# Windows PowerShell 5.1 turns ANY captured stderr line from a native program (even a harmless Python
# FutureWarning) into a terminating error while $ErrorActionPreference is "Stop". Run native commands whose
# stderr we redirect through this, and judge success by $LASTEXITCODE instead.
function Invoke-Native([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $old }
}

Write-Host "=================================================================" -ForegroundColor Magenta
Write-Host "  VoxCPM2 Voice Server - Windows installer" -ForegroundColor Magenta
Write-Host "  Folder: $Root" -ForegroundColor Magenta
Write-Host "=================================================================" -ForegroundColor Magenta

# ------------------------------------------------------------------------------------------------
Step 1 "Checking this computer"
$os = Get-CimInstance Win32_OperatingSystem
Ok "$($os.Caption) (build $($os.BuildNumber))"
if ([int]$os.BuildNumber -lt 17763) { Fail "Windows 10 version 1809 or newer is required." "Update Windows." }

$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -eq "ARM64") {
    Warn "Windows on ARM: PyTorch support is limited; the CPU build will be used and may be slow."
    $Cpu = $true
} elseif ($arch -ne "AMD64") {
    Fail "64-bit Windows is required (found $arch)." ""
}

if ($Root -match "[^\x00-\x7F]") {
    Warn "The folder path contains non-English characters. If installation fails, move the folder to e.g. C:\VoxCPM2."
}
if ($Root.Length -gt 120) {
    Warn "The folder path is long ($($Root.Length) chars). If installation fails, move the folder to e.g. C:\VoxCPM2."
}

$drive = (Get-Item -LiteralPath $Root).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB, 1)
$needGB = 15
if ($SkipModel -or $ModelDir) { $needGB = 9 }
if ($freeGB -lt $needGB) {
    Fail "Only $freeGB GB free on drive $($drive.Name):, about $needGB GB is needed (PyTorch ~4 GB, model ~5 GB)." "Free some space or move this folder to a bigger drive."
}
Ok "$freeGB GB free on drive $($drive.Name):"

$ramGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 0)
if ($ramGB -lt 15) { Warn "$ramGB GB of RAM. 16 GB or more is recommended." } else { Ok "$ramGB GB of RAM" }

$gpuName = ""
$nvsmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $nvsmi -and (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) { $nvsmi = Get-Item "$env:SystemRoot\System32\nvidia-smi.exe" }
if ($nvsmi -and -not $Cpu) {
    try {
        $q = Invoke-Native { & $nvsmi.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null } | Select-Object -First 1
        if ($q) {
            $parts = $q.Split(",") | ForEach-Object { $_.Trim() }
            $gpuName = $parts[0]
            Ok "NVIDIA GPU: $($parts[0]), $($parts[1]), driver $($parts[2])"
            $vram = [int]($parts[1] -replace "[^0-9]", "")
            if ($vram -gt 0 -and $vram -lt 7500) { Warn "Under 8 GB of VRAM: the model may not fit. The server falls back gracefully; use -Cpu if loading fails." }
        }
    } catch { }
}
if (-not $gpuName -and -not $Cpu) {
    $other = (Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join ", "
    if ($other -match "Arc") {
        Info "Intel Arc GPU found ($other): using the Intel XPU build of PyTorch."
        if ($TorchBackend -eq "auto") { $TorchBackend = "xpu" }
    } else {
        Warn "No NVIDIA GPU found ($other). Installing the CPU build: it works, but speech is slow."
        $Cpu = $true
    }
}
if ($Cpu) { $TorchBackend = "cpu" }
Info "PyTorch build: $TorchBackend"

# voxcpm needs kaldifst, which has no 64-bit Windows wheel for Python 3.14+ (pip would try to compile it
# with CMake + Visual C++ and fail with "'cmake' is not recognized"). Keep to versions with ready-made wheels.
$pyMinor = 0
if ($PythonVersion -match "^3\.(\d+)") { $pyMinor = [int]$Matches[1] }
if ($pyMinor -lt 10 -or $pyMinor -gt 13) {
    Warn "Python $PythonVersion is not supported on Windows (VoxCPM2 dependencies have no ready-made packages for it). Using Python 3.11 instead."
    $PythonVersion = "3.11"
}

# ------------------------------------------------------------------------------------------------
Step 2 "Installing uv (Python manager)"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Info "Downloading uv from astral.sh ..."
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Fail "Could not install uv: $($_.Exception.Message)" "Check your internet connection, or install uv manually: https://docs.astral.sh/uv/getting-started/installation/"
    }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { Fail "uv was installed but cannot be found." "Close this window, open a new one and run the installer again." }
}
$uvVersion = (& uv --version) -replace "^uv\s+", ""
Ok "uv $uvVersion"
$vParts = ($uvVersion.Split(" ")[0]).Split(".")
if ([int]$vParts[0] -eq 0 -and [int]$vParts[1] -lt 7) {
    Info "Updating uv (an older version cannot pick PyTorch builds automatically)..."
    Invoke-Native { & uv self update 2>&1 } | Out-Null
}

# ------------------------------------------------------------------------------------------------
Step 3 "Creating the Python $PythonVersion environment (.venv)"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $Py) {
    Ok "Existing .venv found - reusing it"
} else {
    & uv venv .venv --python $PythonVersion
    if ($LASTEXITCODE -ne 0) { Fail "Creating the Python environment failed." "Check install.log. Deleting the .venv folder and running again often helps." }
    Ok "Created .venv"
}

# ------------------------------------------------------------------------------------------------
Step 4 "Installing PyTorch ($TorchBackend) and the VoxCPM2 engine (several GB, be patient)"
& uv pip install --python $Py --torch-backend $TorchBackend torch torchaudio
if ($LASTEXITCODE -ne 0) {
    Fail "Installing PyTorch failed." "Check your connection. For an old NVIDIA driver, update it (nvidia.com/drivers) or run with -TorchBackend cu126 / -Cpu."
}
& uv pip install --python $Py --torch-backend $TorchBackend -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Installing the VoxCPM2 engine failed." "Check install.log for the package that failed." }

$checkLines = @(Invoke-Native { & $Py -W ignore -c "import torch, voxcpm, soundfile; c=torch.cuda.is_available(); x=hasattr(torch,'xpu') and torch.xpu.is_available(); print('VOXCHECK', torch.__version__, 'CUDA' if c else ('XPU' if x else 'CPU'))" 2>&1 })
$checkCode = $LASTEXITCODE
$check = ($checkLines | Where-Object { $_ -like "VOXCHECK *" } | Select-Object -Last 1) -replace "^VOXCHECK ", ""
if ($checkCode -ne 0 -or -not $check) { Fail "The installed packages do not import: $(($checkLines | Select-Object -Last 3) -join ' | ')" "Delete the .venv folder and run the installer again." }
Ok "PyTorch $check"
if ($gpuName -and ($check -notmatch "CUDA")) {
    Warn "An NVIDIA GPU is present but PyTorch cannot use it. Update the NVIDIA driver, then run this installer again."
}

# ------------------------------------------------------------------------------------------------
Step 5 "Writing settings (.env)"
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Ok "Created .env from .env.example"
} else {
    Ok ".env already exists - keeping your settings"
}
function Set-EnvValue($key, $value) {
    $lines = Get-Content -LiteralPath $EnvFile
    $found = $false
    $lines = $lines | ForEach-Object {
        if ($_ -match "^\s*#?\s*$key=") { $found = $true; "$key=$value" } else { $_ }
    }
    if (-not $found) { $lines += "$key=$value" }
    [System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines)
}
if ($Port -gt 0) { Set-EnvValue "VOXCPM_PORT" $Port; Ok "Port set to $Port" }
if ($ModelDir) { Set-EnvValue "VOXCPM_MODEL_DIR" $ModelDir; Ok "Model folder set to $ModelDir" }
if ($Cpu) { Set-EnvValue "VOXCPM_DEVICE" "cpu" }

# ------------------------------------------------------------------------------------------------
Step 6 "Downloading the VoxCPM2 model (~5 GB, one time)"
if ($SkipModel) {
    Info "Skipped (-SkipModel). The server downloads it on first start, or run: .venv\Scripts\python scripts\download_model.py"
} else {
    & $Py scripts\download_model.py
    if ($LASTEXITCODE -ne 0) {
        Warn "The model download did not finish. Run the installer again (it resumes), or start the server - it downloads missing files itself."
    }
}

# ------------------------------------------------------------------------------------------------
Step 7 "Final checks and shortcuts"
& $Py scripts\doctor.py --quick
$Start = Join-Path $Root "start-windows.bat"
function New-Shortcut($path, $args, $windowStyle) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $Start
    $lnk.Arguments = $args
    $lnk.WorkingDirectory = $Root
    $lnk.WindowStyle = $windowStyle
    $lnk.Description = "VoxCPM2 Voice Server"
    $lnk.Save()
}
if ($DesktopShortcut -or (-not $Yes -and (Ask "Create a desktop shortcut?"))) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "VoxCPM2 Voice Server.lnk") "" 1
    Ok "Desktop shortcut created"
}
if ($Autostart) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath("Startup")) "VoxCPM2 Voice Server.lnk") "--no-open" 7
    Ok "The server now starts (minimised) when you log in. Remove it from shell:startup to undo."
}

$portShown = 8808
if ($Port -gt 0) { $portShown = $Port }
Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  Installed!" -ForegroundColor Green
Write-Host "  Start:        double-click start-windows.bat" -ForegroundColor Green
Write-Host "  Web UI:       http://127.0.0.1:$portShown" -ForegroundColor Green
Write-Host "  OpenAI API:   http://127.0.0.1:$portShown/v1" -ForegroundColor Green
Write-Host "  AI agents:    double-click integrate-windows.bat" -ForegroundColor Green
Write-Host "  Problems?     double-click doctor-windows.bat" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch { }

if (-not $Yes -and (Ask "Start the server now?")) {
    Start-Process -FilePath $Start -WorkingDirectory $Root
}
