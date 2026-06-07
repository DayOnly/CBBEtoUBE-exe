# Register CBBEtoUBE.exe as a one-click executable entry in a Mod Organizer 2
# instance, so the user can run the whole CBBE/3BA -> UBE conversion from MO2's
# executable dropdown.
#
# Usage:
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "<MODLIST>"
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "<MODLIST>" -InstallTools
#   .\scripts\install_mo2_entry.ps1 -Mo2Root "<MODLIST>" -ExePath "C:\Tools\CBBEtoUBE\CBBEtoUBE.exe"
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
    [string]$Title = "CBBEtoUBE Converter"
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
    if (Test-Path $destBundle) { Remove-Item -Recurse -Force $destBundle }
    New-Item -ItemType Directory -Force -Path $destBundle | Out-Null
    Copy-Item -Recurse -Force (Join-Path $srcBundle "*") $destBundle
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

# Read the INI as text lines and locate [customExecutables].
$lines = [System.Collections.Generic.List[string]]::new()
Get-Content $mo2Ini -Encoding UTF8 | ForEach-Object { [void]$lines.Add($_) }

$inSection    = $false
$sectionStart = -1
$sectionEnd   = $lines.Count
$existingIdx  = $null
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
            if ($matches[2] -eq $Title) { $existingIdx = $idx }
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
    Write-Host "refreshing existing entry $existingIdx ('$Title')"
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
