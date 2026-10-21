@echo off
cd /d "%~dp0"
set "BUILD_OUTPUT=dist"
if exist "dist\KhataUpdate\RestaurantBilling.exe" set "BUILD_OUTPUT=dist\KhataUpdate"
if exist ".bootstrap\bin\uv.exe" (
    ".bootstrap\bin\uv.exe" run --offline --cache-dir .uv-cache python -m PyInstaller --noconfirm --clean --distpath "%BUILD_OUTPUT%" RestaurantBilling.spec
) else (
    uv run --offline python -m PyInstaller --noconfirm --clean --distpath "%BUILD_OUTPUT%" RestaurantBilling.spec
)
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo Built %BUILD_OUTPUT%\RestaurantBilling.exe
