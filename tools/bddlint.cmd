@echo off
REM Windows launcher: `bddlint lint features\`
REM Prefers the py launcher, which is what a standard python.org install
REM provides and which picks a correct interpreter version; falls back to
REM python.exe on PATH for stripped-down or Store installs.
setlocal
set "HERE=%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%HERE%bddlint.py" %*
) else (
    python "%HERE%bddlint.py" %*
)
exit /b %ERRORLEVEL%
