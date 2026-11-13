@echo off
cd /d "%~dp0"
echo Getting the latest version from GitHub...
rem Merges progress.json from both PCs, and puts the files back as they were if it can't pull.
python sync.py pull
if errorlevel 1 (
    echo.
    echo Could not pull from GitHub, so HSK Practice will start with what is on this PC.
    rem Not pause: at Windows startup nobody may be there to press a key.
    timeout /t 15
)
start "" pythonw "%~dp0hsk_practice.py"
