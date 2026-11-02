@echo off
rem claude-switch on Windows. install.ps1 puts this file in %USERPROFILE%\.local\bin, next to claude.exe, and the
rem script in %USERPROFILE%\.local\share\claude-switch.
py -3 -I "%~dp0..\share\claude-switch\claude-switch.py" %*
exit /b %ERRORLEVEL%
