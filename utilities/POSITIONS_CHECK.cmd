@echo off
rem Check the switch position table live: flip switches in DCS and confirm each label in this window
rem (Enter = right, n = wrong, s = skip). Close the instructor first: it uses the same cockpit port.
cd /d "%~dp0.."
title DCS-Instructor switch positions check
".venv\Scripts\python.exe" tools\positions_check.py
pause
