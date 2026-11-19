@echo off
setlocal
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Please install Node.js 22 or 24, then reopen this file.
  pause
  exit /b 1
)
if not exist node_modules (
  echo Installing dependencies. Internet access is required.
  call npm.cmd ci
  if errorlevel 1 goto failed
)
echo Opening MetaMind development server. Keep this window open.
echo Open the Local URL shown below in your browser. Press Ctrl+C to stop.
call npm.cmd run dev -- --host 127.0.0.1
exit /b
:failed
echo Installation failed. Check your network and Node.js installation.
pause
exit /b 1
