@echo off
echo =========================================
echo  Firm Website Finder - Build EXE
echo =========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher from https://python.org
    pause
    exit /b 1
)

echo [1/4] Installing dependencies...
pip install requests beautifulsoup4 openpyxl lxml pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/4] Cleaning previous builds...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q *.spec

echo.
echo [3/4] Building executable...
pyinstaller --name="FirmWebsiteFinder" ^
    --onefile ^
    --windowed ^
    --hidden-import tkinter ^
    --hidden-import tkinter.filedialog ^
    --hidden-import tkinter.messagebox ^
    --hidden-import tkinter.ttk ^
    --hidden-import openpyxl ^
    --hidden-import openpyxl.cell._writer ^
    --hidden-import requests ^
    --hidden-import bs4 ^
    --hidden-import lxml ^
    --hidden-import lxml.html ^
    --hidden-import lxml.etree ^
    --exclude-module pandas ^
    --exclude-module numpy ^
    --exclude-module matplotlib ^
    --exclude-module scipy ^
    --exclude-module PIL ^
    firm_finder_desktop.py

if errorlevel 1 (
    echo.
    echo ERROR: Build failed
    pause
    exit /b 1
)

echo.
echo [4/4] Verifying build...
if exist "dist\FirmWebsiteFinder.exe" (
    echo.
    echo =========================================
    echo  BUILD SUCCESSFUL!
    echo =========================================
    echo.
    echo Your executable is located at:
    echo   dist\FirmWebsiteFinder.exe
    echo.
    for %%I in ("dist\FirmWebsiteFinder.exe") do echo File size: %%~zI bytes
    echo.
    echo =========================================
    echo  HOW TO USE
    echo =========================================
    echo.
    echo 1. Double-click FirmWebsiteFinder.exe
    echo 2. Click "Browse for Excel File..."
    echo 3. Select your Excel file with a "Firm" column
    echo 4. Click "Find Official Websites"
    echo 5. Output file will be saved automatically
    echo.
) else (
    echo ERROR: Executable not found after build
    pause
    exit /b 1
)

pause
