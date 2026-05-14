@echo off
echo ====================================
echo  XHS Scraper - Install Dependencies
echo ====================================
echo.
echo [1/3] Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo Install failed. Check pip.
    pause
    exit /b 1
)
echo.
echo [2/3] Installing Playwright browser driver...
python -m playwright install chromium
echo.
echo [3/3] Installing Node.js sign deps...
cd /d %~dp0\static
if not exist node_modules (
    npm install crypto-js
)
cd /d %~dp0
echo.
echo Done. Run run.bat to start.
pause
