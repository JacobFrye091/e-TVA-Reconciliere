@echo off
cd /d "%~dp0"

set "PY=python"
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

"%PY%" -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo Pachetele Python necesare lipsesc pentru "%PY%" - le instalez automat acum...
    echo Aceasta poate dura un minut la prima rulare.
    echo.
    "%PY%" -m pip install -r requirements.txt
    echo.
    "%PY%" -c "import flask" >nul 2>nul
    if errorlevel 1 (
        echo.
        echo Instalarea automata a esuat - vezi erorile afisate mai sus.
        echo Sistemul poate avea mai multe instalari de Python -
        echo verifica ce "python" se deschide din Explorer.
        echo.
        echo Aceasta fereastra ramane deschisa - inchide-o manual dupa ce citesti mesajul.
        ping -n 999 127.0.0.1 >nul
        exit /b 1
    )
    echo Pachetele au fost instalate cu succes.
    echo.
)

echo Pornesc platforma e-TVA Reconciliere pe http://127.0.0.1:8990/ ...
echo Serverul ruleaza intr-o fereastra separata, numita "e-TVA Platforma".
echo Daca acea fereastra nu apare sau se inchide, eroarea e vizibila acolo.
echo.
start "e-TVA Platforma" cmd /k %PY% -m portal.run

echo Astept ca serverul sa raspunda ^(pana la 20 secunde^) ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 40;$i++){try{Invoke-WebRequest -Uri 'http://127.0.0.1:8990/' -UseBasicParsing -TimeoutSec 1 | Out-Null; $ok=$true; break}catch{Start-Sleep -Milliseconds 500}}; if(-not $ok){exit 1}"
if errorlevel 1 (
    echo.
    echo Serverul nu a raspuns dupa 20 de secunde.
    echo Verifica fereastra "e-TVA Platforma" - acolo se vede eroarea exacta.
    echo.
    echo Aceasta fereastra ramane deschisa - inchide-o manual dupa ce citesti mesajul.
    ping -n 999 127.0.0.1 >nul
    exit /b 1
)

start "" http://127.0.0.1:8990/
