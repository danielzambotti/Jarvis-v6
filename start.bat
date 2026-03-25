@echo off
title Jarvis v5.0
color 0A
echo.
echo  ================================================
echo   JARVIS v5.0 - Autonomous AI Engineering Agent
echo   Ollama + Telegram + Notion + GitHub
echo  ================================================
echo.

cd /d C:\Jarvis

REM Check if Ollama is running
curl -s --max-time 2 http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [AVISO] Ollama nao detectado. Iniciando...
    start /min "" ollama serve
    echo [INFO] Aguardando Ollama (5s)...
    timeout /t 5 /nobreak >nul
) else (
    echo [OK] Ollama online
)

REM Choose Python - prefer venv
if exist "C:\Jarvis\venv\Scripts\python.exe" (
    set PYTHON=C:\Jarvis\venv\Scripts\python.exe
    echo [OK] Venv: %PYTHON%
) else (
    set PYTHON=python
    echo [OK] Python do sistema
)

echo.
echo [START] Iniciando Jarvis v5.0...
echo [INFO]  Ctrl+C para parar
echo.

%PYTHON% main.py

echo.
echo [STOP] Jarvis encerrado.
pause >nul
