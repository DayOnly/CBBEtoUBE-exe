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

# Deploy dist\CBBEtoUBE to a tools folder SAFELY.
#
# Uses robocopy /E (copy, do NOT delete extras) -- never /MIR -- so a redeploy
# never removes runtime state the exe wrote next to itself (exclusions list,
# last-run log, last-failures, coverage sentinel). A /MIR deploy once wiped a
# user's exclusions; this script exists so that cannot happen again.
#
# Usage:
#   .\scripts\deploy_exe.ps1 -Dest "D:\path\to\MO2\tools\CBBEtoUBE"
#   .\scripts\deploy_exe.ps1 -Dest <path> -WhatIf     # preview only

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Dest,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$src = Join-Path $projectRoot "dist\CBBEtoUBE"
if (-not (Test-Path (Join-Path $src "CBBEtoUBE.exe"))) {
    throw "build not found: $src\CBBEtoUBE.exe (run scripts\build_exe.ps1 first)"
}

Write-Host "source: $src"
Write-Host "dest  : $Dest"

# /E     = copy subdirs incl. empty (NO /MIR -> extras in dest are KEPT)
# /COPY:DAT = data + attrs + timestamps (preserve build mtime for --incremental floor)
# /R:2 /W:2 = brief retry; /NFL /NDL /NP = quieter
$roboArgs = @($src, $Dest, "/E", "/COPY:DAT", "/R:2", "/W:2", "/NFL", "/NDL", "/NP")
if ($WhatIf) { $roboArgs += "/L" }   # list only, change nothing

& robocopy @roboArgs | Out-Host
$code = $LASTEXITCODE
# robocopy: 0-7 = success (8+ = failure). /E never deletes, so 0-3 is typical.
if ($code -ge 8) { throw "robocopy failed (exit $code)" }

if (-not $WhatIf) {
    $exe = Join-Path $Dest "CBBEtoUBE.exe"
    if (-not (Test-Path $exe)) { throw "deploy finished but exe missing: $exe" }
    $i = Get-Item $exe
    Write-Host ""
    Write-Host "DEPLOY OK (robocopy exit $code)"
    Write-Host ("  exe   : {0}  ({1} bytes, {2})" -f $exe, $i.Length, $i.LastWriteTime)
    Write-Host "  note  : runtime state files in the dest were preserved (/E, not /MIR)."
}
