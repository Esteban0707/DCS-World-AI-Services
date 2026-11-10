@echo off
rem DCS-Instructor, local mode. Type in this window to talk to the instructor (a lesson command such as "continue" or "repeat" in your language, or any question).
rem Close COCKPIT_TEST first: both listen on the same port.
cd /d "%~dp0.."
title DCS-Instructor
".venv\Scripts\python.exe" -m instructor.main
pause
