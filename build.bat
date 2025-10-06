@echo off

REM Define nomes dos scripts principais e o nome final da aplicação
set "MAIN_SCRIPT=client/client.py"
set "MAIN_APP_NAME=SkyMetricsMonitor"

echo --- SkyMetrics PyInstaller Build Script ---

REM 1. LIMPEZA: Remove arquivos e pastas de builds anteriores
echo.
echo [1/5] Limpando builds anteriores...
rd /s /q build 2>nul
rd /s /q dist 2>nul
del "%MAIN_APP_NAME%.spec" 2>nul
del "%MAIN_APP_NAME%_Update_Package.zip" 2>nul
echo Limpeza concluida.

REM 2. BUILD DO SKYMETRICSMONITOR.EXE (APLICACAO PRINCIPAL)
echo.
echo [2/5] Criando %MAIN_APP_NAME%.exe e pasta de assets...
pyinstaller --noconfirm ^
--clean ^
--windowed ^
--name "%MAIN_APP_NAME%" ^
--icon "client/assets/icons/skymetrics.ico" ^
--add-data "client/assets;assets" ^
--hidden-import "keyring" ^
--hidden-import "pystray" ^
--collect-all "ttkbootstrap" ^
--add-binary "C:\Users\ander\Documents\KAFLY\sky\teste\.venv\Lib\site-packages\SimConnect\SimConnect.dll;SimConnect" ^
"%MAIN_SCRIPT%"


REM 3. BUILD DO UPDATER.EXE (UTILITARIO) - Comentado pois o arquivo nao foi fornecido
REM echo.
REM echo [3/5] Criando updater.exe na pasta de destino final...
REM pyinstaller --noconfirm ^
REM --onefile ^
REM --noconsole ^
REM --distpath "dist\%MAIN_APP_NAME%" ^
REM --name "updater" ^
REM "client/updater.py"

REM ==========================================================
REM 4. PREPARACAO E COMPACTACAO DO PACOTE DE ATUALIZACAO
REM ==========================================================
echo.
echo [4/5] Preparando e compactando o pacote de atualizacao...

set "DIST_BASE=dist\%MAIN_APP_NAME%"
set "TEMP_PACKAGE_DIR=%DIST_BASE%\PackageTemp"
set "ZIP_FILENAME=%MAIN_APP_NAME%_Update_Package.zip"
set "FINAL_ZIP_PATH=%DIST_BASE%\%ZIP_FILENAME%"

REM A. Cria a pasta temporaria DENTRO da pasta de destino final
rd /s /q "%TEMP_PACKAGE_DIR%" 2>nul
mkdir "%TEMP_PACKAGE_DIR%"

REM B. Move os arquivos essenciais para o pacote (EXE principal e pasta de dependencias)
move /Y "%DIST_BASE%\%MAIN_APP_NAME%.exe" "%TEMP_PACKAGE_DIR%"
move /Y "%DIST_BASE%\_internal" "%TEMP_PACKAGE_DIR%"

REM C. Compacta a pasta temporaria para o arquivo ZIP
powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%TEMP_PACKAGE_DIR%', '%FINAL_ZIP_PATH%', [System.IO.Compression.CompressionLevel]::Optimal, $false)"

IF ERRORLEVEL 1 (
    ECHO ERRO CRITICO: Falha ao criar o arquivo ZIP.
    GOTO :END_PROCESS
)

REM D. Limpa a pasta temporaria
RD /S /Q "%TEMP_PACKAGE_DIR%" 2>nul

echo SUCESSO! Pacote de atualizacao '%ZIP_FILENAME%' criado em '%DIST_BASE%'.

:END_PROCESS
echo.
echo [5/5] Finalizando...

echo --- PROCESSO CONCLUIDO ---
echo O pacote de atualizacao e o executavel estao em: %DIST_BASE%
pause