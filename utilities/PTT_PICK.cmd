@echo off
rem Choose the instructor's push-to-talk button: press it on your joystick and it is saved to settings.json.
rem Pick a button other than SRS's own PTT.
cd /d "%~dp0.."
title DCS-Instructor push-to-talk button
".venv\Scripts\python.exe" tools\ptt_pick.py
pause
