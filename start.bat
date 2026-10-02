@echo off
rem ============================================================
rem  HyPRA one-click launcher.
rem  Thin wrapper: all logic lives in start.ps1 (Chinese help there).
rem  Default target = backend + desktop console.
rem  Usage: start.bat [desktop|web|all|backend|frontend|console|docker|check] [-Release]
rem         start.bat help
rem  Also starts _local\qdrant\qdrant.exe when .env uses a local Qdrant.
rem  Bypass affects only this process; it does not change system policy.
rem  NOTE: keep this file ASCII-only -- cmd parses .bat as ANSI before
rem        chcp takes effect, so non-ASCII comments become garbage commands.
rem ============================================================
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
