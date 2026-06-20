@echo off
REM ============================================================
REM  XORR bot keep-alive launcher (Windows)
REM  Double-click this file (or run it in a terminal you LEAVE OPEN).
REM  It runs the bot and AUTO-RESTARTS it if it ever exits/crashes,
REM  so it survives unattended for the competition.
REM  To stop: just close this window.
REM ============================================================
cd /d %~dp0
:loop
echo.
echo [%date% %time%] Starting XORR bot (python -u run.py --spot)...
python -u run.py --spot
echo [%date% %time%] Bot exited (code %errorlevel%). Restarting in 5s... (close window to stop)
timeout /t 5 /nobreak >nul
goto loop
