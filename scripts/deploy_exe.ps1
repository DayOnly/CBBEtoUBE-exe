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

# Snapshot the live settings file BEFORE the copy. The recipe a pack was
# built with is what every in-game verdict is scored against, and until
# 2026-09-01 that copy was made by hand under four naming schemes. One
# scheme, taken by the deploy itself, newest 10 kept.
$settings = Join-Path $Dest "CBBEtoUBE_settings.json"
if ((Test-Path $settings) -and -not $WhatIf) {
    $snap = "$settings.prebuild-" + (Get-Date).ToString("yyyyMMdd-HHmmss")
    Copy-Item -Path $settings -Destination $snap
    Write-Host "settings snapshot: $(Split-Path -Leaf $snap)"
    Get-ChildItem -Path $Dest -Filter "CBBEtoUBE_settings.json.prebuild-*" |
        Sort-Object Name -Descending | Select-Object -Skip 10 |
        ForEach-Object { Remove-Item -Path $_.FullName; Write-Host "pruned $($_.Name)" }
}

# RUNTIME STATE THE EXE WRITES NEXT TO ITSELF -- excluded from the copy.
# "/E is not /MIR" protects against DELETION, never against OVERWRITE: /E still
# replaces a dest file whose name exists in the SOURCE, and the source acquires
# these the moment anyone RUNS the exe out of dist\. A bare `--help` leaves a
# CBBEtoUBE_last_run.log there; launching the GUI from dist would leave a
# CBBEtoUBE_settings.json. The next deploy then copies those over the live ones.
# MEASURED 2026-09-02 on a throwaway instance: a sentinel run log in the
# destination came back holding dist's `--help` output. The settings file
# survived that test only because dist happened not to have one -- which is
# luck, not protection, and losing it is the class
# feedback_deployed_build_runs_at_defaults exists for.
$stateFiles = @(
    "CBBEtoUBE_settings.json",
    "CBBEtoUBE_exclusions.json",
    "CBBEtoUBE_last_run.log",
    "CBBEtoUBE_last_failures.json",
    "CBBEtoUBE_settings.json.bak-*",
    "CBBEtoUBE_settings.json.prebuild-*"
)

# /E     = copy subdirs incl. empty (NO /MIR -> extras in dest are KEPT)
# /COPY:DAT = data + attrs + timestamps (preserve build mtime for --incremental floor)
# /XF    = never copy the runtime state above OVER the deployed copy
# /R:2 /W:2 = brief retry; /NFL /NDL /NP = quieter
$roboArgs = @($src, $Dest, "/E", "/COPY:DAT", "/XF") + $stateFiles +
            @("/R:2", "/W:2", "/NFL", "/NDL", "/NP")
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
    Write-Host "  note  : runtime state (settings, backups, logs) left untouched:"
    Write-Host "          /E does not delete dest extras, and /XF stops a copy in"
    Write-Host "          the SOURCE bundle overwriting the deployed one."
}
