@echo off
rem DCS install patches of DCS_AI_Service (client\dcs_patches.py): silence DCS's own ATC voice (speech.lua) and the
rem native command bridge (RadioCommandDialogsPanel.lua). Close DCS first. After every DCS update run "update" again.
rem   DCS_PATCHES.cmd status | update | remove
cd /d "%~dp0.."
title DCS_AI_Service - DCS patches
set ACTION=%1
if "%ACTION%"=="" set ACTION=status
".venv\Scripts\python.exe" -m client.dcs_patches %ACTION%
pause
