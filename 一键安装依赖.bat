@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================================
echo   MetaMind 依赖安装
echo   需要：Python 3.12、uv、Node.js 18+
echo ============================================================
echo.
where uv >nul 2>nul
if errorlevel 1 (
  echo [缺少] uv。先安装：https://docs.astral.sh/uv/
  pause & exit /b 1
)

echo [1/4] 创建 OpenTutor 的 Python 3.12 环境...
if not exist ".venv-ot312\Scripts\python.exe" (
  uv venv .venv-ot312 --python 3.12
  uv pip install --python .venv-ot312\Scripts\python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r opentutor\apps\api\requirements-core.txt
) else (
  echo [跳过] .venv-ot312 已存在
)

echo [2/4] 创建教学 Agent 的 Python 环境...
if not exist "teaching\.venv\Scripts\python.exe" (
  uv venv teaching\.venv --python 3.13
  uv pip install --python teaching\.venv\Scripts\python.exe --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r teaching\requirements.txt
) else (
  echo [跳过] teaching\.venv 已存在
)

echo [3/4] 安装网页依赖...
if not exist "frontend\node_modules" (
  pushd frontend
  call npm install --no-audit --no-fund
  call npm install @rollup/rollup-win32-x64-msvc @esbuild/win32-x64 --no-save
  popd
) else (
  echo [跳过] frontend\node_modules 已存在
)

echo [4/4] 完成。双击「启动全部.bat」。
pause
