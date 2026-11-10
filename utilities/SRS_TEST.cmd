@echo off
rem Speaks the test phrase through the configured voice output (settings.json "voice.output" and "srs").
rem For SRS it first opens the SRS server and client (if not running); press Enter in this window to transmit.
cd /d "%~dp0.."
title DCS-Instructor SRS test
".venv\Scripts\python.exe" tools\srs_test.py
pause
