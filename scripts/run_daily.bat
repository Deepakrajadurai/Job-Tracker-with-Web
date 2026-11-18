@echo off
REM Wrapper for Windows Task Scheduler. Set the API key and IMAP variables as user
REM environment variables (see README) so this script can stay free of secrets.
cd /d "%~dp0.."
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
REM Runs each file in profiles\. Falls back to config.yaml when there are no profiles.
python -m jobtracker.run --all-profiles
