# login_client_kafly.py (FINAL: Cliente Completo com Configuração Externa)

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

# =================================================================
# 1. CONFIGURAÇÕES E ENDPOINTS (Lidos do .ini)
# =================================================================
CONFIG_FILE = 'client_config.ini'
CLIENT_CONFIG_SECTION = 'CLIENT_CONFIG' 
CLIENT_LOGIN_SECTION = 'LOGIN_CREDENTIALS'

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


# --- Variáveis Globais de Estado ---
CONN_STATUS = "REAL" 
sm = None 
aq = None 
last_sent_data = None 

# PRECISION MAP: Define a precisão funcional de cada métrica
# Lat/Lng e G_Force reduzidos para evitar jitter constante no solo.
DATA_PRECISION = { 
    "alt_ind": 0, "vs": 0, "ias": 1, "tas": 1, "agl": 0, "on_ground": 0, 
    "total_fuel": 0, "gear_left_pos": 0, "g_force": 1, # DE 2 PARA 1
    "engine_count": 0, 
    "lat": 3, "lng": 3, # DE 4 PARA 3
    "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 1, 
    "engine_vibration_1": 0,
}

# =================================================================
# 2. LÓGICA DE AUTENTICAÇÃO, ID e CREDENCIAIS
# =================================================================

def generate_pilot_numeric_id(email: str) -> int:
    email_bytes = email.lower().encode('utf-8')
    return zlib.crc32(email_bytes) & 0xFFFFFFFF 

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

def delete_credentials(email: str):
    try:
        keyring_username = email
        try: keyring.delete_password(KEYRING_SERVICE_ID, keyring_username)
        except Exception: pass 
        
        global config
        config.read(CONFIG_FILE)
        if CLIENT_LOGIN_SECTION in config: 
             config[CLIENT_LOGIN_SECTION]['remember_me'] = 'False'
             config[CLIENT_LOGIN_SECTION]['pilot_email'] = ''
        with open(CONFIG_FILE, 'w') as configfile: config.write(configfile)
    except Exception as e: print(f"Erro ao deletar credenciais: {e}")

# =================================================================
# 3. LÓGICA DO CLIENTE WEBSOCKET (Coleta de Dados Completos)
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
        if var == "SIM_ON_GROUND": return 1 if self.get("ALTITUDE ABOVE GROUND") < 10 else 0
        if var == "GEAR_HANDLE_POSITION": return 1.0 if cycle_60s < 10 else 0.0
        if var == "NUMBER_OF_ENGINES": return 2
        if var == "PLANE_BANK_DEGREES": return 45 if 2 < cycle_60s < 4 else 5 
        if var == "G_FORCE": return 1.0 + 0.8 * abs(0.5 - (cycle_60s / 60))
        if var == "FUEL_TOTAL_QUANTITY": return 8500.5 + 500 * random.uniform(-0.01, 0.01)
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
    sm = MockSimConnect(); aq = MockAircraftRequests(sm); CONN_STATUS = "SIMULADO" 

# --- ESTRUTURA DE DADOS COMPLETA (Skymetrics) ---
flight_data = {
    "alt_ind": 0, "vs": 0.0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
    "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, 
    "engine_vibration_1": 0.0,
    "pilot_id": "", "vatsim_id": "", "ivao_id": "", 
    "alerts": {"overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0,}
}

def get_safe_value(var_name, default=0):
    try:
        if aq is None: return default 
        value = aq.get(var_name)
        return value if value is not None else default
    except Exception: return default

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
    flight_data["tas"] = get_safe_value("AIRSPEED_TRUE"); flight_data["agl"] = get_safe_value("ALTITUDE ABOVE GROUND"); flight_data["on_ground"] = get_safe_value("SIM_ON_GROUND")
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
# 4. CLASSE MONITOR E GUI
# =================================================================

class FlightMonitor:
    def __init__(self, pilot_email: str, numeric_id: int, pilot_data: dict):
        self.pilot_email = pilot_email; self.numeric_id = numeric_id
        self.vatsim_id = pilot_data.get('vatsim_id', 'N/A'); self.ivao_id = pilot_data.get('ivao_id', 'N/A')
        self.running = True; self.ws_client = None; self.last_sent_data = None; self.packets_sent_count = 0; self.total_bytes_sent = 0.0
        self.last_send_time = time.time() # NOVO: Inicializa o tempo do último envio

    def start_monitor(self):
        """Inicia a thread de gerenciamento de conexão e reconexão."""
        global flight_data
        flight_data["pilot_id"] = str(self.numeric_id); flight_data["vatsim_id"] = self.vatsim_id; flight_data["ivao_id"] = self.ivao_id
        threading.Thread(target=self._connection_management_loop, daemon=True).start()
        
    def _connection_management_loop(self):
        RETRY_DELAY = 5 
        while self.running:
            print(f"Monitor ID: {self.numeric_id}. Tentando conectar a {WEBSOCKET_URL}...")
            self.ws_client = websocket.WebSocketApp(
                WEBSOCKET_URL, on_open=self._on_open, on_error=self._on_error, on_close=self._on_close
            )
            self.ws_client.run_forever() 
            if self.running:
                print(f"[WS] Conexão perdida. Tentando reconectar em {RETRY_DELAY} segundos..."); self.last_sent_data = None 
                time.sleep(RETRY_DELAY)

    def _on_open(self, ws):
        print(f"[WS] Conexão estabelecida. Iniciando envio de dados...")
        threading.Thread(target=self._send_data_loop, daemon=True).start()

    def _on_error(self, ws, error): print(f"[WS ERROR] {error}")
    def _on_close(self, ws, close_status_code, close_msg): print(f"[WS] Conexão encerrada pelo servidor ou erro (Code: {close_status_code}).")

    def _send_data_loop(self):
        global HEARTBEAT_INTERVAL
        while self.running:
            try:
                fetch_all_data(); current_rounded = create_rounded_data(flight_data)
                
                # Heartbeat: Envia se houver mudança OU se o tempo limite for atingido
                force_send = (time.time() - self.last_send_time) >= HEARTBEAT_INTERVAL

                if has_significant_change(current_rounded, self.last_sent_data) or force_send:
                    
                    self.packets_sent_count += 1
                    payload = json.dumps(current_rounded)
                    
                    message_size = len(payload.encode('utf-8'))
                    self.total_bytes_sent += message_size
                    current_rounded['mb_sent'] = self.total_bytes_sent / (1024 * 1024)
                    current_rounded['packets_sent'] = self.packets_sent_count

                    self.ws_client.send(payload)
                    self.last_sent_data = current_rounded.copy()
                    self.last_send_time = time.time() # Atualiza o tempo do último envio
                
            except websocket.WebSocketConnectionClosedException: break 
            except Exception as e: time.sleep(0.1) 
            time.sleep(0.1) 


class LoginFormFrame(ttk.Frame):
    def __init__(self, master, on_success: Callable[[str, str, int, dict], None], **kwargs):
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
        self.status_label.config(text="Verificando credenciais (1/3)...", bootstyle="info"); self.update() 
        if not check_login(email, password):
            self.status_label.config(text="Falha no login. Verifique e-mail/senha.", bootstyle="danger"); delete_credentials(email); return
        self.status_label.config(text="Login OK. Verificando status de piloto (2/3)...", bootstyle="info"); self.update()
        pilot_data = get_validated_pilot_data(email) 
        if not pilot_data:
            self.status_label.config(text="Login OK, mas piloto não está na lista de validados.", bootstyle="warning"); delete_credentials(email); return
        
        if remember: save_credentials(email, password)
        else: delete_credentials(email)
            
        numeric_id = generate_pilot_numeric_id(email)
        self.status_label.config(text=f"Piloto Validado! ID: {numeric_id}", bootstyle="success")
        self.after(1000, lambda: self.on_success(email, numeric_id, pilot_data))


class MainApplication(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title(f"Monitor de Voo - Login {VA_KEY}"); self.geometry("450x480"); self.resizable(False, False)
        self.monitor = None; self._show_login_form(); self.protocol("WM_DELETE_WINDOW", self._on_app_closing)
    def _on_app_closing(self):
        if self.monitor: self.monitor.running = False;
        if CONN_STATUS == "REAL": sm.exit() 
        self.destroy()
    def _show_login_form(self):
        self.login_frame = LoginFormFrame(self, on_success=self._on_login_success) 
        self.login_frame.pack(fill=BOTH, expand=YES)
    def _on_login_success(self, email: str, numeric_id: int, pilot_data: dict):
        self.login_frame.destroy(); self.geometry("400x150"); self.title(f"Monitor de Voo {VA_KEY} - ID: {numeric_id}")
        self.monitor = FlightMonitor(email, numeric_id, pilot_data); self.monitor.start_monitor()
        
        print("-" * 50); print("CONEXÃO ESTABELECIDA E MONITOR DE DADOS INICIADO!"); print("-" * 50)
        
        ttk.Label(self, text=f"Transmissão iniciada para ID: {numeric_id}", font=("TkDefaultFont", 12)).pack(pady=20)
        ttk.Label(self, text=f"Status SimConnect: {CONN_STATUS}", font=("TkDefaultFont", 10), bootstyle="info").pack(pady=5)

if __name__ == "__main__":
    app = MainApplication(); app.mainloop()