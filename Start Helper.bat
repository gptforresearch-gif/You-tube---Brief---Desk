@echo off
title YouTube Brief Desk - Helper
cd /d "%~dp0"
echo Pehli baar chala rahe hain to zaroori cheezein lag rahi hain...
pip install -q requests youtube-transcript-api
echo.
python brief_desk_helper.py
pause
