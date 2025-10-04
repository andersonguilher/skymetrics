# sim_data_monitor.py 
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import time
import sys
import random
import os
from PIL import Image, ImageTk
import gui_elements 
import json 
from datetime import datetime, timedelta 

# =================================================================
# 0. LÓGICA DE LOGGING DE EVENTOS
# =================================================================

LOG_FILE = "flight_events.json"
COOLDOWN_SECONDS = 60

class FlightEventLogger:
    """Gerencia o log de eventos com timestamp e cooldown."""
    def __init__(self):
        # Dicionário: {'event_name': datetime_of_last_log}
        self.cooldowns = {} 
        # Flag para garantir que o evento de combustível inicial só seja logado uma vez por sessão de voo
        self.initial_fuel_logged = False 
        self.ensure_log_file_exists()

    def ensure_log_file_exists(self):
        """Cria o arquivo JSON se ele não existir, garantindo que seja um array válido."""
        if not os.path.exists(LOG_FILE) or os.stat(LOG_FILE).st_size == 0:
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump([], f)

    def _should_log(self, event_name):
        """Verifica se o evento pode ser logado (se já se passaram 60 segundos)."""
        if event_name not in self.cooldowns:
            return True
        
        last_log_time = self.cooldowns[event_name]
        return datetime.now() >= last_log_time + timedelta(seconds=COOLDOWN_SECONDS)

    def log_event(self, event_name: str, flight_data: dict, description: str = ""):
        """Registra o evento no arquivo JSON, aplicando o cooldown."""
        
        # Cooldown só se aplica a alertas recorrentes. Eventos únicos (Decolagem, Pouso) são sempre logados.
        if "ALERTA:" in event_name and not self._should_log(event_name):
            return False
        
        # Dados de localização
        lat = flight_data.get("lat", "N/A")
        lng = flight_data.get("lng", "N/A")

        event = {
            "evento": event_name,
            "descricao": description,
            "data_hora": datetime.now().isoformat(),
            "lat": lat,
            "lng": lng
        }
        
        try:
            # Lógica para adicionar objeto a um array JSON existente no arquivo
            with open(LOG_FILE, 'r+', encoding='utf-8') as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell() - 1 
                
                if pos < 0: # Arquivo vazio ou [
                     f.seek(0)
                     f.write(json.dumps([event], indent=4))
                else:
                    # Vai para o final, remove ']', adiciona ',' + novo evento + ']'
                    f.seek(pos)
                    f.truncate()
                    
                    if pos > 1: # Se já tem eventos, adiciona vírgula
                        f.write(',')

                    # Adiciona novo evento e fecha o array JSON
                    f.write(json.dumps(event, indent=4).strip() + '\n]')

            # Atualiza o timestamp do cooldown para alertas
            if "ALERTA:" in event_name:
                self.cooldowns[event_name] = datetime.now()
            return True

        except Exception as e:
            # Em ambientes com segurança alta (ex: sandboxed), pode haver falha de I/O
            print(f"Erro ao logar evento {event_name}: {e}")
            return False

# Inicializa o logger globalmente
event_logger = FlightEventLogger()


# =================================================================
# 1. SIMCONNECT MOCKUP E INICIALIZAÇÃO 
# =================================================================

# FIX: MOVIDAS AS DEFINIÇÕES PARA O ESCOPO GLOBAL
class MockSimConnect:
    def exit(self): pass

class MockAircraftRequests:
    """Simula a obtenção de dados e a lógica de alerta do SimConnect."""
    
    def __init__(self, sm=None):
        self._start_time = time.time()
        self._num_engines = 2 

    def get(self, var):
        t = time.time() - self._start_time
        
        cycle_10s = t % 10  
        cycle_20s = t % 20  
        cycle_30s = t % 30  
        cycle_60s = t % 60  
        
        if var == "PLANE_ALTITUDE":
            # Simula subida/descida para testar alertas de altitude
            alt = 10000 + 5000 * random.uniform(-0.1, 0.1)
            # Simula voo acima de 11.5k e descida
            if t > 60:
                 alt = 12000 + 1000 * random.uniform(-0.1, 0.1)
            return alt
        
        if var == "AIRSPEED_INDICATED":
            # Simula IAS > 250 kts em baixa altitude para testar alerta
            return 280 if t > 30 and t < 40 else 215 + 65 * random.uniform(-0.05, 0.05)
            
        if var == "AIRSPEED_TRUE":
            return self.get("AIRSPEED_INDICATED") * 1.1 + 10 
            
        if var == "FUEL_TOTAL_QUANTITY":
            return 8500.5 + 500 * random.uniform(-0.01, 0.01)
            
        if var in ["PLANE_ALT_ABOVE_GROUND", "ALTITUDE ABOVE GROUND"]:
            return 500 if cycle_60s < 5 or cycle_60s > 50 else 10000
            
        if var in ["SIM_ON_GROUND", "PLANE_ON_GROUND"]:
            return 1 if self.get("PLANE_ALT_ABOVE_GROUND") < 10 else 0
            
        if var == "G_FORCE":
            return 1.0 + 0.8 * abs(0.5 - (cycle_10s / 10)) 
        
        if var == "GEAR_HANDLE_POSITION":
            return 1.0 if cycle_60s < 10 else (0.0 if cycle_60s > 20 else 1.0 - (cycle_60s - 10) * 0.1)
            
        if var == "NUMBER_OF_ENGINES":
             return self._num_engines

        # MOCKUP: Index 0 (Desligado)
        if var == "GENERAL_ENG_COMBUSTION:0":
             return 0 
        
        # CORREÇÃO MOCKUP: Index 1 (Ativo, conforme Simvar Watcher do usuário)
        if var == "GENERAL_ENG_COMBUSTION:1":
             return 1 if cycle_60s > 5 else 0 

        # CORREÇÃO MOCKUP: SimVar com underscore para as luzes
        if var == "LIGHT_BEACON_ON": 
             # Simula Beacon desligado entre 15s e 25s (com motor ligado) para testar alerta
             # Usamos Index 1 para verificar o status do motor
             return 0 if 15 < cycle_60s < 25 and self.get("GENERAL_ENG_COMBUSTION:1") == 1 else 1 
        
        # CORREÇÃO MOCKUP: SimVar com underscore para as luzes
        if var == "LIGHT_LANDING_ON": 
             # Simula Landing ON acima de 11.5k para testar alerta
             if self.get("PLANE_ALTITUDE") > 11500 and 65 < t < 75: 
                 return 1 # ON acima de 11.5k
             return 1 if self.get("PLANE_ALTITUDE") < 10000 else 0 
             
        # CORREÇÃO MOCKUP: SimVar com underscore para as luzes
        if var == "LIGHT_STROBE_ON":
             return 1 if self.get("SIM_ON_GROUND") == 0 else 0 

        # NOVO: Localização (mockup estático)
        if var == "PLANE LATITUDE":
            return -23.5505 + 0.01 * (t/100) # Simula São Paulo
        if var == "PLANE LONGITUDE":
            return -46.6333 + 0.01 * (t/100)
        
        if var == "OVERSPEED_WARNING":
            return 1 if 4 < cycle_20s < 6 else 0
        if var == "STALL_WARNING":
            return 1 if 10 < cycle_30s < 12 else 0 
        if var == "STALL_PROTECTION_ACTIVE":
             return 1 if 20 < cycle_30s < 22 else 0
        if var == "GPWS_WARNING":
             return 1 if 15 < cycle_20s < 17 else 0
        if var == "FLAPS_SPEED_EXCEEDED":
             return 1 if 18 < cycle_20s < 19 else 0
        if var == "GEAR_WARNING_SYSTEM_ACTIVE":
            return 1 if self.get("GEAR_HANDLE_POSITION") < 0.1 and self.get("PLANE_ALT_ABOVE_GROUND") < 500 else 0

        if var.startswith("GENERAL_ENG_FIRE"):
            index = int(var.split(":")[-1])
            if index == 0 and 10 < cycle_60s < 15:
                return 1
            return 0
            
        if var.startswith("GENERAL_ENG_VIBRATION"):
            index = int(var.split(":")[-1])
            if index == 1 and 25 < cycle_30s < 28:
                return 1200 
            return 500
            
        if var == "PLANE_BANK_DEGREES":
            return 45 if 2 < cycle_10s < 4 else 5 

        return 0

try:
    # Tenta importar e inicializar o SimConnect REAL
    from SimConnect import SimConnect, AircraftRequests
    sm = SimConnect()
    aq = AircraftRequests(sm)
    CONN_STATUS = "REAL" 
    
except ImportError:
    sm = MockSimConnect()
    aq = MockAircraftRequests(sm)
    CONN_STATUS = "SIMULADO"
except Exception:
    sm = MockSimConnect()
    aq = MockAircraftRequests(sm)
    CONN_STATUS = "SIMULADO"


# --- Armazenamento de Dados Globais ---
flight_data = {
    "alt_ind": 0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0,
    "vs": 0.0,
    "total_fuel": 0,
    "gear_left_pos": 0, "g_force": 1.0,
    "engine_count": 0, 
    
    # NOVAS Variáveis
    "lat": 0.0, "lng": 0.0,
    "eng_combustion": 0, # Status de combustão do motor 1 (0 ou 1)
    "light_beacon_on": 0, 
    "light_landing_on": 0, 
    "light_strobe_on": 0,
    
    "is_airborne": False,
    "has_landed": False,
    "landing_vs": 0.0,
    
    "network_online": "N/A",
    
    "alerts": {
        "overspeed_warning": 0, "stall_warning": 0, "stall_protection_active": 0,
        "gear_warning_system_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0,
        "bank_alert": 0, "g_force_alert": 0,
        "engine_fire": [], "engine_vibration_high": [],
        # NOVOS ALERTAS
        "beacon_off_engine_on": 0,
        "ias_high_below_10k": 0,
        "lights_on_above_10k": 0,
        "lights_off_below_8500": 0,
    }
}

# =================================================================
# 2. FUNÇÕES DE DADOS E LÓGICA DE ALERTA 
# =================================================================

def get_safe_value(var_name, default=0):
    """Obtém um valor seguro do SimConnect/Mockup."""
    try:
        value = aq.get(var_name)
        return value if value is not None else default
    except Exception:
        return default

def fetch_all_data():
    """Busca dados, aplica a lógica de alerta e LOGA eventos."""
    global flight_data, event_logger
    
    # --- Coleta de Dados Primários e NOVOS DADOS ---
    flight_data["alt_ind"] = get_safe_value("PLANE_ALTITUDE")
    flight_data["ias"] = get_safe_value("AIRSPEED_INDICATED")
    flight_data["tas"] = get_safe_value("AIRSPEED_TRUE")
    flight_data["agl"] = get_safe_value("PLANE_ALT_ABOVE_GROUND")
    flight_data["on_ground"] = get_safe_value("SIM_ON_GROUND")
    flight_data["vs"] = get_safe_value("VERTICAL_SPEED")
    
    flight_data["total_fuel"] = get_safe_value("FUEL_TOTAL_QUANTITY") 
    
    gear_raw = get_safe_value("GEAR_HANDLE_POSITION")
    flight_data["gear_left_pos"] = round(gear_raw * 100, 0)
    
    flight_data["g_force"] = get_safe_value("G_FORCE")
    flight_data["engine_count"] = int(get_safe_value("NUMBER_OF_ENGINES", default=0))

    # CORREÇÃO: Coleta de Status com Index 1 (conforme ambiente do usuário)
    flight_data["eng_combustion"] = get_safe_value("GENERAL_ENG_COMBUSTION:1", default=0)
    flight_data["light_beacon_on"] = get_safe_value("LIGHT_BEACON_ON", default=0)
    flight_data["light_landing_on"] = get_safe_value("LIGHT_LANDING_ON", default=0)
    flight_data["light_strobe_on"] = get_safe_value("LIGHT_STROBE_ON", default=0)
    flight_data["lat"] = get_safe_value("PLANE LATITUDE", default=0.0)
    flight_data["lng"] = get_safe_value("PLANE LONGITUDE", default=0.0)

    # =========================================================
    # LÓGICA DE EVENTOS (LOGGING)
    # =========================================================

    # --- EVENTO 1: COMBUSTÍVEL INICIAL (Ao Ligar o Motor) ---
    if flight_data["eng_combustion"] == 1 and not event_logger.initial_fuel_logged:
        event_logger.log_event("COMBUSTIVEL_INICIAL", flight_data, 
                               description=f"Motor ligado. Combustível: {flight_data['total_fuel']:,.0f} gal")
        event_logger.initial_fuel_logged = True 

    # --- LÓGICA DE DECOLAGEM E POUSO (Atualizada para Logar) ---

    is_airborne_prev = flight_data["is_airborne"]
    has_landed_prev = flight_data["has_landed"]

    # --- 1. DETECÇÃO DE DECOLAGEM ---
    if (not is_airborne_prev and 
        flight_data["agl"] > 50 and 
        flight_data["ias"] > 40):
        
        flight_data["is_airborne"] = True
        flight_data["has_landed"] = False
        print("--- DECOLAGEM DETECTADA ---")
        event_logger.log_event("DECOLAGEM", flight_data, description="Decolagem detectada. Aeronave no ar.")

        # LOGGING ESPECÍFICO DE LUZES NA DECOLAGEM
        if flight_data["light_landing_on"] == 0:
            event_logger.log_event("ALERTA:LANDING_OFF_DECOLAGEM", flight_data, description="Landing Lights desligadas (0) no momento da decolagem.")
        if flight_data["light_strobe_on"] == 0:
            event_logger.log_event("ALERTA:STROBE_OFF_DECOLAGEM", flight_data, description="Strobe Lights desligadas (0) no momento da decolagem.")


    # --- 2. CAPTURA DE VS E DETECÇÃO DE TOQUE (LANDING) ---
    if (is_airborne_prev and 
        flight_data["on_ground"] == 1 and 
        flight_data["agl"] < 100 and
        not has_landed_prev):

        if flight_data["landing_vs"] == 0.0:
            flight_data["landing_vs"] = flight_data["vs"]
            print(f"--- TOQUE NA PISTA DETECTADO --- VS Capturado: {flight_data['landing_vs']:.2f} fpm")

        if (flight_data["ias"] < 10 and 
            flight_data["g_force"] > 0.8 and 
            flight_data["agl"] < 5):
            
            flight_data["has_landed"] = True
            flight_data["is_airborne"] = False
            print("--- POUSO FINALIZADO (Parada Total) ---")
            event_logger.log_event("POUSO_FINALIZADO", flight_data, 
                                   description=f"Pouso concluído. VS no toque: {flight_data['landing_vs']:.2f} fpm")

            # LOGGING ESPECÍFICO DE LUZES NO POUSO
            if flight_data["light_landing_on"] == 0:
                 event_logger.log_event("ALERTA:LANDING_OFF_POUSO", flight_data, description="Landing Lights desligadas (0) no momento do pouso.")


    # --- 3. RESET MANUAL ---
    if (has_landed_prev and 
        flight_data["on_ground"] == 1 and 
        flight_data["ias"] > 50):
        
        flight_data["is_airborne"] = False
        flight_data["has_landed"] = False
        event_logger.initial_fuel_logged = False # Reset flag para novo voo
        print("--- ESTADO RESETADO: Aguardando Nova Decolagem ---")

    # =========================================================

    # --- Coleta de Alertas (Direto) ---
    alerts = flight_data["alerts"]
    alerts["overspeed_warning"] = get_safe_value("OVERSPEED_WARNING")
    alerts["stall_warning"] = get_safe_value("STALL_WARNING")
    alerts["stall_protection_active"] = get_safe_value("STALL_PROTECTION_ACTIVE")
    alerts["gear_warning_system_active"] = get_safe_value("GEAR_WARNING_SYSTEM_ACTIVE")
    alerts["gpws_warning"] = get_safe_value("GPWS_WARNING")
    alerts["flaps_speed_exceeded"] = get_safe_value("FLAPS_SPEED_EXCEEDED")

    # --- Lógica de Alertas Customizados e NOVOS ALERTA DE LOGGING ---
    
    # 1. Alerta BEACON OFF com Motor LIGADO (NOVO)
    if flight_data["eng_combustion"] == 1 and flight_data["light_beacon_on"] == 0:
        alerts["beacon_off_engine_on"] = 1
        event_logger.log_event("ALERTA:BEACON_OFF_ENGINE_ON", flight_data, description="Beacon Lights desligadas (0) com o motor em funcionamento (1).")
    else:
        alerts["beacon_off_engine_on"] = 0

    # 2. IAS > 250 kts abaixo de 10000 ft (Tolerância 11500) (NOVO)
    alt = flight_data["alt_ind"]
    ias = flight_data["ias"]
    if ias > 250 and alt < 11500:
        alerts["ias_high_below_10k"] = 1
        event_logger.log_event("ALERTA:IAS_OVER_250_BELOW_11500", flight_data, description=f"Velocidade {ias:.0f} kts acima do limite de 250 kts abaixo de 11500 ft (Altitude: {alt:,.0f} ft).")
    else:
        alerts["ias_high_below_10k"] = 0

    # 3. Landing Lights ON acima de 10000 ft (Tolerância 11500) (NOVO)
    if flight_data["light_landing_on"] == 1 and alt > 11500:
        alerts["lights_on_above_10k"] = 1
        event_logger.log_event("ALERTA:LANDING_ON_ABOVE_11500", flight_data, description=f"Landing Lights ligadas (1) acima de 11500 ft (Altitude: {alt:,.0f} ft).")
    else:
        alerts["lights_on_above_10k"] = 0

    # 4. Landing Lights OFF abaixo de 10000 ft (Tolerância 8500) (NOVO)
    if flight_data["light_landing_on"] == 0 and alt < 8500 and is_airborne_prev:
        alerts["lights_off_below_8500"] = 1
        event_logger.log_event("ALERTA:LANDING_OFF_BELOW_8500", flight_data, description=f"Landing Lights desligadas (0) abaixo de 8500 ft (Altitude: {alt:,.0f} ft) durante o voo.")
    else:
        alerts["lights_off_below_8500"] = 0
        
    
    # --- LOGGING de Alertas Simples (Os alertas originais também devem ser logados com cooldown) ---
    
    # Alertas de sistema diretos (Exemplo: Overspeed, Stall)
    alerts_to_log = {
        "overspeed_warning": alerts["overspeed_warning"],
        "stall_warning": alerts["stall_warning"],
        "gpws_warning": alerts["gpws_warning"],
        "flaps_speed_exceeded": alerts["flaps_speed_exceeded"],
        "bank_alert": alerts["bank_alert"],
        "g_force_alert": alerts["g_force_alert"],
    }
    
    for key, is_on in alerts_to_log.items():
        if is_on == 1:
            event_logger.log_event(f"ALERTA:{key.upper()}", flight_data, description=f"Alerta de sistema: {key}.")
    
    # Lógica de Alertas de Motor (ENGINE FIRE / VIBRATION)
    engine_fire_status = []
    vibration_status = []
    
    for i in range(flight_data["engine_count"]):
        fire = get_safe_value(f"GENERAL_ENG_FIRE:{i}")
        if fire:
            engine_fire_status.append(i + 1)
            event_logger.log_event(f"ALERTA:ENG_FIRE_ENG{i+1}", flight_data, description=f"Incêndio detectado no Motor {i+1}.")
            
        vibration = get_safe_value(f"GENERAL_ENG_VIBRATION:{i}")
        if vibration > 1000:
            vibration_status.append(i + 1)
            event_logger.log_event(f"ALERTA:VIBRATION_HIGH_ENG{i+1}", flight_data, description=f"Vibração alta detectada no Motor {i+1} ({vibration:.0f}).")

    alerts["engine_fire"] = engine_fire_status
    alerts["engine_vibration_high"] = vibration_status


# =================================================================
# 3. INTERFACE GRÁFICA (TTKBOOTSTRAP)
# =================================================================

# 1. Lista centralizada de campos de alerta para a nova janela
ALERT_FIELDS = [
    ("STATUS REDE VIRTUAL:", "network_online_status"),
    ("STATUS VOO:", "flight_status"),
    ("VS Pouso (Toque):", "landing_vs"),
    ("OVERSPEED_WARNING", "overspeed_warning"),
    ("STALL_WARNING", "stall_warning"),
    ("STALL_PROTECTION_ACTIVE", "stall_protection_active"),
    ("GEAR_WARNING_SYSTEM_ACTIVE", "gear_warning_system_active"),
    ("GPWS_WARNING", "gpws_warning"),
    ("FLAPS_SPEED_EXCEEDED", "flaps_speed_exceeded"),
    ("PLANE_BANK_DEGREES (> 30º)", "bank_alert"),
    ("G_FORCE (> 1.5G)", "g_force_alert"),
    ("ENG_ON_FIRE:index", "engine_fire_alert"),
    ("ENG_VIBRATION:index (Alta > 1000)", "vibration_alert"),
    ("ALERTA: BEACON OFF c/ motor ligado", "beacon_off_engine_on"),
    ("ALERTA: IAS > 250kts abaixo 11.5k", "ias_high_below_10k"),
    ("ALERTA: Landing ON acima 11.5k", "lights_on_above_10k"),
    ("ALERTA: Landing OFF abaixo 8.5k", "lights_off_below_8500"),
]

# 2. Nova Classe para a Janela de Alertas
class AlertsWindow(ttk.Toplevel):
    def __init__(self, master_app, alert_fields):
        # A nova janela usa o master (MainApplication) do AircraftMonitorApp
        super().__init__(master_app.master)
        self.master_app = master_app
        self.title("Monitor de Alertas de Voo (F12)")
        self.geometry("400x500")
        self.resizable(False, False)
        # Permite fechar a janela com o botão 'X' ou F12
        self.protocol("WM_DELETE_WINDOW", self.hide_window) 
        
        self.alert_labels = {}
        self.alert_fields = alert_fields
        self._is_visible = False
        
        self.create_ui()
        # Inicia a janela escondida
        self.withdraw()
        
    def create_ui(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=BOTH, expand=YES)
        
        ttk.Label(main_frame, text="ALERTAS DO SISTEMA (Completo)", font=("TkDefaultFont", 12, "bold")).pack(pady=(0, 10))
        
        # Frame para os alertas
        alert_frame = ttk.Frame(main_frame)
        alert_frame.pack(fill=X, expand=YES)
        alert_frame.columnconfigure(0, weight=1)
        alert_frame.columnconfigure(1, weight=1)
        
        # Cria os rótulos
        for i, (label_text, key) in enumerate(self.alert_fields):
            ttk.Label(alert_frame, text=label_text, font=("-size 7 -weight bold")).grid(
                row=i, column=0, padx=5, pady=4, sticky="w"
            )
            label = ttk.Label(alert_frame, text="N/A", bootstyle="info", font=("-size 8"))
            label.grid(row=i, column=1, padx=5, pady=4, sticky="e")
            self.alert_labels[key] = label

    def hide_window(self):
        """Esconde a janela."""
        self._is_visible = False
        self.withdraw()
        
    def show_window(self):
        """Exibe a janela."""
        self._is_visible = True
        self.deiconify()
        # Traz a janela para frente
        self.lift()
        
    def is_visible(self):
        return self._is_visible

    def update_alerts(self, flight_data):
        """Atualiza todos os rótulos de alerta na janela pop-up."""
        alerts = flight_data["alerts"]
        
        def update_alert_label(key, is_alert_on, message_on="ALERTA ATIVO", message_off="NORMAL"):
            style = "danger" if is_alert_on else "success"
            text = message_on if is_alert_on else message_off
            if key in self.alert_labels:
                self.alert_labels[key].config(text=text, bootstyle=style)

        # Status da Rede Virtual
        network_status = flight_data.get("network_online", "N/A")
        if network_status == "IVAO":
             status_text = "ONLINE NA IVAO"
             status_style = "success"
        elif network_status == "VATSIM":
             status_text = "ONLINE NA VATSIM"
             status_style = "success"
        elif network_status == "Offline":
             status_text = "OFFLINE NAS REDES"
             status_style = "warning"
        elif network_status == "SIMULADO":
             status_text = "MOCK SIMCONNECT"
             status_style = "info"
        else:
             status_text = network_status 
             status_style = "info"
        self.alert_labels["network_online_status"].config(text=status_text, bootstyle=status_style)
        
        # Status do Voo
        if flight_data["is_airborne"]:
            status_text = "EM VOO"
            status_style = "primary"
        elif flight_data["has_landed"]:
            status_text = "POUSO CONCLUÍDO"
            status_style = "success"
        else:
            status_text = "EM SOLO (Aguardando Decolagem)"
            status_style = "warning"
        self.alert_labels["flight_status"].config(text=status_text, bootstyle=status_style)
        
        # VS de Pouso
        vs_pouso_text = f"{flight_data['landing_vs']:,.0f} fpm" if flight_data["landing_vs"] != 0.0 else "N/A"
        vs_pouso_style = "success" if -500 <= flight_data['landing_vs'] <= 0 else "danger"
        self.alert_labels["landing_vs"].config(text=vs_pouso_text, bootstyle=vs_pouso_style)


        # Alertas Simples
        update_alert_label("overspeed_warning", alerts["overspeed_warning"])
        update_alert_label("stall_warning", alerts["stall_warning"])
        update_alert_label("stall_protection_active", alerts["stall_protection_active"])
        update_alert_label("gear_warning_system_active", alerts["gear_warning_system_active"])
        update_alert_label("gpws_warning", alerts["gpws_warning"])
        update_alert_label("flaps_speed_exceeded", alerts["flaps_speed_exceeded"])

        # Alertas Customizados (Com valor na UI, se necessário)
        # Note: Usamos flight_data diretamente para os valores que não são apenas 0/1, obtendo o valor atual do SimVar
        bank_degrees = get_safe_value('PLANE_BANK_DEGREES')
        bank_msg = f"BANK {abs(bank_degrees):.0f}º"
        update_alert_label("bank_alert", alerts["bank_alert"], message_on=bank_msg)
        
        g_force_msg = f"G: {flight_data['g_force']:.2f}G"
        update_alert_label("g_force_alert", alerts["g_force_alert"], message_on=g_force_msg)

        # Alertas Indexados (Motores)
        fire_alert_on = len(alerts["engine_fire"]) > 0
        fire_msg = f"FOGO ENG: {', '.join(map(str, alerts['engine_fire']))}" if fire_alert_on else "NORMAL"
        update_alert_label("engine_fire_alert", fire_alert_on, message_on=fire_msg)

        vib_alert_on = len(alerts["engine_vibration_high"]) > 0
        vib_msg = f"VIBRAÇÃO ENG: {', '.join(map(str, alerts['engine_vibration_high']))}" if vib_alert_on else "NORMAL"
        update_alert_label("vibration_alert", vib_alert_on, message_on=vib_msg)
        
        # NOVOS ALERTAS Customizados
        update_alert_label("beacon_off_engine_on", alerts["beacon_off_engine_on"])
        update_alert_label("ias_high_below_10k", alerts["ias_high_below_10k"], message_on=f"IAS {flight_data['ias']:.0f}kts @ {flight_data['alt_ind']:,.0f}ft")
        update_alert_label("lights_on_above_10k", alerts["lights_on_above_10k"], message_on=f"Landing ON @ {flight_data['alt_ind']:,.0f}ft")
        update_alert_label("lights_off_below_8500", alerts["lights_off_below_8500"], message_on=f"Landing OFF @ {flight_data['alt_ind']:,.0f}ft")


class AircraftMonitorApp:
    
    def _load_icons(self):
        """Carrega e redimensiona os ícones de avião."""
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        
        GREEN_ICON_PATH = os.path.join(base_dir, "assets", "icons", "plane_green.png")
        RED_ICON_PATH = os.path.join(base_dir, "assets", "icons", "plane_red.png")
        
        icon_size = (20, 20)
        
        try:
            green_img = Image.open(GREEN_ICON_PATH).resize(icon_size, Image.LANCZOS)
            self.icon_green = ImageTk.PhotoImage(green_img)
            
            red_img = Image.open(RED_ICON_PATH).resize(icon_size, Image.LANCZOS)
            self.icon_red = ImageTk.PhotoImage(red_img)
        except Exception as e:
            print(f"AVISO CRÍTICO: Falha ao carregar ícones de status (plane_X.png): {e}")
            self.icon_green = None
            self.icon_red = None
            
    # MODIFICAÇÃO: Aceita va_key e email no __init__
    def __init__(self, master, va_key, email):
        self.master = master
        self.va_key = va_key # Armazena a chave da VA
        self.email = email   # Armazena o e-mail do piloto
        # Configurações de janela (ajustadas para não sobrescrever o MainApplication)
        master.resizable(False, False) 
        
        # --- NOVO: Variável para armazenar o ID do after ---
        self.after_id = None

        self._load_icons()

        self.running = True
        self.setup_ui()
        self._set_connection_indicator() 
        
        # NOVO: Instancia a janela de alertas
        self.alerts_window = AlertsWindow(self, ALERT_FIELDS) 
        
        # NOVO: Checagem de status da Rede Virtual no início
        self.check_network_status()

        self.data_thread = threading.Thread(target=self.polling_loop, daemon=True)
        self.data_thread.start()

        # Armazena o ID da primeira chamada after
        self.after_id = self.master.after(500, self.update_ui)

    # NOVO: Função para alternar a visibilidade da janela de alertas
    def toggle_alerts_window(self):
        """Alterna a visibilidade da janela de alertas."""
        if self.alerts_window.is_visible():
            self.alerts_window.hide_window()
        else:
            self.alerts_window.show_window()

    # NOVO: Função para checar o status online IVAO/VATSIM
    def check_network_status(self):
        """Busca o status online do piloto nas redes virtuais (IVAO/VATSIM)."""
        global flight_data
        
        # Importamos aqui para evitar circular dependency ao importar va_auth no topo
        import va_auth 
        
        if CONN_STATUS == "SIMULADO":
             flight_data["network_online"] = "SIMULADO"
        else:
             # Chama a função que criamos em va_auth.py
             is_online, network = va_auth.is_pilot_online_ivao_vatsim(self.va_key, self.email)
             flight_data["network_online"] = network # Armazena 'IVAO', 'VATSIM', 'Offline' ou 'Piloto não validado na VA'
        
    def _on_logoff(self):
        """
        Função chamada ao clicar em Logoff.
        Limpa as credenciais salvas e retorna para a tela inicial.
        """
        # 1. Deleta as credenciais salvas
        current_va = self.master.va_key_selected
        current_email = self.master.pilot_email
        gui_elements.delete_credentials(current_va, current_email)
        
        # 2. Fecha a conexão SimConnect e a thread, e CANCELA o loop de UI
        self.on_closing(is_logoff=True) 
        
        # 3. DESTROI O FRAME DA UI DO MONITOR
        if hasattr(self, 'main_frame') and self.main_frame.winfo_exists():
            self.main_frame.destroy() 
        
        # 4. Retorna à tela de seleção de VA (no MainApplication)
        self.master._show_va_selection() 
        
    def _set_connection_indicator(self):
        # ... (código completo)
        """Define o ícone e o texto de status da conexão."""
        if CONN_STATUS == "REAL":
            indicator_text = "(Dados Reais)"
            icon = self.icon_green
            color = "success"
        else:
            indicator_text = "(Dados Simulados)"
            icon = self.icon_red
            color = "danger"
        
        self.connection_status_label.config(
            text=indicator_text, 
            image=icon if icon else '', 
            compound='left', 
            bootstyle=color
        )
        
        self.connection_status_label.image = icon 

    def setup_ui(self):
        # --- MUDANÇA AQUI: Armazena o frame principal como self.main_frame ---
        self.main_frame = ttk.Frame(self.master, padding=15)
        self.main_frame.pack(fill=BOTH, expand=YES)
        
        # --- NOVO: Botão de Logoff (Discreto) ---
        ttk.Button(
            self.main_frame, # Usa self.main_frame como master
            text="Logoff",
            command=self._on_logoff,
            bootstyle="link-danger", # Estilo discreto e em vermelho
            cursor="hand2"
        ).place(relx=1.0, rely=0, x=-15, y=5, anchor=NE)
        
        # FONTE DIMINUÍDA (16 -> 11)
        ttk.Label(self.main_frame, text="Monitor de Voo Detalhado", font=("TkDefaultFont", 11, "bold")).pack(pady=(0, 15))
        
        # RÓTULO DE INSTRUÇÃO F12
        ttk.Label(self.main_frame, text="Pressione F12 para Alertas Detalhados", font=("-size 8"), bootstyle="info").pack(pady=(0, 5))


        # --- INDICADOR DE STATUS (COM IMAGEM) ---
        # FONTE DIMINUÍDA (11 -> 8)
        self.connection_status_label = ttk.Label(
            self.main_frame, 
            text="Iniciando...", 
            font=("-size 8 -weight bold"),
            bootstyle="info"
        )
        self.connection_status_label.pack(pady=5) 
        
        # --- Seção 1: Dados de Voo ---
        data_frame = ttk.Labelframe(self.main_frame, text="Dados Primários", padding=10)
        data_frame.pack(fill=X, pady=10)
        data_frame.columnconfigure(0, weight=1)
        data_frame.columnconfigure(1, weight=1)

        self.data_labels = {}
        data_fields = [
            ("Altitude Indicada:", "alt_ind", "0 ft"),
            ("Altitude AGL:", "agl", "0 ft"),
            ("VS (Velocidade Vertical):", "vs", "0 fpm"),
            ("IAS:", "ias", "0 kt"),
            ("TAS:", "tas", "0 kt"),
            ("G-Force:", "g_force", "1.00 G"),
            ("Combustível Total:", "total_fuel", "0 gal"), 
            ("Posição Trem Esquerdo:", "gear_left_pos", "0 %"),
            ("No Solo (SIM_ON_GROUND):", "on_ground", "N/A"),
            # NOVOS CAMPOS ADICIONADOS
            ("Motor LIGADO (1):", "eng_combustion", "0"),
            ("Luzes Beacon ON:", "light_beacon_on", "0"),
            ("Luzes Landing ON:", "light_landing_on", "0"),
            ("Luzes Strobe ON:", "light_strobe_on", "0"),
        ]

        for i, (label_text, key, default_text) in enumerate(data_fields):
            # FONTE DIMINUÍDA (10 -> 7)
            ttk.Label(data_frame, text=label_text, font=("-size 7 -weight bold")).grid(
                row=i, column=0, padx=5, pady=4, sticky="w"
            )
            # FONTE DIMINUÍDA (11 -> 8)
            label = ttk.Label(data_frame, text=default_text, font=("-size 8"), anchor="e")
            label.grid(row=i, column=1, padx=5, pady=4, sticky="e")
            self.data_labels[key] = label
        
        # REMOVIDA A SEÇÃO DE ALERTA DO FORMULÁRIO PRINCIPAL
        # A nova janela pop-up AlertsWindow cuidará disso.

    def update_ui(self):
        """Atualiza a interface com os dados mais recentes."""
        
        # Verifica se o frame principal ainda existe antes de tentar atualizar os widgets
        if not self.running or not self.main_frame.winfo_exists():
            return

        # --- 1. Atualiza Dados de Voo ---
        self.data_labels["alt_ind"].config(text=f"{flight_data['alt_ind']:,.0f} ft")
        self.data_labels["agl"].config(text=f"{flight_data['agl']:,.0f} ft")
        self.data_labels["vs"].config(text=f"{flight_data['vs']:,.0f} fpm")
        self.data_labels["ias"].config(text=f"{flight_data['ias']:.0f} kt")
        self.data_labels["tas"].config(text=f"{flight_data['tas']:.0f} kt")
        self.data_labels["g_force"].config(text=f"{flight_data['g_force']:.2f} G")
        self.data_labels["total_fuel"].config(text=f"{flight_data['total_fuel']:,.0f} gal")
        self.data_labels["gear_left_pos"].config(text=f"{flight_data['gear_left_pos']:.0f} %")
        
        on_ground_text = "TRUE" if flight_data["on_ground"] == 1 else "FALSE"
        self.data_labels["on_ground"].config(text=on_ground_text, bootstyle="warning" if on_ground_text == "TRUE" else "primary")

        # NOVOS DADOS NA UI
        self.data_labels["eng_combustion"].config(text=str(flight_data["eng_combustion"]), bootstyle="success" if flight_data["eng_combustion"] == 1 else "danger")
        self.data_labels["light_beacon_on"].config(text=str(flight_data["light_beacon_on"]), bootstyle="success" if flight_data["light_beacon_on"] == 1 else "danger")
        self.data_labels["light_landing_on"].config(text=str(flight_data["light_landing_on"]), bootstyle="success" if flight_data["light_landing_on"] == 1 else "danger")
        self.data_labels["light_strobe_on"].config(text=str(flight_data["light_strobe_on"]), bootstyle="success" if flight_data["light_strobe_on"] == 1 else "danger")

        # --- 2. Atualiza Alertas na Janela POP-UP ---
        if self.alerts_window.is_visible():
             self.alerts_window.update_alerts(flight_data)

        if self.running:
             # Armazena o ID da chamada para que possa ser cancelada
             self.after_id = self.master.after(500, self.update_ui)

    def polling_loop(self):
        """Loop de busca de dados rodando em segundo plano (a cada 0.1s)."""
        while self.running:
            fetch_all_data()
            time.sleep(0.1)

    def on_closing(self, is_logoff=False):
        """
        Encerra threads, fecha a conexão SimConnect e cancela o agendamento de UI.
        """
        global sm
        self.running = False
        
        # --- NOVO: Garante que a janela pop-up seja destruída ---
        if hasattr(self, 'alerts_window'):
             self.alerts_window.destroy()
             
        if self.after_id:
             try:
                 self.master.after_cancel(self.after_id)
             except Exception:
                 pass
        
        # Encerra a conexão SimConnect
        if sm and not isinstance(sm, MockSimConnect): 
            try:
                sm.exit()
            except:
                pass
        
        if not is_logoff:
             self.master.destroy()