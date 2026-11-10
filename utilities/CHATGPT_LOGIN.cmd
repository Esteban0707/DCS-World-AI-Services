@echo off
rem Connect the instructor to your ChatGPT plan (Sign in with ChatGPT), choose the model and test one answer.
rem Optional: CHATGPT_LOGIN.cmd --model gpt-6.1-sol
cd /d "%~dp0.."
title DCS-Instructor ChatGPT sign-in
".venv\Scripts\python.exe" tools\chatgpt_login.py %*
pause
