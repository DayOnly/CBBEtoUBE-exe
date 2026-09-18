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

# Automated runner for AddUBERaces.pas via Mod Organizer 2.
#
# Why this exists:
#   SSEEdit launched directly from PowerShell sees only the vanilla Skyrim
#   Data folder - it doesn't get MO2's virtual file system, so the modlist's
#   plugins (including UBE_AllRace.esp) are invisible. The script then can't
#   find the UBE race records and does nothing.
#
#   Launching SSEEdit *through MO2* with the right arguments fixes that. MO2's
#   USVFS hooks the child process, the modlist's plugins are visible, and the
#   -AutoLoad + -script:AddUBERaces flags make SSEEdit auto-run our patcher
#   right after load completes.
#
# What this script does:
#   1. Deploys the latest AddUBERaces.pas into SSEEdit's Edit Scripts folder.
#   2. Adds (or refreshes) a custom executable entry in MO2's ModOrganizer.ini
#      named "xEdit - CBBEtoUBE Patcher" that launches SSEEdit with the right
#      -script and -AutoLoad flags.
#   3. Launches MO2 with a moshortcut to that entry, which fires SSEEdit
#      through the VFS, which auto-runs the script.
#
# Usage:
#   .\scripts\run_patcher.ps1            # configure + launch
#   .\scripts\run_patcher.ps1 -Configure # just configure MO2, don't launch
#

[CmdletBinding()]
param(
    [switch]$Configure
)

$ErrorActionPreference = "Stop"

$mo2Root        = if ($env:CBBE2UBE_MODS_ROOT) { $env:CBBE2UBE_MODS_ROOT } else { "D:\path\to\MO2" }
$mo2Exe         = Join-Path $mo2Root "ModOrganizer.exe"
$mo2Ini         = Join-Path $mo2Root "ModOrganizer.ini"
$sseeditExe     = Join-Path $mo2Root "tools\xEdit\SSEEdit.exe"
$scriptsFolder  = Join-Path $mo2Root "tools\xEdit\Edit Scripts"
$projectRoot    = Split-Path -Parent $PSScriptRoot
$sourceScript   = Join-Path $projectRoot "scripts\AddUBERaces.pas"

$executableTitle = "xEdit - CBBEtoUBE Patcher"
# This modlist uses a 'Stock Game' pattern where the real game files
# live in the MO2 instance's Stock Game\Data\ (separate from the Steam install).
# xEdit must be told that path via -D: or it will look at the Steam install
# (where the modlist's plugins don't exist) and fail to load them.
$stockGameData = Join-Path $mo2Root "Stock Game\Data"
$executableArgs = '-IKnowWhatImDoing -AutoLoad -script:AddUBERaces ' +
                  '-AllowMasterFilesEdit ' +
                  "-D:`"$stockGameData`""

foreach ($p in @($mo2Exe, $mo2Ini, $sseeditExe, $sourceScript)) {
    if (-not (Test-Path $p)) { throw "Required path not found: $p" }
}

# MO2 must be CLOSED before we modify its INI - otherwise it rewrites the INI
# from its in-memory state when it exits and our entry is silently lost.
$mo2Running = Get-Process -Name "ModOrganizer" -ErrorAction SilentlyContinue
if ($mo2Running) {
    Write-Host ""
    Write-Host "ERROR: Mod Organizer 2 is currently running (PID $($mo2Running.Id))." -ForegroundColor Red
    Write-Host ""
    Write-Host "MO2 overwrites ModOrganizer.ini from memory on exit, which would silently"
    Write-Host "erase the patcher entry we add. Close MO2 completely, then re-run this script."
    Write-Host ""
    exit 1
}

# 1. Refresh the script in xEdit's Edit Scripts folder
Copy-Item $sourceScript (Join-Path $scriptsFolder "AddUBERaces.pas") -Force
Write-Host "deployed AddUBERaces.pas -> $scriptsFolder"

# 2. Ensure our entry exists in MO2's customExecutables section.
#    The INI format is {index}\key=value with a [customExecutables] header
#    that has size=<count>. We read the file as text, find an existing entry
#    by title (idempotent), or append a new one.
$lines = [System.Collections.Generic.List[string]]::new()
Get-Content $mo2Ini -Encoding UTF8 | ForEach-Object { [void]$lines.Add($_) }

$inSection      = $false
$sectionStart   = -1
$sectionEnd     = $lines.Count
$existingIdx    = $null
$maxIdx         = 0
$sizeLineIdx    = -1
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
            if ($matches[2] -eq $executableTitle) {
                $existingIdx = $idx
            }
        } elseif ($line -match "^(\d+)\\") {
            $idx = [int]$matches[1]
            if ($idx -gt $maxIdx) { $maxIdx = $idx }
        }
    }
}

if (-not $inSection) {
    throw "[customExecutables] section not found in $mo2Ini"
}

if ($existingIdx -ne $null) {
    # Update arguments line for existing entry to keep it in sync.
    Write-Host "found existing entry $existingIdx for '$executableTitle'; refreshing arguments"
    for ($i = $sectionStart; $i -lt $sectionEnd; $i++) {
        if ($lines[$i] -match "^$existingIdx\\arguments=") {
            $lines[$i] = "$existingIdx\arguments=$executableArgs"
            break
        }
    }
} else {
    $newIdx = $maxIdx + 1
    Write-Host "adding new entry $newIdx for '$executableTitle'"
    # Insert the new block just before sectionEnd. Standard MO2 keys per entry:
    [string[]]$block = @(
        "$newIdx\title=$executableTitle"
        "$newIdx\binary=" + ($sseeditExe -replace '\\','/')
        "$newIdx\arguments=$executableArgs"
        "$newIdx\workingDirectory="
        "$newIdx\steamAppID="
        "$newIdx\hide=false"
        "$newIdx\ownicon=false"
        "$newIdx\toolbar=false"
    )
    $lines.InsertRange($sectionEnd, [System.Collections.Generic.IEnumerable[string]]$block)
    # Bump size= line
    if ($sizeLineIdx -ge 0) {
        $currentSize = [int]([regex]::Match($lines[$sizeLineIdx], "^size=(\d+)").Groups[1].Value)
        if ($newIdx -gt $currentSize) {
            $lines[$sizeLineIdx] = "size=$newIdx"
        }
    }
}

[System.IO.File]::WriteAllLines($mo2Ini, $lines)
Write-Host "wrote $mo2Ini"

if ($Configure) {
    Write-Host ""
    Write-Host "Configure-only mode. Launch MO2 and pick '$executableTitle' from the executable dropdown."
    exit 0
}

# 3. Launch via MO2 with a moshortcut so the executable runs under the VFS.
#    The empty instance name `:` means "current instance".
$shortcut = "moshortcut://:$executableTitle"
Write-Host ""
Write-Host "launching: $mo2Exe `"$shortcut`""
Write-Host ""
Write-Host "What to expect:"
Write-Host "  - MO2 starts SSEEdit through its VFS (so UBE_AllRace.esp is visible)."
Write-Host "  - SSEEdit auto-loads plugins (no dialog), then auto-runs AddUBERaces."
Write-Host "  - Watch the Messages tab fill with '+ ... added 00UBE_*Race' lines."
Write-Host "  - When you see 'AddUBERaces done', press Ctrl+S in SSEEdit to save."
Write-Host ""

& $mo2Exe $shortcut
