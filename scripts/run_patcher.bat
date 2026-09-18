@echo off
REM Double-click wrapper for run_patcher.ps1.
REM
REM cmd.exe won't execute .ps1 files directly (it opens them in the default
REM editor). This wrapper invokes PowerShell explicitly with the right flags.
REM
REM Drag this file to taskbar or pin to start for one-click access.

setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%run_patcher.ps1"

powershell -ExecutionPolicy Bypass -NoProfile -File "%PS1%" %*

REM Pause so you can read any output before the window closes.
pause
