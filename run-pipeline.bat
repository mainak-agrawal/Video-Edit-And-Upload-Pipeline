@echo off
cd /d "%~dp0"
python run-pipeline.py
echo.
echo ============================================================
echo  Pipeline finished. Press any key to close this window.
echo ============================================================
pause > nul
 