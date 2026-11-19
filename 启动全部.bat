@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   METAMIND 三版合一
echo   OpenTutor 8000  ^|  教学 Agent 5001  ^|  网页 5173
echo ============================================================
echo.

netstat -ano | findstr ":8000" | findstr LISTENING >nul
if errorlevel 1 (
  start "MetaMind OpenTutor 8000" /d "%~dp0opentutor" cmd /k "..\.venv-ot312\Scripts\python.exe -X utf8 scripts\native-server.py --port 8000"
) else (
  echo [跳过] OpenTutor 8000 已在运行
)

netstat -ano | findstr ":5001" | findstr LISTENING >nul
if errorlevel 1 (
  start "MetaMind 教学 Agent 5001" /d "%~dp0teaching" cmd /k ".venv\Scripts\python.exe -X utf8 server.py"
) else (
  echo [跳过] 教学 Agent 5001 已在运行
)

netstat -ano | findstr ":5173" | findstr LISTENING >nul
if errorlevel 1 (
  start "MetaMind 网页 5173" /d "%~dp0frontend" cmd /k "npm run dev -- --host 127.0.0.1 --port 5173"
) else (
  echo [跳过] 网页 5173 已在运行
)

echo.
echo 等待服务启动（约 8 秒）...
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:5173/#/"
exit /b
