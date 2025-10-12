@echo off

REM Define nomes dos scripts principais e o nome final da aplicação
set "MAIN_SCRIPT=client/main.py"
set "UPDATER_SCRIPT=updater.py"
set "MAIN_APP_NAME=SkyMetricsMonitor"

echo --- SkyMetrics PyInstaller Build Script ---

REM 1. LIMPEZA: Remove arquivos e pastas de builds anteriores
echo.
echo [1/6] Limpando builds anteriores...
rd /s /q build 2>nul
rd /s /q dist 2>nul
del "%MAIN_APP_NAME%.spec" 2>nul
del "updater.spec" 2>nul
del "%MAIN_APP_NAME%_Update_Package.zip" 2>nul
echo Limpeza concluida.

REM 2. BUILD DO SKYMETRICSMONITOR.EXE (APLICACAO PRINCIPAL)
echo.
echo [2/6] Criando %MAIN_APP_NAME%.exe e pasta de assets...
REM MUDANÇA CRÍTICA: Adicionando --paths "client" para forçar PyInstaller a procurar imports lá.
"client\.venv\Scripts\pyinstaller.exe" --noconfirm ^
--paths "client" ^
--clean ^
--windowed ^
--name "%MAIN_APP_NAME%" ^
--icon "client/assets/icons/skymetrics.ico" ^
--add-data "client/assets;assets" ^
--hidden-import "keyring" ^
--hidden-import "pystray" ^
--collect-all "ttkbootstrap" ^
--collect-all "pygame" ^
--collect-all "scipy" ^
--collect-all "numpy" ^
--collect-all "socketio" ^
--hidden-import "sim_data" ^
--hidden-import "auth_utils" ^
--hidden-import "event_logic" ^
--hidden-import "ws_monitor" ^
--hidden-import "gui" ^
--hidden-import "radio_dsp" ^
--hidden-import "radio_ui_logic" ^
--hidden-import "update_logic" ^
--add-binary "C:\Users\ander\Documents\KAFLY\sky\teste\client\.venv\Lib\site-packages\SimConnect\SimConnect.dll;SimConnect" ^
"%MAIN_SCRIPT%"
IF ERRORLEVEL 1 GOTO :PYINSTALLER_FAIL

REM 3. BUILD DO UPDATER.EXE (UTILITARIO)
echo.
echo [3/6] Criando updater.exe na pasta de destino final...
REM MUDANÇA: Chamando pyinstaller.exe a partir de client\.venv\Scripts\
"client\.venv\Scripts\pyinstaller.exe" --noconfirm ^
--onefile ^
--windowed ^
--distpath "dist\%MAIN_APP_NAME%" ^
--name "updater" ^
"%UPDATER_SCRIPT%"
IF ERRORLEVEL 1 GOTO :PYINSTALLER_FAIL

REM ==========================================================
REM 4. PREPARACAO E COMPACTACAO DO PACOTE DE ATUALIZACAO
REM ==========================================================
echo.
echo [4/6] Preparando e compactando o pacote de atualizacao...

set "DIST_BASE=dist\%MAIN_APP_NAME%"
set "TEMP_PACKAGE_DIR=%DIST_BASE%\PackageTemp"
set "ZIP_FILENAME=%MAIN_APP_NAME%_Update_Package.zip"
set "FINAL_ZIP_PATH=%DIST_BASE%\%ZIP_FILENAME%"

REM A. Cria a pasta temporaria DENTRO da pasta de destino final
rd /s /q "%TEMP_PACKAGE_DIR%" 2>nul
mkdir "%TEMP_PACKAGE_DIR%"

REM B. COPIA os arquivos essenciais para o pacote (EXE principal e pasta de dependencias)
echo Copiando arquivos para o pacote de atualizacao...
copy /Y "%DIST_BASE%\%MAIN_APP_NAME%.exe" "%TEMP_PACKAGE_DIR%\" > nul
REM Verifica se _internal existe antes de copiar (só ocorre se a build PyInstaller foi bem-sucedida)
xcopy /E /I /Y "%DIST_BASE%\_internal" "%TEMP_PACKAGE_DIR%\_internal\" > nul
IF ERRORLEVEL 4 GOTO :COPY_FAIL

REM C. Compacta a pasta temporaria para o arquivo ZIP
echo.
echo [5/6] Criando o arquivo ZIP de atualizacao...
powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%TEMP_PACKAGE_DIR%', '%FINAL_ZIP_PATH%', [System.IO.Compression.CompressionLevel]::Optimal, $false)"

IF ERRORLEVEL 1 (
    ECHO ERRO CRITICO: Falha ao criar o arquivo ZIP.
    GOTO :END_PROCESS_ERROR
)

REM D. Limpa a pasta temporaria
RD /s /q "%TEMP_PACKAGE_DIR%" 2>nul

echo SUCESSO!
echo Pacote de atualizacao '%ZIP_FILENAME%' criado em '%DIST_BASE%'.

GOTO :END_PROCESS_SUCCESS


:PYINSTALLER_FAIL
echo.
echo ERRO CRITICO: A execucao do PyInstaller falhou! Verifique a mensagem acima para detalhes (caminhos, venv, etc.).
GOTO :END_PROCESS_ERROR

:COPY_FAIL
echo.
echo ERRO CRITICO: Falha ao copiar arquivos de dependencia (e.g. "_internal"). O build do PyInstaller provavelmente falhou no Passo [2/6].
GOTO :END_PROCESS_ERROR

:END_PROCESS_SUCCESS
echo.
echo [6/6] Finalizando...

echo --- PROCESSO CONCLUIDO COM SUCESSO ---
echo O executavel, o updater e o pacote de atualizacao estao em: %DIST_BASE%
pause
GOTO :EOF

:END_PROCESS_ERROR
echo.
echo [6/6] Finalizando...
echo --- PROCESSO ENCERRADO COM ERROS ---
pause