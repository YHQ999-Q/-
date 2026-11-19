@echo off
chcp 65001 >nul
set MSYSTEM=
set PATH=C:\Espressif\python_env\idf5.2_py3.11_env\Scripts;%PATH%
call C:\Espressif\frameworks\esp-idf-v5.2.8\export.bat
cd /d "%~dp0"
idf.py build
pause
