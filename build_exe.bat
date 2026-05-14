@echo off
cd /d %~dp0
echo ====================================
echo  Build QiQiCollector EXE
echo ====================================
echo.
echo Installing/updating dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install runtime deps from requirements.txt
    pause
    exit /b 1
)
pip install pyinstaller pillow >nul 2>&1
python tools\\png_to_ico.py --src assets\\logo.png --dst assets\\logo.ico >nul 2>&1
echo Building, this takes 2-5 minutes...
echo.
pyinstaller --noconfirm --onefile --windowed ^
  --name QiQiCollector ^
  --icon assets/logo.ico ^
  --collect-all playwright ^
  --hidden-import openpyxl ^
  --hidden-import pymysql ^
  --hidden-import PIL ^
  --hidden-import PIL.Image ^
  --hidden-import PIL.ImageDraw ^
  --hidden-import PIL.ImageFont ^
  --add-data "license_server.txt;." ^
  --add-data "assets/logo.png;assets" ^
  --add-data "assets/logo.ico;assets" ^
  --add-data "static/xhs_main.js;static" ^
  --add-data "static/xhs_rap.js;static" ^
  --add-data "static/xhs_xray.js;static" ^
  --add-data "static/sign_server.js;static" ^
  --add-data "static/node.exe;static" ^
  --add-data "static/node_modules;static/node_modules" ^
  main.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Done: dist\QiQiCollector.exe
echo.
echo Notes:
echo  - Requires Microsoft Edge installed (default on Win10/11)
echo  - First launch shows activation dialog
echo  - Configure license_server.txt next to exe for your server URL
pause
