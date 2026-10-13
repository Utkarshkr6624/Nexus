@echo off
rem Start NEXUS + Jarvis for local development. See scripts\launch_nexus.py for what it checks and starts.
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0scripts\launch_nexus.py" %*
