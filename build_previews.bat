@echo off
echo ===============================
echo Building footage previews...
echo ===============================

if "%1"=="" (
    echo Please provide library path
    echo Example:
    echo build_previews.bat Z:\FootageLibrary
    pause
    exit /b 1
)

python "%~dp0build_previews.py" %1

echo.
echo Preview build finished.
pause
