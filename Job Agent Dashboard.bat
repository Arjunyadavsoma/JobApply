@echo off
cd /d "%~dp0"
title Job Agent Dashboard
echo Starting the Job Agent dashboard - it opens in your browser at http://localhost:8600
echo Keep this window open while you use it. Close it to stop the dashboard.
".venv\Scripts\python.exe" -m jobagent web
pause
