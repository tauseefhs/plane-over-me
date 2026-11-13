@echo off
REM Windows: double-click this file to start Plane Over Me.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First start: setting things up, about a minute ...
  py -3 -m venv .venv || python -m venv .venv || (echo Python 3.9 or newer is needed: https://www.python.org/downloads/ & pause & exit /b 1)
)
.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
.venv\Scripts\python.exe -m planeoverme start %*
pause
