@echo off
rem Rebuild the F-5E-3 library: the DCS manuals (English) plus every .pdf, .md or .txt in modules\F-5E-3\documents.
rem Run it again after adding or changing a document there. The DCS folder comes from settings.json "dcs.install".
cd /d "%~dp0.."
title DCS-Instructor library build
".venv\Scripts\python.exe" tools\build_library.py --module F-5E-3
pause
