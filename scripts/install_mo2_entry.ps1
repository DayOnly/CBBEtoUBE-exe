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

# Register CBBEtoUBE.exe as a one-click executable entry in a Mod Organizer 2
# instance, so the user can run the whole CBBE/3BA -> UBE conversion from MO2's
# executable dropdown.
#
# Usage:
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "D:\path\to\MO2"
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "D:\path\to\MO2" -InstallTools
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "D:\path\to\MO2" -ExePath "C:\Tools\CBBEtoUBE\CBBEtoUBE.exe"
#
# What it does:
#   1. (optional, -InstallTools) copies the built dist\CBBEtoUBE bundle into
#      <Mo2Root>\tools\CBBEtoUBE\ so the exe lives inside the instance.
#   2. Adds (or refreshes) a [customExecutables] entry named "CBBEtoUBE
#      Converter" that points at the exe, with workingDirectory set to the
#      instance root. The converter discovers the modpack by finding
#      ModOrganizer.ini from its working directory, so that handoff is all it
#      needs - no per-machine paths baked in.
#
# MO2 MUST be closed: it rewrites ModOrganizer.ini from memory on exit and
# would silently erase any entry we add while it is running.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Mo2Root,
    [string]$ExePath,
    [switch]$InstallTools,
    [string]$Title = "CBBEtoUBE Converter",
    # Refresh this [customExecutables] index explicitly. Only needed when an
    # instance carries several entries for the same tool and you want a
    # specific one; normally the binary-path match below finds it.
    [int]$Index
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$mo2Ini = Join-Path $Mo2Root "ModOrganizer.ini"

if (-not (Test-Path $mo2Ini)) {
    throw "ModOrganizer.ini not found under -Mo2Root: $mo2Ini"
}

# Resolve the exe to register.
if (-not $ExePath) {
    $ExePath = Join-Path $projectRoot "dist\CBBEtoUBE\CBBEtoUBE.exe"
}

# Optionally copy the whole onedir bundle into the instance's tools folder and
# register the copied exe instead (keeps the tool self-contained with the list).
if ($InstallTools) {
    $srcBundle = Split-Path -Parent $ExePath
    if (-not (Test-Path (Join-Path $srcBundle "CBBEtoUBE.exe"))) {
        throw "no built bundle at $srcBundle - run .\scripts\build_exe.ps1 first."
    }
    $destBundle = Join-Path $Mo2Root "tools\CBBEtoUBE"
    Write-Host "copying bundle -> $destBundle"
    # NEVER wipe the destination: on a live instance it holds the user's
    # CBBEtoUBE_settings.json, its backups and the last-run log. deploy_exe.ps1
    # copies with robocopy /E (extras in dest are kept) and takes a settings
    # snapshot first -- the same path every redeploy uses.
    & (Join-Path $PSScriptRoot "deploy_exe.ps1") -Dest $destBundle
    if (-not $?) { throw "deploy_exe.ps1 failed" }
    $ExePath = Join-Path $destBundle "CBBEtoUBE.exe"
}

if (-not (Test-Path $ExePath)) {
    throw "exe not found: $ExePath - build it with .\scripts\build_exe.ps1 (or pass -ExePath)."
}
Write-Host "registering exe: $ExePath"

# MO2 must be closed.
$mo2Running = Get-Process -Name "ModOrganizer" -ErrorAction SilentlyContinue
if ($mo2Running) {
    Write-Host ""
    Write-Host "ERROR: Mod Organizer 2 is running (PID $($mo2Running.Id))." -ForegroundColor Red
    Write-Host "Close MO2 completely, then re-run this script (MO2 overwrites its"
    Write-Host "INI from memory on exit and would erase the entry we add)."
    exit 1
}

# Values for the entry. MO2 stores paths with forward slashes in the INI.
$binaryVal  = ($ExePath -replace '\\', '/')
$workdirVal = ($Mo2Root -replace '\\', '/')
# Normalised form used to recognise an entry that ALREADY points at this exe.
$binaryNorm = $binaryVal.TrimEnd('/')

# Read the INI as text lines and locate [customExecutables].
$lines = [System.Collections.Generic.List[string]]::new()
Get-Content $mo2Ini -Encoding UTF8 | ForEach-Object { [void]$lines.Add($_) }

$inSection    = $false
$sectionStart = -1
$sectionEnd   = $lines.Count
$existingIdx  = $null
$titleIdx     = $null
$binaryIdx    = $null
$maxIdx       = 0
$sizeLineIdx  = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if ($line -match "^\[customExecutables\]") {
        $inSection = $true
        $sectionStart = $i
        continue
    }
    if ($inSection -and $line -match "^\[") {
        $sectionEnd = $i
        break
    }
    if ($inSection) {
        if ($line -match "^size=(\d+)") {
            $sizeLineIdx = $i
        } elseif ($line -match "^(\d+)\\title=(.*)$") {
            $idx = [int]$matches[1]
            if ($idx -gt $maxIdx) { $maxIdx = $idx }
            # Case-INSENSITIVE and trimmed: an MO2 title is user-editable text.
            if ($matches[2].Trim() -ieq $Title.Trim()) { $titleIdx = $idx }
        } elseif ($line -match "^(\d+)\\binary=(.*)$") {
            $idx = [int]$matches[1]
            if ($idx -gt $maxIdx) { $maxIdx = $idx }
            # MATCH ON THE BINARY FIRST. Matching only an EXACT title appended a
            # SECOND entry whenever the live title differed from this script's
            # default by so much as a word -- and it does: the live entry reads
            # `title=CBBEtoUBE` while the default here is "CBBEtoUBE Converter".
            # MO2 then shows two launchers for one tool, `size=` grows on every
            # re-run, and the one the user clicks may be the stale one. The
            # binary path is what actually identifies the entry.
            $binHere = $matches[2].Trim()
            if ($binHere.StartsWith("@ByteArray(") -and $binHere.EndsWith(")")) {
                $binHere = $binHere.Substring(11, $binHere.Length - 12)
            }
            $binHere = $binHere.Replace("\\", "/").Replace("\", "/").TrimEnd("/")
            if ($binHere -ieq $binaryNorm) { $binaryIdx = $idx }
        } elseif ($line -match "^(\d+)\\") {
            $idx = [int]$matches[1]
            if ($idx -gt $maxIdx) { $maxIdx = $idx }
        }
    }
}

# Precedence: an explicit -Index, then the entry ALREADY pointing at this exe,
# then a title match. Anything else is a genuinely new registration.
if ($PSBoundParameters.ContainsKey("Index")) {
    $existingIdx = $Index
    Write-Host "using -Index $Index"
} elseif ($null -ne $binaryIdx) {
    $existingIdx = $binaryIdx
    Write-Host "matched existing entry $binaryIdx by binary path"
} elseif ($null -ne $titleIdx) {
    $existingIdx = $titleIdx
    Write-Host "matched existing entry $titleIdx by title"
}

if (-not $inSection) {
    throw "[customExecutables] section not found in $mo2Ini"
}

if ($null -ne $existingIdx) {
    # Report the entry's OWN title, not this script's default -- when the match
    # came from the binary path those differ, and printing the default would
    # claim we refreshed an entry that is not the one on screen in MO2. The
    # title itself is deliberately NOT rewritten: the user may have renamed it.
    $liveTitle = $Title
    for ($i = $sectionStart; $i -lt $sectionEnd; $i++) {
        if ($lines[$i] -match "^$existingIdx\\title=(.*)$") { $liveTitle = $matches[1] }
    }
    Write-Host "refreshing existing entry $existingIdx ('$liveTitle')"
    for ($i = $sectionStart; $i -lt $sectionEnd; $i++) {
        if ($lines[$i] -match "^$existingIdx\\binary=") {
            $lines[$i] = "$existingIdx\binary=$binaryVal"
        } elseif ($lines[$i] -match "^$existingIdx\\workingDirectory=") {
            $lines[$i] = "$existingIdx\workingDirectory=$workdirVal"
        } elseif ($lines[$i] -match "^$existingIdx\\arguments=") {
            $lines[$i] = "$existingIdx\arguments="
        }
    }
} else {
    $newIdx = $maxIdx + 1
    Write-Host "adding new entry $newIdx ('$Title')"
    [string[]]$block = @(
        "$newIdx\title=$Title"
        "$newIdx\binary=$binaryVal"
        "$newIdx\arguments="
        "$newIdx\workingDirectory=$workdirVal"
        "$newIdx\steamAppID="
        "$newIdx\hide=false"
        "$newIdx\ownicon=false"
        "$newIdx\toolbar=false"
    )
    $lines.InsertRange($sectionEnd, [System.Collections.Generic.IEnumerable[string]]$block)
    if ($sizeLineIdx -ge 0) {
        $currentSize = [int]([regex]::Match($lines[$sizeLineIdx], "^size=(\d+)").Groups[1].Value)
        if ($newIdx -gt $currentSize) { $lines[$sizeLineIdx] = "size=$newIdx" }
    }
}

[System.IO.File]::WriteAllLines($mo2Ini, $lines)
Write-Host "wrote $mo2Ini"
Write-Host ""
Write-Host "DONE. Open MO2, pick '$Title' from the executable dropdown, and Run."
Write-Host "When it finishes, refresh MO2 and enable 'CBBEtoUBE Auto' + its"
Write-Host "CBBE_to_UBE_Combined.esp. (Close Skyrim first so the texture atlas"
Write-Host "isn't locked.)"
