@echo off
setlocal
cd /d "%~dp0"

title FUTMNG - FIFA 17 Ultimate Team Database

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0app\FUTMNG.py" %*
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0app\FUTMNG.py" %*
    goto :eof
)

echo.
echo [FUTMNG] Python 3 est introuvable.
echo Installe Python 3 puis relance FUTMNG.
echo.
pause
