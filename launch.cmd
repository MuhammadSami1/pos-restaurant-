@echo off
cd /d "%~dp0"
if exist "dist\KhataUpdate\RestaurantBilling.exe" (
    start "" "dist\KhataUpdate\RestaurantBilling.exe"
    exit /b
)
if exist "dist\RestaurantBilling.exe" (
    start "" "dist\RestaurantBilling.exe"
    exit /b
)
if exist ".bootstrap\bin\uv.exe" (
    ".bootstrap\bin\uv.exe" run --offline --cache-dir .uv-cache python main.py
) else (
    uv run --offline python main.py
)
if errorlevel 1 pause
