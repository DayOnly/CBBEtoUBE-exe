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

# 1. Pick a Python and make sure PyInstaller is available in it.
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python not found on PATH." }
Write-Host "python: $($py.Source)"

& python -c "import PyInstaller" 2>$null
if (-not $?) {
    Write-Host "PyInstaller not installed; installing into this interpreter..."
    & python -m pip install --upgrade pyinstaller
    if (-not $?) { throw "pip install pyinstaller failed." }
}
$piVer = (& python -c "import PyInstaller; print(PyInstaller.__version__)")
Write-Host "PyInstaller: $piVer"

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
& python -m PyInstaller --noconfirm --clean $spec
if (-not $?) { throw "PyInstaller build failed." }

# 4. Report.
$exe = Join-Path $projectRoot "dist\CBBEtoUBE\CBBEtoUBE.exe"
if (-not (Test-Path $exe)) { throw "build finished but exe missing: $exe" }
Write-Host ""
Write-Host "BUILD OK"
Write-Host "  exe   : $exe"
Write-Host "  folder: $(Split-Path -Parent $exe)"
Write-Host ""
Write-Host "Next: register it as an MO2 executable with"
Write-Host "  .\scripts\install_mo2_entry.ps1 -Mo2Root <path-to-your-modlist>"
