@echo off
cd /d "C:\Users\steve\AppData\Local\Programs\Python\Python311\Claude"
echo Pulling latest call log from GitHub...
git pull
echo.
echo Starting Keap Call Manager...
echo.
python keap_call_manager.py
pause
