@echo off
rem DCS-Instructor setup: double-click once after downloading, and again any time (it only does what is missing).
rem Downloads this project's own Python and voice model, finds DCS, builds the aircraft data from your DCS install,
rem and asks before changing anything in DCS (Saved Games export/hook, optional DCS install patches).
rem Log of the steps: this window. Details: tools\bootstrap.ps1 and tools\setup.py
cd /d "%~dp0"
title DCS-Instructor setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\bootstrap.ps1"
if errorlevel 1 (
    echo.
    echo Setup stopped with an error. Fix the problem above and run SETUP.cmd again.
)
echo.
pause
