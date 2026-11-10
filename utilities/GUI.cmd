@echo off
rem The DCS-Instructor GUI without DCS: voice settings and tests, audio devices. If the instructor is already
rem running (DCS-Instructor.bat), this only opens its GUI window. Close this console (or Ctrl+C) to stop.
cd /d "%~dp0.."
title DCS-Instructor GUI
".venv\Scripts\python.exe" tools\open_gui.py
