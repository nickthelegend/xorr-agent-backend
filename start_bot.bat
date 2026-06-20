@echo off
REM ============================================================
REM  XORR bot keep-alive launcher (Windows)
REM  Auto-restarts the bot if it ever exits/crashes so it survives
REM  unattended. Full python path + Start-Sleep so it works under
REM  Task Scheduler too (no PATH / no console needed).
REM  To stop: end the XORR_Bot scheduled task, or close this window.
REM ============================================================
cd /d %~dp0
set "PYEXE=C:\Users\testi\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
:loop
echo.
echo [%date% %time%] Starting XORR bot (%PYEXE% -u run.py --spot)...
"%PYEXE%" -u run.py --spot
echo [%date% %time%] Bot exited (code %errorlevel%). Restarting in 5s...
powershell -NoProfile -Command "Start-Sleep -Seconds 5"
goto loop
