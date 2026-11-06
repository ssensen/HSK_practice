@echo off
cd /d "%~dp0"
echo Getting the latest version from GitHub...
git pull --no-edit
if errorlevel 1 (
    rem A merge conflict leaves markers inside progress.json, so put the files back as they were.
    git merge --abort 2>nul
    echo.
    echo Could not pull from GitHub, so HSK Practice will start with what is on this PC.
    pause
)
start "" pythonw "%~dp0hsk_practice.py"
