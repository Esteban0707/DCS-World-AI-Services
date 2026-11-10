@echo off
rem Shows the live cockpit stream from DCS (dcs/instructor_export.lua). Close the window to stop.
cd /d "%~dp0.."
title DCS-Instructor cockpit test
".venv\Scripts\python.exe" -m client.cockpit_receiver
pause
