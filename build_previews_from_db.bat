@echo off
echo ===============================
echo Building previews from database
echo ===============================

python "%~dp0build_previews_from_db.py"

echo.
echo Preview build from DB finished.
pause

