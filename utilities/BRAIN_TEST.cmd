@echo off
rem Voice loop with the brain: hold the instructor push-to-talk button, ask, and the instructor answers on SRS.
cd /d "%~dp0.."
title DCS-Instructor brain test
".venv\Scripts\python.exe" tools\dictation_test.py --brain
pause
