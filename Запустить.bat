@echo off
chcp 65001 > nul
rem Запуск реестра обращений двойным кликом.
rem Обходит политику выполнения PowerShell, не меняя её в системе.
cd /d "%~dp0"

set SEED=
if not exist "var\dpo.db" set SEED=-Seed

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %SEED% %*

echo.
echo Сервер остановлен. Окно можно закрыть.
pause
