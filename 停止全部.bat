@echo off
chcp 65001 >nul
setlocal
echo 正在停止 METAMIND 的 3 个服务...
for %%P in (8000 5001 5173) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr LISTENING ^| findstr ":%%P"') do (
    taskkill /PID %%A /F >nul 2>&1
  )
)
echo 已停止。若某个窗口还在，关闭该终端窗口即可。
timeout /t 2 /nobreak >nul
exit /b
