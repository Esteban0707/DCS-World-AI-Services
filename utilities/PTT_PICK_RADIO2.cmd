@echo off
rem Optional second push-to-talk button: it always transmits on the aircraft's radio 2 (as SRS's radio 2 PTT).
rem Press it on your joystick and it is saved to settings.json "ptt.radio2". Pick a button other than SRS's own PTTs.
rem To remove it: tools\ptt_pick.py --radio2 --clear
cd /d "%~dp0.."
title DCS-Instructor push-to-talk button for radio 2
".venv\Scripts\python.exe" tools\ptt_pick.py --radio2
pause
