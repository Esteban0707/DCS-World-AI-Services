@echo off
rem DCS_AI_Service: double-click to start everything (open DCS yourself). Checks the DCS export/hook and the
rem aircraft libraries, opens SRS (server + client), the GUI, the Chrome dictation window and the agents (Services in
rem the GUI); the black box records the flight and every cockpit control touched (logs\sessions). Tools: utilities\
rem Tune an agent's frequency, hold the push-to-talk and speak. Close the GUI window to stop everything.
rem Log: logs\launcher.log
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto setup
if not exist "settings.json" goto setup
start "" ".venv\Scripts\pythonw.exe" tools\launcher.py
exit /b 0
:setup
echo DCS-Instructor is not set up yet: run SETUP.cmd first (double-click it).
pause
