@echo off
chcp 65001 >nul
set MSYSTEM=
set PATH=C:\Espressif\python_env\idf5.2_py3.11_env\Scripts;%PATH%
call C:\Espressif\frameworks\esp-idf-v5.2.8\export.bat
cd /d "%~dp0"

set /p PORT=请输入串口号（例如 COM5，直接回车默认 COM5）:
if "%PORT%"=="" set PORT=COM5

echo.
echo 正在编译并烧录到 %PORT% ...
idf.py -p %PORT% flash monitor
pause
