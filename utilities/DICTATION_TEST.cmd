@echo off
rem Voice loop test: hold the instructor push-to-talk button, speak, and the instructor repeats what it understood.
cd /d "%~dp0.."
title DCS-Instructor dictation test
".venv\Scripts\python.exe" tools\dictation_test.py
pause
