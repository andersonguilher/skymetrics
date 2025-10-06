# Arquivo: updater.py - Executado PELO SkyMetricsMonitor.exe quando uma atualização é detectada.
#
# Tarefa: O script principal (main.py) lança este programa com a versão de destino como argumento.
# A função deste programa é gerar um script .bat e se encerrar imediatamente.
# O script .bat, por sua vez, forçará o encerramento do SkyMetricsMonitor.exe,
# realizará o download e a substituição do arquivo, e, por fim, reiniciará o aplicativo.

import os
import sys
import time
from subprocess import Popen
from tkinter import messagebox, Tk

# --- CONSTANTES DE CONFIGURAÇÃO ---
# ATUALIZADO: URL agora aponta para o pacote ZIP de atualização
UPDATE_DOWNLOAD_URL = "https://kafly.com.br/dash/skymetrics/SkyMetricsMonitor_Update_Package.zip"
MAIN_EXE_NAME = "SkyMetricsMonitor.exe"
# ATUALIZADO: Nome do arquivo a ser baixado
UPDATE_PACKAGE_NAME = "SkyMetricsMonitor_Update_Package.zip"
NEW_EXE_TEMP = "SkyMetricsMonitor_novo.exe" # Mantido, mas nao usado na nova logica
OLD_EXE_BACKUP = "SkyMetricsMonitor_old.exe" # Mantido, mas nao usado na nova logica
DOWNLOAD_SCRIPT = "update_finalizer_script.bat" 
# ----------------------------------

def create_finalizer_script(latest_version):
    """
    Cria e retorna o caminho para o script BAT que realiza o download, o encerramento forçado e a substituição.
    """
    
    # O diretório base deve ser o mesmo onde o updater.exe está rodando
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
    os.chdir(BASE_DIR)
    
    # Nova variável para o diretório de extração temporário
    EXTRACT_TEMP_DIR = "ExtractedTemp"
    
    # Note que a versão mais recente é passada como argumento para este script
    
    # As flags /F (forçar), /IM (nome da imagem) e 2>nul (silenciar erros se não rodando)
    # garantem o encerramento e evitam caixas de diálogo.
    bat_content = f"""
@echo off
ECHO SkyMetrics Updater - Versao {latest_version}

REM Define o caminho de trabalho
CD /D "{BASE_DIR}"

REM 1. FORÇA O ENCERRAMENTO DO PROGRAMA PRINCIPAL
ECHO Encerrando {MAIN_EXE_NAME} para liberar o arquivo...
TASKKILL /F /IM {MAIN_EXE_NAME} 2>nul
ping 127.0.0.1 -n 3 > nul

REM 2. Baixa o novo pacote ZIP de atualizacao
ECHO Iniciando download do pacote de atualizacao V{latest_version}...
curl -L -o "{UPDATE_PACKAGE_NAME}" "{UPDATE_DOWNLOAD_URL}"
IF ERRORLEVEL 1 (
    ECHO ERRO CRITICO: Falha ao baixar o arquivo. Verifique a conexao e tente novamente.
    PAUSE
    EXIT /B 1
)

ECHO Substituindo arquivos...

REM 3. APAGA OS ARQUIVOS ANTIGOS ANTES DA SUBSTITUICAO
ECHO Apagando executavel e dependencias antigos...
DEL "{MAIN_EXE_NAME}" 2>nul
RD /S /Q "_internal" 2>nul
ping 127.0.0.1 -n 2 > nul

REM 4. DESCOMPACTA O CONTEUDO DO ZIP PARA UMA PASTA TEMPORARIA
ECHO Descompactando o novo pacote...
REM Cria o diretorio temporario e garante que esteja limpo
RD /S /Q "{EXTRACT_TEMP_DIR}" 2>nul
powershell -Command "Expand-Archive -Path '{UPDATE_PACKAGE_NAME}' -DestinationPath '{EXTRACT_TEMP_DIR}' -Force"
IF ERRORLEVEL 1 (
    ECHO ERRO CRITICO: Falha ao descompactar o novo executavel.
    PAUSE
    EXIT /B 1
)

REM 5. MOVE O CONTEUDO EXTRAIDO (Lógica robusta que lida com pastas raiz do ZIP)
ECHO Movendo o novo executavel e dependencias para o diretorio principal...
REM O XCOPY move recursivamente, lidando com o caso onde o ZIP extrai uma pasta raiz.
XCOPY /E /I /Y "{EXTRACT_TEMP_DIR}\*" . > nul

REM 6. Limpa e Inicia a Aplicacao
ECHO Atualizacao concluida. Reiniciando o aplicativo...

REM 6a. INICIA A APLICACAO ATUALIZADA
START /MIN "" "{MAIN_EXE_NAME}"

REM 6b. Limpa o ZIP, a pasta temporaria e o script
DEL "{UPDATE_PACKAGE_NAME}" 2>nul
RD /S /Q "{EXTRACT_TEMP_DIR}" 2>nul
DEL "%~f0" 2>nul
EXIT
"""
    bat_path = os.path.join(BASE_DIR, DOWNLOAD_SCRIPT)
    with open(bat_path, "w") as f:
        f.write(bat_content.strip())
    
    return bat_path

if __name__ == "__main__":
    
    # Adicionamos o tkinter root aqui, para exibir o messagebox caso o updater seja
    # executado fora do fluxo normal e tente dar um erro.
    root = None
    if not hasattr(sys, 'frozen'):
        try:
            root = Tk()
            root.withdraw()
        except:
            pass

    # 1. Se não houver argumentos (ou for iniciado manualmente sem eles), apenas lança o monitor principal
    if len(sys.argv) < 2:
        try:
            Popen([MAIN_EXE_NAME], creationflags=0x08)
        except:
            pass
        sys.exit()

    # 2. Se houver argumentos, é o main.py que está pedindo a atualização
    latest_version = sys.argv[1] # A versão a ser baixada
    
    # Tenta lançar o script BAT de forma silenciosa
    CREATE_NO_WINDOW = 0x08000000

    try:
        # Cria o script BAT
        bat_path = create_finalizer_script(latest_version)
        
        # Inicia o BAT com CREATE_NO_WINDOW para evitar a janela preta
        Popen([bat_path], creationflags=CREATE_NO_WINDOW)
        
    except Exception as e:
        # Em caso de falha, tenta reiniciar o aplicativo principal e avisa o usuário (se o tkinter estiver disponível)
        try:
             Popen([MAIN_EXE_NAME])
             if root:
                 messagebox.showerror("Erro Crítico no Updater", f"O processo de atualização falhou antes de ser iniciado: {e}. O Monitor será reiniciado.")
        except:
             pass
        print(f"Updater ERRO FATAL: {e}") 

    # O updater.exe se encerra IMEDIATAMENTE após lançar o BAT.
    sys.exit()