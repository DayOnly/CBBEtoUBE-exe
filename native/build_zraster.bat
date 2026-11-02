@echo off
rem Builds native\zraster.dll (the body-zap visibility rasteriser, our own C) with MSVC.
rem Plain /O2, no /arch or /fp:fast: runs on any x64 CPU, and the +/-inf buffer tests stay exact.
setlocal
set VCVARS=
for %%E in (Community Professional Enterprise BuildTools) do (
  if exist "%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat"
)
if "%VCVARS%"=="" (echo build_zraster: no Visual Studio 2022 found & exit /b 1)
call "%VCVARS%" >nul
cd /d "%~dp0"
cl /nologo /O2 /LD zraster.c /Fe:zraster.dll || exit /b 1
del /q zraster.obj zraster.exp zraster.lib 2>nul
endlocal
