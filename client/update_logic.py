# Arquivo: client/update_logic.py

import requests
import threading
from typing import Tuple, Optional
from tkinter import messagebox
from datetime import datetime

DECISION_PROCEED_TO_LOGIN = 1
DECISION_INITIATE_UPDATE = 2

def _compare_versions(current_v: str, latest_v: str) -> bool:
    """Compara duas strings de versão."""
    try:
        current_parts = [int(p) for p in current_v.split('.')]
        latest_parts = [int(p) for p in latest_v.split('.')]
        max_len = max(len(current_parts), len(latest_parts))
        current_parts += [0] * (max_len - len(current_parts))
        latest_parts += [0] * (max_len - len(latest_parts))
        for i in range(max_len):
            if latest_parts[i] > current_parts[i]: return True
            if latest_parts[i] < current_parts[i]: return False
        return False
    except Exception: return False

def initiate_update_and_exit_sync(app_instance, current_v: str, latest_v: str) -> int:
    """Exibe o diálogo modal de atualização."""
    decision_lock = threading.Lock()
    decision_lock.acquire()
    user_decision = [DECISION_PROCEED_TO_LOGIN] 
    
    def show_modal():
        message = (
            f"Uma nova versão ({latest_v}) do Cliente Monitor está disponível (versão atual: {current_v}).\n\n"
            "Deseja atualizar agora? (SIM para atualizar e encerrar; NÃO para continuar com a versão atual)"
        )
        user_accepted = messagebox.askyesno("Atualização Crítica Disponível", message)
        if user_accepted: user_decision[0] = DECISION_INITIATE_UPDATE
        decision_lock.release()

    app_instance.after(0, show_modal)
    decision_lock.acquire()
    decision_lock.release()
    return user_decision[0]

def check_for_update_sync(app_instance, current_v: str, url: str) -> Tuple[int, Optional[str]]:
    """Verifica a versão mais recente."""
    try:
        app_instance.after(0, lambda: app_instance.title(f"Monitor de Voo - Verificando Atualização..."))
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        latest_version = response.text.strip()
        
        if _compare_versions(current_v, latest_version):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [ALERTA] Nova versão {latest_version} disponível.")
            decision = initiate_update_and_exit_sync(app_instance, current_v, latest_version)
            return decision, latest_version
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] A versão atual ({current_v}) é a mais recente.")
            return DECISION_PROCEED_TO_LOGIN, None
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [AVISO] Falha ao verificar atualização: {e}")
        return DECISION_PROCEED_TO_LOGIN, None
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] inesperado ao verificar atualização: {e}")
        return DECISION_PROCEED_TO_LOGIN, None