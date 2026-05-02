@echo off
cd /d "%~dp0"
python merge-videos.py
echo.
echo ============================================================
echo  Finished. Press any key to close this window.
echo ============================================================
pause > nul
