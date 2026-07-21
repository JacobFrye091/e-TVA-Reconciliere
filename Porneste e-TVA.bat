@echo off
cd /d "%~dp0"
start "e-TVA Portal" /min python -m portal.run
start "e-TVA Reconciliere" /min python -m etva.main
