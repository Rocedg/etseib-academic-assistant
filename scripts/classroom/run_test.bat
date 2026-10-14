@echo off
REM Run the prototype with Ollama on Windows CPU. Usage: double-click or "run_test.bat 2".
REM Ubuntu (run_test.sh) is recommended.
setlocal enabledelayedexpansion
set OPTION=%1
if "%OPTION%"=="" set OPTION=1
for %%I in ("%~dp0..\..") do set "KIT=%%~fI"
REM Candidates only; keep these names aligned with config.env.
set MODELS_4B=qwen3.5:4b gemma4:e4b qwen3:4b
set AGENT_MODELS=qwen3.5:9b gemma4:12b qwen3:8b
set EMBEDDING_MODEL=qwen3-embedding:0.6b
set OLLAMA_VERSION=0.34.4
set RES=%KIT%\results
mkdir "%RES%" 2>nul & mkdir "%KIT%\models" 2>nul
set OUTPUT=%RES%\%COMPUTERNAME%_opcion%OPTION%_windows.txt
set OLLAMA_MODELS=%KIT%\models
set OLLAMA_HOST=127.0.0.1:11434
set OLLAMA_CONTEXT_LENGTH=4096
set OLLAMA_KEEP_ALIVE=30m

echo === 1/6 Hardware === > "%OUTPUT%"
wmic cpu get name,numberofcores >> "%OUTPUT%" 2>&1
wmic os get FreePhysicalMemory,TotalVisibleMemorySize >> "%OUTPUT%" 2>&1
set PY=python
python --version >nul 2>&1 || set PY=py -3
%PY% --version >> "%OUTPUT%" 2>&1 || (echo ERROR: Python 3.10+ is required. Use Ubuntu. & echo Python unavailable >> "%OUTPUT%" & pause & exit /b 1)

echo === 2/6 Ollama ===
if not exist "%KIT%\ollama\ollama.exe" (
  mkdir "%KIT%\ollama" 2>nul
  curl -fL -o "%KIT%\ollama\o.zip" https://github.com/ollama/ollama/releases/download/v%OLLAMA_VERSION%/ollama-windows-amd64.zip ^
   || curl -fL -o "%KIT%\ollama\o.zip" https://ollama.com/download/ollama-windows-amd64.zip
  tar -xf "%KIT%\ollama\o.zip" -C "%KIT%\ollama" && del "%KIT%\ollama\o.zip"
)
set OLLAMA="%KIT%\ollama\ollama.exe"
%OLLAMA% --version >> "%OUTPUT%" 2>&1 || (echo ERROR: Ollama no se puede ejecutar ^(bloqueado o no descargado^). & pause & exit /b 1)

echo === 3/6 Servidor ===
curl -s localhost:11434/api/version >nul 2>&1 || start "ollama" /min %OLLAMA% serve
for /l %%i in (1,1,30) do (curl -s localhost:11434/api/version >nul 2>&1 && goto :servidor & timeout /t 1 >nul)
:servidor

echo === 4/6 Models ===
set M4B=
for %%m in (%MODELS_4B%) do if not defined M4B (%OLLAMA% pull %%m && set M4B=%%m)
if not defined M4B (echo ERROR: no 4B model is available; edit config.env & pause & exit /b 1)
echo 4B model: %M4B% >> "%OUTPUT%"
set LLM_BACKEND=ollama
set LLM_TIMEOUT=900
if "%OPTION%"=="2" (
  set MAG=
  for %%m in (%AGENT_MODELS%) do if not defined MAG (%OLLAMA% pull %%m && set MAG=%%m)
  %OLLAMA% pull %EMBEDDING_MODEL%
  set LLM_MODEL=!MAG!& set LLM_SMALL_MODEL=%M4B%& set EMBEDDING_MODEL=%EMBEDDING_MODEL%& set RAG_K=5
  echo Agente: !MAG! >> "%OUTPUT%"
) else (
  set LLM_MODEL=%M4B%& set EMBEDDING_MODEL=none& set RAG_K=3
)

echo === 5/6 Question bank (several minutes per question) ===
cd /d "%KIT%"
echo Working... Do not close this window. This may take 10-30 minutes.
%PY% evaluate.py --today 2026-10-01 >> "%OUTPUT%" 2>&1
type "%OUTPUT%"
echo === 6/6 Memory === >> "%OUTPUT%"
%OLLAMA% ps >> "%OUTPUT%" 2>&1
echo.
echo Done. Upload this file to the course thread: %OUTPUT%
pause
