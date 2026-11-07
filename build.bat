@echo off
cd /d "%~dp0"
echo Building Black Box...
echo.

set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto nopython

%PY% --version >nul 2>nul
if errorlevel 1 goto nopython

if not exist blackbox.py goto nofile
if not exist requirements.txt goto nofile
if not exist version_info.txt goto nofile

%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

%PY% -m PyInstaller --noconfirm --onedir --windowed --noupx --name BlackBox --version-file version_info.txt blackbox.py
if errorlevel 1 goto fail

echo.
echo Done! Your app is here:
echo %~dp0dist\BlackBox\BlackBox.exe
explorer "%~dp0dist\BlackBox"
pause
exit /b 0

:nopython
echo Python was not found on this PC.
echo Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run this again.
pause
exit /b 1

:nofile
echo blackbox.py, requirements.txt and version_info.txt must be in the same folder as build.bat.
pause
exit /b 1

:fail
echo.
echo Something went wrong. Copy the messages above and include them if you report an issue.
pause
exit /b 1
