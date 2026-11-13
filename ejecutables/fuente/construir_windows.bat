@echo off
rem Genera ejecutables\pasarela.exe y ejecutables\cliente.exe para Windows.
rem Doble clic en un Windows que tenga Python con pybullet y UR3e_rg2 instalados
rem (el mismo entorno con el que funciona src\clienteTLS.py).
cd /d "%~dp0"

set PY=python
if exist ..\..\.venv\Scripts\python.exe set PY=..\..\.venv\Scripts\python.exe

%PY% -c "import pybullet, UR3e_rg2" 2>nul
if errorlevel 1 (
    echo Falta pybullet o UR3e_rg2 en este Python. Instala primero:
    echo     %PY% -m pip install numpy ur3e_rg2-0.1.0-py3-none-any.whl
    pause
    exit /b 1
)

%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 %PY% -m pip install pyinstaller

%PY% -m PyInstaller --noconfirm --clean --distpath .. --workpath build ejecutables.spec
if errorlevel 1 (
    echo.
    echo La generacion ha fallado: mira el error de arriba.
    pause
    exit /b 1
)
echo.
echo Hecho: pasarela.exe y cliente.exe estan en la carpeta ejecutables
pause
