@echo off
rem Chrome starts this file as the native messaging host of the companion extension, and reads what it writes as
rem messages: nothing but chrome-host.py may write to the output, so the echo stays off.
py -3 -I "%~dp0chrome-host.py" %*
