@echo off
rem Builds native\liveview.dll (the Live steps view renderer, our own C; the window only) with MSVC.
rem Plain /O2, no /arch or /fp:fast: runs on any x64 CPU.
setlocal
set VCVARS=
for %%E in (Community Professional Enterprise BuildTools) do (
  if exist "%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat"
)
if "%VCVARS%"=="" (echo build_liveview: no Visual Studio 2022 found & exit /b 1)
call "%VCVARS%" >nul
cd /d "%~dp0"
cl /nologo /O2 /LD liveview.c /Fe:liveview.dll || exit /b 1
del /q liveview.obj liveview.exp liveview.lib 2>nul
endlocal
