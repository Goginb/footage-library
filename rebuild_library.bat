@echo off
setlocal ENABLEDELAYEDEXPANSION

cd /d "%~dp0"

echo ===============================
echo Rebuild Footage Library
echo ===============================

echo.
echo Enter one or more footage roots.
echo Example: Z:\_Library\ActionVFX  Z:\_Library\Textures
set /p LIB_ROOTS="Roots: "

if "%LIB_ROOTS%"=="" (
    echo No paths provided. Aborting.
    pause
    endlocal
    exit /b 1
)

echo.
echo 1/2 Indexing footage...
python -m indexer.scan %LIB_ROOTS%
if errorlevel 1 (
    echo.
    echo Indexer failed. Check errors above.
    pause
    endlocal
    exit /b 1
)

echo.
echo 2/2 Building previews from database...
python "%~dp0build_previews_from_db.py"
if errorlevel 1 (
    echo.
    echo Preview build failed. Check errors above.
    pause
    endlocal
    exit /b 1
)

echo.
echo ===============================
echo Library rebuild COMPLETE
echo ===============================
echo DB:    database\footage.db
echo Previews live next to footage in preview\<asset_name>\
echo.
pause

endlocal
