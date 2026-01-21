@echo off
cd /d %~dp0

echo =======================================================
echo      Device Inspection Tool Packer (Win7 Compatible)
echo =======================================================
echo.

echo 1. Cleaning build folders...
if exist build rd /s /q build
if exist dist rd /s /q dist
if exist *.spec del /q *.spec

echo.
echo 2. Running PyInstaller...
echo Note: Using PyInstaller 4.10 compatible mode
echo.

:: Flattened command to avoid line continuation issues and encoding problems
pyinstaller --clean -F main.py --name=DeviceInspectionTool --add-data "inspection_tool.py;." --add-data "port_status_inspection.py;." --hidden-import=pandas --hidden-import=openpyxl --hidden-import=netmiko --hidden-import=paramiko --hidden-import=cryptography --hidden-import=bcrypt --hidden-import=msoffcrypto --hidden-import=msoffcrypto.tool --hidden-import=idna --hidden-import=encodings.idna --hidden-import=cffi --hidden-import=six --exclude-module=matplotlib --exclude-module=tkinter --exclude-module=scipy --exclude-module=IPython

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Packaging failed!
    echo Please check requirements: pip install -r requirements.txt
    goto :error
)

echo.
echo =======================================================
echo                 Success!
echo =======================================================
echo Output: %~dp0dist\DeviceInspectionTool.exe
echo.
pause
exit /b 0

:error
echo.
echo To retry manually, copy this command:
echo pyinstaller --clean -F main.py --name=DeviceInspectionTool --add-data "inspection_tool.py;." --add-data "port_status_inspection.py;." --hidden-import=pandas --hidden-import=openpyxl --hidden-import=netmiko --hidden-import=paramiko --hidden-import=cryptography --hidden-import=bcrypt --hidden-import=msoffcrypto --hidden-import=msoffcrypto.tool --hidden-import=idna --hidden-import=encodings.idna --hidden-import=cffi --hidden-import=six
echo.
pause
exit /b 1
