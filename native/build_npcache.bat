@echo off
rem Builds native\npcache.pyd (the numpy large-block cache, our own C; #numpy-block-cache) with MSVC, against the
rem headers of the BUILD venv's Python and numpy -- the ones the exe bundles. Falls back to `python` on PATH.
setlocal
set VCVARS=
for %%E in (Community Professional Enterprise BuildTools) do (
  if exist "%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\%%E\VC\Auxiliary\Build\vcvars64.bat"
)
if "%VCVARS%"=="" (echo build_npcache: no Visual Studio 2022 found & exit /b 1)
call "%VCVARS%" >nul
cd /d "%~dp0"
set PY=python
if exist "%~dp0..\.venv-build\Scripts\python.exe" set "PY=%~dp0..\.venv-build\Scripts\python.exe"
rem cmd cannot run a quoted command with quoted arguments inside FOR /F, so Python writes the paths out as SETs.
"%PY%" "%~dp0npcache_paths.py" > "%~dp0npcache_paths.cmd" || exit /b 1
call "%~dp0npcache_paths.cmd"
del /q "%~dp0npcache_paths.cmd" 2>nul
cl /nologo /O2 /LD npcache.c /I"%PYINC%" /I"%NPINC%" /Fe:npcache.pyd /link /LIBPATH:"%PYLIB%" || exit /b 1
del /q npcache.obj npcache.exp npcache.lib 2>nul
endlocal
