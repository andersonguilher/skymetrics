# login_client_kafly.py (FINAL: Cliente Completo com Configuração Externa e Auto-Update)

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from typing import Callable
import requests
import json
import configparser
import keyring 
import zlib 
import os 
import sys
import time
import random
from copy import deepcopy 

# --- Módulos de WebSocket e Threading ---
import websocket
import threading
from datetime import datetime

# NOVO: Módulos para atualização e alertas
from subprocess import Popen 
from threading import Lock 
from tkinter import messagebox 

# NOVO: Módulos para System Tray
try:
    from PIL import Image
    import pystray 
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False
    print("[AVISO] pystray/Pillow não encontrados. O recurso de ícone de bandeja será desativado.")


# =================================================================
# 1. CONSTANTES E CONFIGURAÇÃO
# =================================================================
CONFIG_FILE = 'client_config.ini'
CLIENT_CONFIG_SECTION = 'CLIENT_CONFIG' 
CLIENT_LOGIN_SECTION = 'LOGIN_CREDENTIALS'

# NOVO: VERSÃO ATUAL E LÓGICA DE ATUALIZAÇÃO
CURRENT_VERSION = "1.0.0" 
UPDATE_CHECK_URL = "https://kafly.com.br/skymetrics/update/current_version.txt"
UPDATE_EXECUTABLE_NAME = "updater.exe" 
UPDATE_CHECK_LOCK = Lock()

# --- Carregar Configurações do Arquivo ---
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

# Lógica de fallback para garantir que as seções existam
if CLIENT_CONFIG_SECTION not in config: config[CLIENT_CONFIG_SECTION] = {}
if CLIENT_LOGIN_SECTION not in config: config[CLIENT_LOGIN_SECTION] = {}

# CARREGAR VARIÁVEIS DO INI
KEYRING_SERVICE_ID = config.get(CLIENT_CONFIG_SECTION, 'keyring_service_id', fallback='KAFY_Pilot_Password')
VA_KEY = config.get(CLIENT_CONFIG_SECTION, 'va_key', fallback='KAFLY')
KAFY_BASE_URL = config.get(CLIENT_CONFIG_SECTION, 'kafy_base_url', fallback='https://kafly.com.br')
LOGIN_ENDPOINT = config.get(CLIENT_CONFIG_SECTION, 'login_endpoint', fallback='/dash/utils/login_check.php')
PILOTS_ENDPOINT = config.get(CLIENT_CONFIG_SECTION, 'pilots_endpoint', fallback='/dash/utils/get_validated_pilots.php')
WEBSOCKET_URL = config.get(CLIENT_CONFIG_SECTION, 'websocket_url', fallback='ws://www.kafly.com.br:8765')
HEARTBEAT_INTERVAL = config.getint(CLIENT_CONFIG_SECTION, 'heartbeat_interval', fallback=5)


# FUNÇÃO PARA GERENCIAMENTO DE CAMINHO DE RECURSOS (PyInstaller-compatible)
def _get_resource_path(relative_path):
    """ Obtém o caminho absoluto para um recurso, funcionando com PyInstaller. """
    try:
        if getattr(sys, 'frozen', False):
            # No modo PyInstaller, os assets estão em uma pasta chamada 'assets' 
            # criada pelo argumento --add-data "client/assets;assets" (do build.bat).
            # A base é sys._MEIPASS.
            return os.path.join(sys._MEIPASS, 'assets', relative_path)
        else:
            # No modo de desenvolvimento, a pasta de assets está em 'client/assets' 
            # ou 'assets' dependendo de onde o script está rodando.
            # O caminho relativo a partir de 'client.py' é '../assets'.
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', relative_path)
    except Exception:
        return relative_path

ICON_PATH = _get_resource_path('icons/skymetrics.ico')


# --- Variáveis Globais de Estado ---
CONN_STATUS = "REAL" 
sm = None 
aq = None 
last_sent_data = None 

# PRECISION MAP: Define a precisão funcional de cada métrica
DATA_PRECISION = { 
    "alt_ind": 0, "vs": 0, "ias": 1, "tas": 1, "agl": 0, "on_ground": 0, 
    "total_fuel": 0, "gear_left_pos": 0, "g_force": 1, 
    "engine_count": 0, 
    "lat": 3, "lng": 3, 
    "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0, 
    "engine_vibration_1": 0,
}

# =================================================================
# 2. FUNÇÕES DE LÓGICA DE ATUALIZAÇÃO
# (Conteúdo da Seção 2 inalterado)
# =================================================================

def _compare_versions(current_v, latest_v):
    """Compara duas strings de versão (ex: '1.0.0' vs '1.0.1').
    Retorna True se latest_v > current_v."""
    try:
        current_parts = [int(p) for p in current_v.split('.')]
        latest_parts = [int(p) for p in latest_v.split('.')]
        
        max_len = max(len(current_parts), len(latest_parts))
        current_parts += [0] * (max_len - len(current_parts))
        latest_parts += [0] * (max_len - len(latest_parts))
        
        for i in range(max_len):
            if latest_parts[i] > current_parts[i]:
                return True
            if latest_parts[i] < current_parts[i]:
                return False
        return False
        
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] ao comparar versões: {e}")
        return False

def initiate_update_and_exit(app_instance, latest_version):
    """Inicia o updater.exe e fecha o aplicativo principal."""
    
    if app_instance._update_in_progress:
        return

    message = (
        f"Uma nova versão ({latest_version}) do Cliente Monitor está disponível (versão atual: {CURRENT_VERSION}).\n\n"
        "⚠️ ATENÇÃO: Esta é uma atualização crítica. Não atualizar pode resultar em erros de conexão.\n\n"
        "O aplicativo será fechado imediatamente para iniciar o processo de atualização automática.\n\n"
        "Deseja atualizar agora?"
    )
    
    if not messagebox.askyesno("Atualização Crítica Disponível", message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [AVISO] Atualização de {latest_version} IGNORADA pelo usuário.")
        return

    app_instance._update_in_progress = True
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Iniciando processo de atualização...")
    
    app_instance.stop_monitor_and_simconnect()
    
    try:
        Popen([UPDATE_EXECUTABLE_NAME, latest_version])
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [SUCESSO] Executado {UPDATE_EXECUTABLE_NAME} com argumento {latest_version}. Encerrando o Monitor.")
    except FileNotFoundError:
         print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] O arquivo {UPDATE_EXECUTABLE_NAME} não foi encontrado. Atualização abortada.")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] ao executar o updater: {e}")
    
    app_instance.destroy()

def check_for_update(app_instance, silent=False):
    """Verifica a versão mais recente e inicia a atualização se necessário."""
    
    with UPDATE_CHECK_LOCK:
        try:
            if not app_instance.winfo_exists() or app_instance._update_in_progress:
                return False 
                
            if not silent:
                 print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Checando por novas versões em {UPDATE_CHECK_URL}...")
                 
            response = requests.get(UPDATE_CHECK_URL, timeout=5)
            response.raise_for_status()
            
            latest_version = response.text.strip()
            
            if _compare_versions(CURRENT_VERSION, latest_version):
                if not silent:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [ALERTA] Nova versão {latest_version} disponível (atual: {CURRENT_VERSION}). Iniciando diálogo...")
                
                app_instance.after(0, initiate_update_and_exit, app_instance, latest_version)
                
                return True
            else:
                if not silent:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] A versão atual ({CURRENT_VERSION}) é a mais recente.")
                return False
                
        except requests.exceptions.RequestException as e:
            if not silent:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [AVISO] Falha ao verificar atualização (Conexão/Timeout): {e}")
            return False
        except Exception as e:
            if not silent:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] inesperado ao verificar atualização: {e}")
            return False

# =================================================================
# 3. LÓGICA DE AUTENTICAÇÃO, ID e CREDENCIAIS
# (Conteúdo da Seção 3 inalterado)
# =================================================================

# REMOVIDO: A função generate_pilot_numeric_id não é mais usada para o ID do piloto.

def check_login(email: str, password: str) -> bool:
    url = KAFY_BASE_URL + LOGIN_ENDPOINT
    data = {'username': email, 'password': password}
    try:
        response = requests.post(url, data=data, timeout=10)
        return response.text.strip().lower() == 'true'
    except requests.exceptions.RequestException: return False

def get_validated_pilot_data(email: str) -> dict | None:
    url = KAFY_BASE_URL + PILOTS_ENDPOINT
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        pilots_list = response.json()
        for pilot in pilots_list:
            if pilot.get('_email_contato', '').lower() == email.lower(): return pilot
        return None
    except Exception: return None

def load_credentials() -> tuple[str, str, bool]:
    email, password, remember_me = "", "", False
    try:
        global config 
        config.read(CONFIG_FILE) 
        if CLIENT_LOGIN_SECTION in config:
            email = config.get(CLIENT_LOGIN_SECTION, 'pilot_email', fallback="")
            remember_me = config.getboolean(CLIENT_LOGIN_SECTION, 'remember_me', fallback=False)
        if email and remember_me:
            password = keyring.get_password(KEYRING_SERVICE_ID, email)
    except Exception: pass
    return email, password, remember_me

def save_credentials(email: str, password: str):
    try:
        global config 
        config.read(CONFIG_FILE)
        if CLIENT_LOGIN_SECTION not in config: config[CLIENT_LOGIN_SECTION] = {}
        config[CLIENT_LOGIN_SECTION]['remember_me'] = 'True'
        config[CLIENT_LOGIN_SECTION]['pilot_email'] = email
        with open(CONFIG_FILE, 'w') as configfile: config.write(configfile)
        keyring_username = email
        keyring.set_password(KEYRING_SERVICE_ID, keyring_username, password)
    except Exception as e: print(f"Erro ao salvar credenciais: {e}")

def delete_credentials(email: str, clear_email: bool = True):
    """
    Remove as credenciais e desativa o autologin.
    """
    try:
        keyring_username = email
        try: keyring.delete_password(KEYRING_SERVICE_ID, keyring_username)
        except Exception: pass 
        
        global config
        config.read(CONFIG_FILE)
        if CLIENT_LOGIN_SECTION in config: 
             config[CLIENT_LOGIN_SECTION]['remember_me'] = 'False'
             if clear_email:
                 config[CLIENT_LOGIN_SECTION]['pilot_email'] = '' 
        with open(CONFIG_FILE, 'w') as configfile: config.write(configfile)
    except Exception as e: print(f"Erro ao deletar credenciais: {e}")


# =================================================================
# 4. SIMCONNECT, MOCK E LÓGICA DE DADOS
# (Conteúdo da Seção 4 inalterado)
# =================================================================

# --- MOCKUP / SIMCONNECT SETUP ---
class MockSimConnect:
    def exit(self): pass

class MockAircraftRequests:
    def __init__(self, sm=None): self._start_time = time.time()
    def get(self, var):
        t = time.time() - self._start_time
        cycle_60s = t % 60
        if var == "VERTICAL_SPEED": return random.uniform(-0.001, 0.001) if cycle_60s < 10 else random.uniform(-5000, 5000)
        if var == "PLANE_LATITUDE": return -23.5505 + random.uniform(-0.00001, 0.00001)
        if var == "PLANE_LONGITUDE": return -46.6333 + random.uniform(-0.00001, 0.00001)
        if var == "PLANE_ALTITUDE": return 10000 + 5000 * random.uniform(-0.1, 0.1) if t > 10 else 0
        if var == "AIRSPEED_INDICATED": return 215 + 65 * random.uniform(-0.05, 0.05)
        if var == "AIRSPEED_TRUE": return self.get("AIRSPEED_INDICATED") * 1.1 + 10 
        if var == "ALTITUDE ABOVE GROUND": return 500 if cycle_60s < 5 or cycle_60s > 50 else 10000
        if var == "SIM_ON_GROUND": return 1 if self.get("ALTITUDE_ABOVE_GROUND") < 10 else 0
        if var == "GEAR_HANDLE_POSITION": return 1.0 if cycle_60s < 10 else 0.0
        if var == "NUMBER_OF_ENGINES": return 2
        if var == "PLANE_BANK_DEGREES": return 45 if 2 < cycle_60s < 4 else 5 
        if var == "G_FORCE": return 1.0 + 0.8 * abs(0.5 - (cycle_60s / 60))
        if var == "FUEL_TOTAL_QUANTITY": return 8500.5 + 500 * random.uniform(-0.01, -0.01)
        if var == "GENERAL_ENG_COMBUSTION:1": return 1 if t > 5 else 0
        if var == "LIGHT_BEACON_ON": return 1 if t > 5 else 0
        if var == "LIGHT_LANDING_ON": return 1 if t < 60 else 0 
        if var == "LIGHT_STROBE_ON": return 1 if self.get("SIM_ON_GROUND") == 0 else 0 
        if var == "OVERSPEED_WARNING": return 1 if 4 < cycle_60s < 6 else 0
        if var == "STALL_WARNING": return 1 if 10 < cycle_60s < 12 else 0 
        if var == "GENERAL_ENG_FIRE:1": return 1 if 10 < cycle_60s < 15 else 0 
        if var == "GENERAL_ENG_VIBRATION:1": return 1200 if 25 < cycle_60s < 28 else 500 
        if var == "STALL_PROTECTION_ACTIVE": return 0 
        if var == "GPWS_WARNING": return 0 
        if var == "FLAPS_SPEED_EXCEEDED": return 0 
        if var == "GEAR_WARNING_SYSTEM_ACTIVE": return 0 
        return 0

# --- VARIÁVEIS DE CONEXÃO (INICIALIZAÇÃO SEGURA) ---
sm = None ; aq = None 
try:
    from SimConnect import SimConnect, AircraftRequests
    sm = SimConnect(); aq = AircraftRequests(sm); CONN_STATUS = "REAL" 
except Exception as e:
    # MODIFICADO: Loga o erro específico de inicialização do SimConnect.
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [SIMCONNECT ERROR] Falha na inicialização do SimConnect. Usando modo SIMULADO. Erro: {e}")
    sm = MockSimConnect(); aq = MockAircraftRequests(sm); CONN_STATUS = "SIMULADO" 

# --- ESTRUTURA DE DADOS COMPLETA (Skymetrics) ---
flight_data = {
    "alt_ind": 0, "vs": 0.0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
    "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, 
    "engine_vibration_1": 0.0,
    "pilot_name": "N/A", "vatsim_id": "", "ivao_id": "", # MODIFICADO: 'pilot_id' substituído por 'pilot_name'
    "alerts": {"overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0,},
    "client_disconnect": 0, 
}

# CHAVE DA CORREÇÃO: Força o erro a ser levantado se SimConnect falhar
def get_safe_value(var_name, default=0):
    try:
        if aq is None: return default 
        value = aq.get(var_name)
        return value if value is not None else default
    except Exception as e: 
        # Se a conexão for REAL, re-eleva a exceção para o _send_data_loop tratar como perda de SimConnect
        if CONN_STATUS == "REAL":
            raise e
        return default

def fetch_all_data():
    """Busca dados COMPLETOS e atualiza o dicionário global."""
    global flight_data
    
    # 1. Coleta de VS e Coerção de Zero
    flight_data["vs"] = get_safe_value("VERTICAL_SPEED")
    if abs(flight_data["vs"]) < 0.5: flight_data["vs"] = 0.0 # Coerção de Zero
         
    # Coleta de Lat/Lng (Garantido)
    flight_data["lat"] = get_safe_value("PLANE_LATITUDE", default=0.0); flight_data["lng"] = get_safe_value("PLANE_LONGITUDE", default=0.0)
    
    # Coleta de Dados Primários (restante)
    flight_data["alt_ind"] = get_safe_value("PLANE_ALTITUDE"); flight_data["ias"] = get_safe_value("AIRSPEED_INDICATED")
    flight_data["tas"] = get_safe_value("AIRSPEED_TRUE"); flight_data["agl"] = get_safe_value("PLANE_ALT_ABOVE_GROUND"); flight_data["on_ground"] = get_safe_value("SIM_ON_GROUND")
    flight_data["g_force"] = get_safe_value("G_FORCE"); flight_data["total_fuel"] = get_safe_value("FUEL_TOTAL_QUANTITY"); flight_data["gear_left_pos"] = round(get_safe_value("GEAR_HANDLE_POSITION") * 100, 0)
    flight_data["engine_count"] = int(get_safe_value("NUMBER_OF_ENGINES", default=0)); flight_data["plane_bank_degrees"] = get_safe_value("PLANE_BANK_DEGREES", default=0.0)
    flight_data["engine_vibration_1"] = get_safe_value("GENERAL_ENG_VIBRATION:1", default=0.0)

    # Coleta de Status e Luzes
    flight_data["eng_combustion"] = get_safe_value("GENERAL_ENG_COMBUSTION:1", default=0); flight_data["light_beacon_on"] = get_safe_value("LIGHT_BEACON_ON", default=0)
    flight_data["light_landing_on"] = get_safe_value("LIGHT_LANDING_ON", default=0); flight_data["light_strobe_on"] = get_safe_value("LIGHT_STROBE_ON", default=0)

    # Lógica de Alertas
    alerts = flight_data["alerts"]
    alerts["overspeed_warning"] = get_safe_value("OVERSPEED_WARNING"); alerts["stall_warning"] = get_safe_value("STALL_WARNING"); alerts["stall_protection_active"] = get_safe_value("STALL_PROTECTION_ACTIVE")
    alerts["gpws_warning"] = get_safe_value("GPWS_WARNING"); alerts["flaps_speed_exceeded"] = get_safe_value("FLAPS_SPEED_EXCEEDED"); alerts["gear_warning_system_active"] = get_safe_value("GEAR_WARNING_SYSTEM_ACTIVE")
    alerts["engine_fire"] = get_safe_value("GENERAL_ENG_FIRE:1")
    if flight_data["eng_combustion"] == 1 and flight_data["light_beacon_on"] == 0: alerts["beacon_off_engine_on"] = 1
    else: alerts["beacon_off_engine_on"] = 0


# --- OTIMIZAÇÃO (Delta Encoding) ---
def create_rounded_data(source_data):
    """Cria um novo dicionário com as métricas arredondadas para a precisão definida."""
    global DATA_PRECISION
    rounded = source_data.copy()
    for key, precision in DATA_PRECISION.items():
        if key in rounded and isinstance(rounded[key], (float, int)):
            rounded[key] = round(rounded[key], precision)
    return rounded

def has_significant_change(current_data, last_data):
    if last_data is None: return True
    return current_data != last_data

# =================================================================
# 5. CLASSES DO CLIENTE WEBSOCKET E GUI
# =================================================================

class FlightMonitor:
    # MODIFICADO: numeric_id substituído por display_name
    def __init__(self, pilot_email: str, display_name: str, pilot_data: dict, master_app: 'MainApplication'):
        self.pilot_email = pilot_email; self.display_name = display_name # NOVO: Armazena o nome
        self.vatsim_id = pilot_data.get('vatsim_id', 'N/A'); self.ivao_id = pilot_data.get('ivao_id', 'N/A')
        self.running = True; self.ws_client = None; self.last_sent_data = None; self.packets_sent_count = 0; self.total_bytes_sent = 0.0
        self.last_send_time = time.time() 
        self.master_app = master_app
        self.transmitting = False 

    def start_monitor(self):
        """Inicia a thread de gerenciamento de conexão e reconexão."""
        global flight_data
        # MODIFICADO: Atualiza pilot_name em vez de pilot_id
        flight_data["pilot_name"] = self.display_name; 
        flight_data["vatsim_id"] = self.vatsim_id; 
        flight_data["ivao_id"] = self.ivao_id
        flight_data["client_disconnect"] = 0
        threading.Thread(target=self._connection_management_loop, daemon=True).start()
        
    def _connection_management_loop(self):
        RETRY_DELAY = 5 
        while self.running:
            print(f"Monitor Piloto: {self.display_name}. Tentando conectar a {WEBSOCKET_URL}...")
            self.ws_client = websocket.WebSocketApp(
                WEBSOCKET_URL, 
                on_open=self._on_open, 
                on_error=self._on_error, 
                on_close=self._on_close,
                on_message=self._on_message 
            )
            self.ws_client.run_forever() 
            if self.running:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [WS] Conexão perdida. Tentando reconectar em {RETRY_DELAY} segundos..."); self.last_sent_data = None 
                time.sleep(RETRY_DELAY)

    def _on_open(self, ws):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [WS] Conexão estabelecida. Enviando primeiro pacote de identificação...")
        # MODIFICADO: Envia pilot_name em vez de pilot_id
        initial_payload = json.dumps({
            "pilot_name": self.display_name, 
            "vatsim_id": self.vatsim_id, 
            "ivao_id": self.ivao_id,
            "packets_sent": 0, "mb_sent": 0.0
        })
        ws.send(initial_payload)
        
        threading.Thread(target=self._send_data_loop, daemon=True).start()

    def _on_error(self, ws, error): 
        self.transmitting = False
        self.master_app.after(0, self.master_app.current_frame.update_status, False, "ERRO DE CONEXÃO")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [WS ERROR] {error}")
        
    def _on_close(self, ws, close_status_code, close_msg): 
        self.transmitting = False 
        self.master_app.after(0, self.master_app.current_frame.update_status, False, "DESCONECTADO")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [WS] Conexão encerrada pelo servidor ou erro (Code: {close_status_code}).")

    def _on_message(self, ws, message):
            """Recebe e processa comandos e alertas do servidor."""
            try:
                data = json.loads(message)
                command = data.get("command") 
                
                if command == "START_TX":
                    self.transmitting = True
                    self.master_app.after(0, self.master_app.current_frame.update_status, True, "TRANSMITINDO (Online Rede)")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENT] Comando START_TX recebido. Iniciando transmissão de telemetria.")
                elif command == "STOP_TX": 
                    self.transmitting = False
                    self.master_app.after(0, self.master_app.current_frame.update_status, False, "PAUSADO (Offline/Solo)")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENT] Comando STOP_TX recebido. Pausando transmissão de telemetria.")
                elif command == "ALERT_CRITICAL":
                    alert_message = data.get("message", "Alerta Crítico Indefinido.")
                    self.master_app.after(0, lambda: messagebox.showwarning("ALERTA CRÍTICO DO SERVIDOR", alert_message))
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [ALERTA] Servidor: {alert_message}")

            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENT] Erro ao receber mensagem do servidor: {e}")

    def _send_data_loop(self):
        global HEARTBEAT_INTERVAL, CONN_STATUS
        
        simconnect_fail_logged = False
        
        while self.running:
            
            if not self.transmitting:
                time.sleep(1) 
                continue 
            
            try:
                fetch_all_data(); 
                current_rounded = create_rounded_data(flight_data)
                
                force_send = (time.time() - self.last_send_time) >= HEARTBEAT_INTERVAL

                if has_significant_change(current_rounded, self.last_sent_data) or force_send:
                    
                    self.last_sent_data = current_rounded.copy()

                    self.packets_sent_count += 1
                    
                    # Atualiza a GUI com os dados mais recentes
                    self.master_app.after(0, self.master_app.current_frame.update_data, current_rounded)
                    
                    # Envia o pacote de dados
                    payload_to_send = json.dumps({
                        **current_rounded, 
                        'mb_sent': self.total_bytes_sent / (1024 * 1024),
                        'packets_sent': self.packets_sent_count
                    })

                    message_size = len(payload_to_send.encode('utf-8'))
                    self.total_bytes_sent += message_size

                    self.ws_client.send(payload_to_send)
                    self.last_send_time = time.time() 
                
                if simconnect_fail_logged:
                    simconnect_fail_logged = False
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, CONN_STATUS)
                
                time.sleep(0.1)

            except websocket.WebSocketConnectionClosedException: break 
            except Exception as e: 
                # DETECÇÃO DE DESCONEXÃO DO SIMULADOR (SimConnect)
                if CONN_STATUS == "REAL" and not simconnect_fail_logged:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERROR] SimConnect Exception: {e}. Assumindo simulador fechado/desconectado.")
                    simconnect_fail_logged = True
                    self.transmitting = False 
                    self.master_app.after(0, self.master_app.current_frame.update_status, False, "SIMULADOR DESCONECTADO")
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, f"{CONN_STATUS} (FALHA)")

                    # Envia um pacote de desconexão limpa ao servidor (client_disconnect: 1)
                    try:
                        flight_data["client_disconnect"] = 1 
                        final_payload = json.dumps({
                            **create_rounded_data(flight_data), 
                            'client_disconnect': 1
                        })
                        self.ws_client.send(final_payload)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [WS] Enviado sinal de desconexão (SimConnect Lost).")
                        flight_data["client_disconnect"] = 0 
                    except:
                        pass 
                    
                    # **********************************************
                    # 🟢 CORREÇÃO CRÍTICA: Fechar a conexão SimConnect de forma limpa.
                    # Isso tenta evitar que o SimConnect DLL cause o erro fatal do SO.
                    global sm
                    if sm:
                        try:
                            sm.exit()
                            sm = None # Define como None para forçar a re-inicialização no próximo ciclo (se necessário)
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] SimConnect encerrado preventivamente.")
                        except Exception as sim_err:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [AVISO] Falha ao fechar SimConnect: {sim_err}")
                    # **********************************************
                    
                    break # Sai do loop de envio de dados.
                
                time.sleep(0.1) 

class LoginFormFrame(ttk.Frame):
    # (Conteúdo da LoginFormFrame inalterado)
    # MODIFICADO: A assinatura da função on_success agora recebe o nome (str) em vez do ID (int)
    def __init__(self, master, on_success: Callable[[str, str, str, dict], None], **kwargs):
        super().__init__(master, padding=30, **kwargs)
        self.on_success = on_success
        self.email_var = ttk.StringVar(); self.password_var = ttk.StringVar(); self.remember_var = ttk.BooleanVar(value=False) 
        
        # --- Layout da GUI ---
        ttk.Label(self, text=f"Login: {VA_KEY}", font=("TkDefaultFont", 18, "bold")).pack(pady=20)
        form_frame = ttk.Frame(self); form_frame.pack(pady=10, fill='x'); form_frame.columnconfigure(0, weight=1); form_frame.columnconfigure(1, weight=1);
        ttk.Label(form_frame, text="E-mail ou Username:", anchor='w').grid(row=0, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.email_var, width=40).grid(row=1, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        ttk.Label(form_frame, text="Senha:", anchor='w').grid(row=2, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.password_var, show="*", width=40).grid(row=3, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        ttk.Checkbutton(form_frame, text="Lembrar E-mail e Senha", variable=self.remember_var, bootstyle="round-toggle").grid(row=4, column=0, columnspan=2, pady=15, padx=5, sticky='w') 
        self.status_label = ttk.Label(form_frame, text="", bootstyle="info", font=("-size 10 -weight bold"), anchor='center'); self.status_label.grid(row=5, column=0, columnspan=2, pady=(15, 5), sticky='ew') 
        ttk.Button(form_frame, text="Entrar", command=self._handle_login, bootstyle="success").grid(row=6, column=0, columnspan=2, pady=(5, 10))
        self._load_saved_credentials()

    def _load_saved_credentials(self):
         email_saved, password_saved, remember_me = load_credentials()
         if email_saved: self.email_var.set(email_saved)
         if remember_me:
             if password_saved: self.password_var.set(password_saved); self.remember_var.set(True)
             self.status_label.config(text="Credenciais salvas carregadas.", bootstyle="info")
            
    def _handle_login(self):
        email = self.email_var.get().strip(); password = self.password_var.get().strip(); remember = self.remember_var.get()
        if not email or not password: self.status_label.config(text="Preenchimento obrigatório.", bootstyle="danger"); return
        
        threading.Thread(target=self._process_login, args=(email, password, remember), daemon=True).start()
        
    def _process_login(self, email: str, password: str, remember: bool):
        """Lógica de login movida para um thread separado."""
        self.master.after(0, lambda: self.status_label.config(text="Verificando credenciais (1/3)...", bootstyle="info"))
        
        if not check_login(email, password):
            self.master.after(0, lambda: self.status_label.config(text="Falha no login. Verifique e-mail/senha.", bootstyle="danger"))
            delete_credentials(email); return 
            
        self.master.after(0, lambda: self.status_label.config(text="Login OK. Verificando status de piloto (2/3)...", bootstyle="info"))
        
        pilot_data = get_validated_pilot_data(email) 
        if not pilot_data:
            self.master.after(0, lambda: self.status_label.config(text="Login OK, mas piloto não está na lista de validados.", bootstyle="warning"))
            delete_credentials(email); return 
        
        # NOVO: Obtém o nome de exibição para usar como identificador
        display_name = pilot_data.get('display_name', 'PILOTO DESCONHECIDO')

        if remember: save_credentials(email, password)
        else: delete_credentials(email) 
            
        # MODIFICADO: Usa o nome de exibição em vez do ID numérico
        self.master.after(0, lambda: self.status_label.config(text=f"Piloto Validado! Nome: {display_name}", bootstyle="success"))
        self.master.after(1000, lambda: self.on_success(email, password, display_name, pilot_data))


class MonitorFrame(ttk.Frame):
    # (Conteúdo da MonitorFrame inalterado)
    """
    Painel de Monitoramento Detalhado e Dinâmico (Fix para KeyError: 'vs_label').
    """
    # MODIFICADO: pilot_id (int) substituído por pilot_name (str)
    def __init__(self, master, pilot_name: str, conn_status: str, **kwargs):
        super().__init__(master, padding=20, **kwargs)
        self.pilot_name = pilot_name
        self.vs_widget = None # Variável para armazenar a referência do widget VS
        
        # Dicionário para armazenar as variáveis de controle da GUI
        self.data_vars = {
            "alt_ind": ttk.StringVar(value="0 ft"), "vs": ttk.StringVar(value="0 fpm"), "ias": ttk.StringVar(value="0 kts"), 
            "agl": ttk.StringVar(value="0 ft"), "g_force": ttk.StringVar(value="1.0 g"), "fuel": ttk.StringVar(value="0 gal")
        }
        
        # --- Layout Principal ---
        ttk.Label(self, text=f"Monitor de Telemetria", font=("TkDefaultFont", 12, "bold")).pack(pady=(0, 10))
        
        # Indicador de Status de Conexão SimConnect
        self.status_frame = ttk.Frame(self); self.status_frame.pack(fill='x', pady=5)
        # MODIFICADO: Exibe o nome do piloto
        ttk.Label(self.status_frame, text=f"Piloto: {pilot_name} | SimConnect:").grid(row=0, column=0, padx=5, sticky='w')
        self.sim_status_label = ttk.Label(self.status_frame, text=conn_status, bootstyle="info"); self.sim_status_label.grid(row=0, column=1, sticky='e')
        
        ttk.Separator(self).pack(fill='x', pady=5)
        
        # Indicador de Status de Transmissão (Principal)
        self.tx_status_label = ttk.Label(self, text="AGUARDANDO SERVIDOR...", font=("TkDefaultFont", 10, "bold"), bootstyle="warning"); self.tx_status_label.pack(fill='x', pady=5)
        
        ttk.Separator(self).pack(fill='x', pady=10)
        
        # Painel de Dados em Tempo Real (NOVO PAINEL VISUAL)
        data_frame = ttk.Frame(self); data_frame.pack(fill='both', expand=True)
        
        self._create_data_row(data_frame, "ALTITUDE (MSL):", "alt_ind", 0)
        self._create_data_row(data_frame, "VS (FPM):", "vs", 1)
        self._create_data_row(data_frame, "IAS (KTS):", "ias", 2)
        self._create_data_row(data_frame, "AGL (FT):", "agl", 3)
        self._create_data_row(data_frame, "G-FORCE:", "g_force", 4)
        self._create_data_row(data_frame, "TOTAL FUEL:", "fuel", 5)
        
        # Botão de Logoff
        ttk.Separator(self).pack(fill='x', pady=10)
        ttk.Button(self, text="Logoff", command=master._handle_logoff, bootstyle="danger-outline").pack(pady=(5, 0))


    def _create_data_row(self, parent, label_text, var_key, row_num):
        row = ttk.Frame(parent, padding=2); row.pack(fill='x')
        ttk.Label(row, text=label_text, width=15).pack(side='left', padx=(0, 10))
        
        # CHAVE 1: Cria o widget de valor
        value_widget = ttk.Label(row, textvariable=self.data_vars[var_key], font=("-size 11 -weight bold"), bootstyle="light")
        value_widget.pack(side='right', fill='x', expand=True)

        # CHAVE 2: Armazena a referência para o widget VS (para mudança de cor)
        if var_key == "vs":
            self.vs_widget = value_widget


    def update_data(self, data: dict):
        """Atualiza todas as variáveis da GUI com os novos dados."""
        # Formata com ponto como separador de milhar (para pt-BR)
        self.data_vars["alt_ind"].set(f"{int(data['alt_ind']):,} ft".replace(',', '.'))
        self.data_vars["vs"].set(f"{int(data['vs']):,} fpm".replace(',', '.'))
        self.data_vars["ias"].set(f"{data['ias']:.1f} kts")
        self.data_vars["agl"].set(f"{int(data['agl']):,} ft".replace(',', '.'))
        self.data_vars["g_force"].set(f"{data['g_force']:.1f} g")
        self.data_vars["fuel"].set(f"{int(data['total_fuel']):,} gal".replace(',', '.'))
        
        # Atualiza a cor da VS usando a referência armazenada
        if self.vs_widget:
            if data['vs'] > 100:
                self.data_vars["vs"].set(f"+{self.data_vars['vs'].get()}")
                self.vs_widget.config(bootstyle="success")
            elif data['vs'] < -100:
                self.vs_widget.config(bootstyle="danger")
            else:
                self.vs_widget.config(bootstyle="light")


    def update_status(self, is_transmitting: bool, message: str):
        """Atualiza o status de transmissão."""
        style = "success" if is_transmitting else "danger"
        self.tx_status_label.config(text=message, bootstyle=style)

    def update_sim_status(self, message: str):
        """Atualiza o status do SimConnect."""
        self.sim_status_label.config(text=message, bootstyle="info")
            

class MainApplication(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title(f"Monitor de Voo - Login {VA_KEY}"); self.geometry("300x480"); self.resizable(False, False)
        
        self._update_in_progress = False 
        
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_dir, 'assets', 'icons', 'skymetrics.ico')
            self.iconbitmap(icon_path)
        except Exception as e:
            pass
            
        self.monitor = None
        self.current_frame = None
        self.current_pilot_email = None 
        self.protocol("WM_DELETE_WINDOW", self._on_app_closing)
        
        # NOVO: Variáveis para o System Tray
        self.tray_icon = None
        self.minimized_to_tray = False
        
        self.after(1000, self.start_periodic_update_check)
        
        # Tenta Login Automático
        email, password, remember_me = load_credentials()
        if email and password and remember_me:
             self._attempt_auto_login(email, password)
        else:
            self._show_login_form()

    # NOVO: Lógica do Ícone de Bandeja
    def _show_window_from_tray(self, icon, item):
        """Restaura a janela principal e fecha o ícone da bandeja."""
        if self.tray_icon:
            icon.stop()
            self.tray_icon = None
        self.after(0, self.deiconify)
        self.minimized_to_tray = False

    def _on_logoff_from_tray(self, icon, item):
        """Inicia o processo de logoff a partir do menu do ícone da bandeja."""
        if self.tray_icon:
            icon.stop()
            self.tray_icon = None
        self.minimized_to_tray = False
        self.after(0, self._handle_logoff) # Executa o logoff na thread principal do Tkinter

    def _on_quit_from_tray(self, icon, item):
        """Encerra o aplicativo a partir do menu do ícone da bandeja."""
        if self.tray_icon:
            icon.stop()
            self.tray_icon = None
        self.minimized_to_tray = False
        self.after(0, self._on_app_closing) # Executa o encerramento na thread principal do Tkinter

    def _start_tray_icon(self):
        """Cria e inicia o ícone de bandeja em uma thread separada."""
        if not PYSTRAY_AVAILABLE or self.tray_icon:
            return

        # Certifica-se de que a janela está oculta (mas não destruída)
        # Usamos self.withdraw() aqui. O Tkinter é thread-safe para isso se chamado
        # a partir de self.after (ou garantimos que a thread do ícone restaure a janela principal)
        self.withdraw()
        self.minimized_to_tray = True
        
        # Define o menu do ícone da bandeja
        menu = (
            pystray.MenuItem('Mostrar Monitor', self._show_window_from_tray, default=True),
            pystray.MenuItem('Logoff', self._on_logoff_from_tray),
            pystray.MenuItem('Sair', self._on_quit_from_tray)
        )

        # Cria o ícone
        try:
            icon_image = Image.open(ICON_PATH)
            self.tray_icon = pystray.Icon(
                'skymetrics_monitor', 
                icon_image, 
                'SkyMetrics Monitor', 
                menu
            )
            # O ícone deve ser rodado em uma thread separada, pois é um loop bloqueante
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Aplicação minimizada para a bandeja.")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [ERRO] Falha ao iniciar o ícone da bandeja: {e}")
            # Se falhar, restaura a janela
            self.deiconify()
            self.minimized_to_tray = False


    def stop_monitor_and_simconnect(self):
        """Encerra o monitor de forma segura e a conexão SimConnect."""
        global sm, CONN_STATUS
        
        if self.monitor:
            self.monitor.running = False
            
        if CONN_STATUS == "REAL" and sm: 
            try:
                sm.exit()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Conexão SimConnect encerrada.")
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [AVISO] Falha ao fechar SimConnect: {e}")

    def start_periodic_update_check(self):
        """Inicia o loop de checagem de atualização a cada 60 minutos (3600 segundos)."""
        if not self.winfo_exists() or self._update_in_progress:
            return

        threading.Thread(target=check_for_update, args=(self, True), daemon=True).start()
        
        self.after(60 * 60 * 1000, self.start_periodic_update_check)

    def _attempt_auto_login(self, email: str, password: str):
        """Inicia o formulário e tenta logar automaticamente."""
        self.login_frame = LoginFormFrame(self, on_success=self._on_login_success) 
        self.login_frame.pack(fill=BOTH, expand=YES)
        self.current_frame = self.login_frame
        
        self.login_frame.email_var.set(email)
        self.login_frame.password_var.set(password)
        self.login_frame.remember_var.set(True)
        
        self.login_frame.status_label.config(text="Tentando Login Automático...", bootstyle="info")
        
        threading.Thread(target=self.login_frame._process_login, args=(email, password, True), daemon=True).start()


    def _on_app_closing(self):
        if self._update_in_progress:
             self.destroy()
             return
        
        # NOVO: Para o ícone da bandeja se estiver rodando
        if self.tray_icon:
            self.tray_icon.stop()
            
        self.stop_monitor_and_simconnect()
        self.destroy()

    def _show_login_form(self):
        if self.current_frame: self.current_frame.destroy()
        
        self.geometry("300x480") 
        
        self.login_frame = LoginFormFrame(self, on_success=self._on_login_success) 
        self.login_frame.pack(fill=BOTH, expand=YES)
        self.current_frame = self.login_frame
        
        # Garante que a janela está visível (em caso de logoff vindo da bandeja)
        if self.minimized_to_tray:
            self.deiconify()
            self.minimized_to_tray = False

    # MODIFICADO: numeric_id substituído por display_name
    def _on_login_success(self, email: str, password: str, display_name: str, pilot_data: dict):
        if self.current_frame: self.current_frame.destroy()
        
        self.current_pilot_email = email
        
        self.geometry("350x380") 
        self.resizable(False, False)
        # MODIFICADO: Exibe o nome do piloto no título
        self.title(f"Monitor de Voo {VA_KEY} - Piloto: {display_name}")
        
        # MODIFICADO: Passa o nome do piloto para o FlightMonitor
        self.monitor = FlightMonitor(email, display_name, pilot_data, self)
        self.monitor.start_monitor()
        
        print("-" * 50); print("CONEXÃO ESTABELECIDA E MONITOR DE DADOS INICIADO!"); print("-" * 50)
        
        # Cria o Novo Painel de Monitoramento (MonitorFrame)
        # MODIFICADO: Passa o nome do piloto para o MonitorFrame
        monitor_frame = MonitorFrame(self, display_name, CONN_STATUS)
        monitor_frame.pack(fill=BOTH, expand=YES)
        self.current_frame = monitor_frame
        
        # NOVO: Minimiza para a bandeja após sucesso
        self.after(500, self._start_tray_icon)

    def _handle_logoff(self):
        """Encerra o monitor, apaga a senha e desativa o autologin, mantendo o email."""
        
        # NOVO: Para o ícone da bandeja se estiver rodando
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
            self.minimized_to_tray = False
        
        self.stop_monitor_and_simconnect()
        
        if self.current_pilot_email:
            delete_credentials(self.current_pilot_email, clear_email=False)
            print("-" * 50); print(f"Logoff bem-sucedido. A senha de '{self.current_pilot_email}' foi removida. O email foi mantido para a próxima vez."); print("-" * 50)

        # Garante que a janela está visível antes de mostrar o formulário de login
        self.deiconify()
        self._show_login_form()


if __name__ == "__main__":
    app = MainApplication(); app.mainloop()