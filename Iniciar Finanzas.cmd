@echo off
setlocal

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0main.py"
    exit /b %errorlevel%
)

if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" (
    "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" "%~dp0main.py"
    exit /b %errorlevel%
)

echo No se encontro Python 3.
echo Instala Python 3 o agrega Python al PATH, y vuelve a ejecutar este archivo.
pause
exit /b 1
