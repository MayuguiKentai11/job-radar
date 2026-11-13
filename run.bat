@echo off
REM JobRadar - doble clic para iniciar
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual por primera vez...
    python -m venv .venv || (echo ERROR: instala Python 3.11+ desde python.org & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo ERROR instalando dependencias & pause & exit /b 1)
)
echo.
echo  JobRadar iniciado en http://localhost:8000  (cierra esta ventana para detenerlo)
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
