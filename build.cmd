@echo off
cd /d "%~dp0"
if exist ".bootstrap\bin\uv.exe" (
    ".bootstrap\bin\uv.exe" run --offline --cache-dir .uv-cache python -m PyInstaller --noconfirm --clean RestaurantBilling.spec
) else (
    uv run --offline python -m PyInstaller --noconfirm --clean RestaurantBilling.spec
)
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo Built dist\RestaurantBilling.exe
