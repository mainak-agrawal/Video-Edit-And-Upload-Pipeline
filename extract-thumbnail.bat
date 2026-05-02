@echo off
cd /d "%~dp0"
python extract-thumbnail.py
echo.
echo ============================================================
echo  Finished. Press any key to close this window.
echo ============================================================
pause > nul
