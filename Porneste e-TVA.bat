@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Nu am gasit "python" in PATH. Instaleaza Python de la python.org
    echo si bifeaza "Add python.exe to PATH" la instalare, apoi reincearca.
    pause
    exit /b 1
)

echo Pornesc platforma e-TVA Reconciliere pe http://127.0.0.1:8990/ ...
echo Aceasta fereastra ramane deschisa cat timp serverul ruleaza.
echo Daca serverul se opreste cu o eroare, mesajul ramane vizibil aici.
echo.
start "e-TVA Platforma" cmd /k python -m portal.run

timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8990/
