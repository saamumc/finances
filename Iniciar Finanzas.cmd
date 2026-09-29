@echo off
setlocal

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    goto :check_deps
)

if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" (
    set "PYTHON="%LocalAppData%\Python\pythoncore-3.14-64\python.exe""
    goto :check_deps
)

echo No se encontro Python 3.
echo Instala Python 3.12 o superior y agrega Python al PATH.
pause
exit /b 1

:check_deps
%PYTHON% -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias de Finanzas...
    %PYTHON% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)

%PYTHON% "%~dp0main.py"
exit /b %errorlevel%
