@echo off
rem Starts the gateway in the foreground to see errors (Ctrl+C to stop). Add --mock for a rehearsal without model.
cd /d "%~dp0.."
py -3 server\server.py %*
pause
