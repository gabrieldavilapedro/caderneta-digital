@echo off
rem Caderneta Digital - inicia tudo com um clique: banco (Docker), sistema e navegador.
rem Na primeira vez tambem prepara o ambiente (venv, dependencias, .env e tabelas do banco).
title Caderneta Digital
cd /d "%~dp0"
if "%PORTA%"=="" set PORTA=8000
set ENDERECO=http://localhost:%PORTA%
echo == Caderneta Digital ==

netstat -ano | findstr ":%PORTA% " | findstr LISTENING >nul 2>nul
if not errorlevel 1 (
  echo O sistema ja esta em execucao em %ENDERECO%.
  start "" "%ENDERECO%"
  exit /b 0
)

where docker >nul 2>nul
if errorlevel 1 goto sem_docker
docker info >nul 2>nul
if errorlevel 1 goto docker_parado

if exist ".venv\Scripts\python.exe" goto configurar
echo Preparando o ambiente Python - somente na primeira vez, precisa de internet...
py -3 -m venv .venv 2>nul || python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto sem_python
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto sem_dependencias

:configurar
".venv\Scripts\python.exe" scripts\preparar_ambiente.py

echo Iniciando o banco de dados...
docker compose up -d db >nul
if errorlevel 1 goto sem_banco

rem Espera o banco responder (aplica as tabelas que estiverem faltando).
set TENTATIVAS=0
:esperar_banco
".venv\Scripts\python.exe" manage.py migrate --noinput >nul 2>nul
if not errorlevel 1 goto pronto
set /a TENTATIVAS+=1
if %TENTATIVAS% GEQ 30 goto sem_banco
timeout /t 2 /nobreak >nul
goto esperar_banco

:pronto
echo Abrindo %ENDERECO% ...
start "" cmd /c "timeout /t 3 /nobreak >nul & start "" %ENDERECO%"
echo Para encerrar o sistema, feche esta janela.
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:%PORTA%
exit /b 0

:sem_docker
echo ERRO: Docker nao encontrado. Instale o Docker Desktop e tente de novo.
goto falha
:docker_parado
echo ERRO: O Docker nao esta em execucao. Abra o Docker Desktop e tente de novo.
goto falha
:sem_python
echo ERRO: Nao foi possivel criar o ambiente Python. Instale o Python 3.12 ou superior.
goto falha
:sem_dependencias
echo ERRO: Nao foi possivel instalar as dependencias.
goto falha
:sem_banco
echo ERRO: O banco de dados nao respondeu. Verifique o Docker Desktop.
goto falha
:falha
pause
exit /b 1
