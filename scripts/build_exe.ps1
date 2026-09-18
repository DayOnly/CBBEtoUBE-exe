# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# Build CBBEtoUBE.exe from CBBEtoUBE.spec via PyInstaller.
#
# Usage:
#   .\scripts\build_exe.ps1            # build (installs PyInstaller if missing)
#   .\scripts\build_exe.ps1 -Clean     # also wipe build\ and dist\ first
#
# Output: dist\CBBEtoUBE\CBBEtoUBE.exe  (a self-contained onedir bundle that
# embeds Python + numpy/scipy + the pynifly NiflyDLL.dll).

[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
Write-Host "project root: $projectRoot"

$spec = Join-Path $projectRoot "CBBEtoUBE.spec"
if (-not (Test-Path $spec)) { throw "spec not found: $spec" }

# 1. Build from the LOCKED toolchain, never from whatever `python` happens to be
# on PATH. requirements.txt asks for numpy>=1.24 and scipy>=1.10, and this script
# used to install "the latest PyInstaller" when it was missing, so a rebuild on
# another machine silently shipped different libraries -- a geometry change until
# a parity run says otherwise. #build-lock
$lock = Join-Path $projectRoot "requirements-build.lock"
if (-not (Test-Path $lock)) { throw "lock not found: $lock" }
$venv = Join-Path $projectRoot ".venv-build"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python not found on PATH (needed once, to create .venv-build)." }
    Write-Host "creating .venv-build from requirements-build.lock ..."
    & python -m venv $venv
    if (-not $?) { throw "python -m venv failed." }
    & $venvPy -m pip install --require-hashes --no-input -r $lock
    if (-not $?) { throw "pip install --require-hashes failed; the lock does not fit this platform." }
}

# The environment must still MATCH the lock: an upgrade inside it is exactly the
# drift this guards against, and it would otherwise ship unnoticed.
# `pip freeze --all`, not plain freeze: freeze HIDES setuptools, which the bundle
# ships and the lock therefore pins, so a plain freeze reports it missing on
# every build. pip and wheel are the venv's own plumbing and are not pinned.
$ignore = @("pip", "wheel", "distribute", "pkg-resources")
$want = @(Get-Content $lock | Where-Object { $_ -match "^[A-Za-z0-9_.-]+==" } |
          ForEach-Object { ($_ -split "\s")[0].ToLower().Replace("_", "-") })
$have = @(& $venvPy -m pip freeze --all | Where-Object { $_ -match "^[A-Za-z0-9_.-]+==" } |
          ForEach-Object { $_.Trim().ToLower().Replace("_", "-") } |
          Where-Object { $ignore -notcontains ($_ -split "==")[0] })
$missing = @($want | Where-Object { $have -notcontains $_ })
$extra = @($have | Where-Object { $want -notcontains $_ })
if ($missing.Count -gt 0 -or $extra.Count -gt 0) {
    Write-Host "BUILD REFUSED: .venv-build no longer matches requirements-build.lock"
    foreach ($m in $missing) { Write-Host "  the lock pins, the venv lacks: $m" }
    foreach ($e in $extra) { Write-Host "  the venv has, the lock does not: $e" }
    throw "toolchain mismatch. Delete .venv-build to rebuild it from the lock, or edit the lock deliberately."
}
Write-Host "python: $venvPy (locked)"
$piVer = (& $venvPy -c "import PyInstaller; print(PyInstaller.__version__)")
Write-Host "PyInstaller: $piVer (locked)"

# 1b. Build stamp: CBBEtoUBE.spec writes src/_build_stamp.py itself now, from
# scripts/build_identity.py, so this script and a direct `pyinstaller
# CBBEtoUBE.spec` stamp a build the same way. #build-identity

# Reproducible build: the spec pins the RUNTIME hash seed; this pins the
# BUILD's, so two builds of the same tree produce the same .pyc bytes.
$env:PYTHONHASHSEED = "1"

# Reproducible to the byte: SOURCE_DATE_EPOCH pins every build time -- the
# stamp's built_utc, and the PE header timestamps PyInstaller writes with the
# checksum they feed. Left to the wall clock, two builds of one commit differed
# in the exe alone, by 180 bytes in three header fields and the stamp; pinned,
# two builds were identical across all 1,121 bundle files (MEASURED 2026-09-16).
# The value is the commit's own committer time, so a rebuild of that commit
# anywhere -- the release workflow's runner included -- reproduces the tracked
# bundle, and `release_gate.py rebuild-check` can prove it. A value already in
# the environment is kept and reported; the gate will refuse a build pinned to
# anything but the commit's time. #reproducible-build
if (-not $env:SOURCE_DATE_EPOCH) {
    $epoch = ""
    try { $epoch = [string](& git -C $projectRoot log -1 --format=%ct HEAD) } catch { $epoch = "" }
    if ($LASTEXITCODE -eq 0 -and $epoch -match "^\d+$") {
        $env:SOURCE_DATE_EPOCH = $epoch
        Write-Host "SOURCE_DATE_EPOCH: $epoch (HEAD's commit time, so this build reproduces)"
    } else {
        Write-Host "SOURCE_DATE_EPOCH: not set -- no commit to pin to, so this build's times are the wall clock"
    }
} else {
    Write-Host "SOURCE_DATE_EPOCH: $env:SOURCE_DATE_EPOCH (already set in the environment)"
}

# 2. Optional clean.
if ($Clean) {
    foreach ($d in @("build", "dist")) {
        $p = Join-Path $projectRoot $d
        if (Test-Path $p) { Write-Host "removing $p"; Remove-Item -Recurse -Force $p }
    }
}

# 3. Build.
Write-Host ""
Write-Host "building (this can take a few minutes the first time)..."
& $venvPy -m PyInstaller --noconfirm --clean $spec
if (-not $?) { throw "PyInstaller build failed." }

# 4. Report.
$exe = Join-Path $projectRoot "dist\CBBEtoUBE\CBBEtoUBE.exe"
if (-not (Test-Path $exe)) { throw "build finished but exe missing: $exe" }
Write-Host ""
Write-Host "BUILD OK"
$versionTxt = Join-Path (Split-Path -Parent $exe) "VERSION.txt"
if (Test-Path $versionTxt) { Write-Host "  build : $(Get-Content $versionTxt)" }
Write-Host "  exe   : $exe"
Write-Host "  folder: $(Split-Path -Parent $exe)"
Write-Host ""
# Send an EXISTING install to deploy_exe.ps1, not the installer. The installer
# is for FIRST registration; naming it here pointed sessions at a script that
# used to wipe the live tools directory -- settings file, its backups and the
# run log with it -- and that still rewrites the MO2 INI, which a redeploy has
# no business touching.
Write-Host "Next:"
Write-Host "  already registered?  .\scripts\deploy_exe.ps1 -Dest <the path in"
Write-Host "                       ModOrganizer.ini's customExecutables binary=>"
Write-Host "                       robocopy /E, so your settings and logs survive"
Write-Host "  first time?          .\scripts\install_mo2_entry.ps1 -Mo2Root <modlist>"
