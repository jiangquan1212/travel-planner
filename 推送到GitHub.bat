@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Push Travel-Planner to GitHub
echo ============================================

REM --- 解决 git dubious ownership ---
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "DIRF=%DIR:\=/%"
git config --global --add safe.directory "%DIRF%" 2>nul
git config --global --add safe.directory "%DIR%" 2>nul

REM --- 已设置 remote 则复用；否则手动输入仓库地址 ---
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  set /p URL=Enter GitHub repo URL (e.g. https://github.com/yourname/travel-planner.git):
  if "%URL%"=="" (
    echo No URL entered. Please run again with a URL.
    pause
    exit /b 1
  )
  git remote add origin "%URL%"
)

REM --- 提交信息：可用参数 %1 自定义，否则用默认 ---
set "MSG=%~1"
if "%MSG%"=="" set "MSG=update: Vue3 frontend + feedback feature + bugfixes"

echo.
echo [1/3] git add -A
git add -A

echo [2/3] git commit
git diff --cached --quiet
if not errorlevel 1 (
  echo No changes to commit, push directly.
) else (
  git commit -m "%MSG%"
)

echo [3/3] git push
git push -u origin main

echo.
echo Done. 若提示输入密码，请使用 Personal Access Token（不是登录密码）。
pause
