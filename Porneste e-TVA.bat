@echo off
cd /d "%~dp0"
start "e-TVA Platforma" /min python -m portal.run
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8990/
