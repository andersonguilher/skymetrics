# sim_data_monitor.py (Conteúdo do seu antigo main.py, com a inicialização removida)
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import time
import sys
import random
import os
from PIL import Image, ImageTk

# =================================================================
# 1. SIMCONNECT MOCKUP E INICIALIZAÇÃO 
# =================================================================

# FIX: MOVIDAS AS DEFINIÇÕES PARA O ESCOPO GLOBAL
class MockSimConnect:
    def exit(self): pass

class MockAircraftRequests:
    """Simula a obtenção de dados e a lógica de alerta do SimConnect."""
    # ... (código completo da classe MockAircraftRequests)
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
            return 10000 + 5000 * random.uniform(-0.1, 0.1)
        
        if var == "AIRSPEED_INDICATED":
            return 215 + 65 * random.uniform(-0.05, 0.05)
            
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
    
    "is_airborne": False,
    "has_landed": False,
    "landing_vs": 0.0,
    
    "alerts": {
        "overspeed_warning": 0, "stall_warning": 0, "stall_protection_active": 0,
        "gear_warning_system_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0,
        "bank_alert": 0, "g_force_alert": 0,
        "engine_fire": [], "engine_vibration_high": []
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
    """Busca dados e aplica a lógica de alerta."""
    global flight_data
    
    # --- Coleta de Dados Primários ---
    # ... (código completo de coleta e lógica de decolagem/pouso e alertas)
    # ...
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


    # =========================================================
    # LÓGICA DE DECOLAGEM E POUSO
    # =========================================================

    # --- 1. DETECÇÃO DE DECOLAGEM ---
    if (not flight_data["is_airborne"] and 
        flight_data["agl"] > 50 and 
        flight_data["ias"] > 40):
        
        flight_data["is_airborne"] = True
        flight_data["has_landed"] = False
        print("--- DECOLAGEM DETECTADA ---")

    # --- 2. CAPTURA DE VS E DETECÇÃO DE TOQUE (LANDING) ---
    if (flight_data["is_airborne"] and 
        flight_data["on_ground"] == 1 and 
        flight_data["agl"] < 100 and
        not flight_data["has_landed"]):

        if flight_data["landing_vs"] == 0.0:
            flight_data["landing_vs"] = flight_data["vs"]
            print(f"--- TOQUE NA PISTA DETECTADO --- VS Capturado: {flight_data['landing_vs']:.2f} fpm")

        if (flight_data["ias"] < 10 and 
            flight_data["g_force"] > 0.8 and 
            flight_data["agl"] < 5):
            
            flight_data["has_landed"] = True
            flight_data["is_airborne"] = False
            print("--- POUSO FINALIZADO (Parada Total) ---")
    
    # --- 3. RESET MANUAL (Para testar decolagem novamente) ---
    if (flight_data["has_landed"] and 
        flight_data["on_ground"] == 1 and 
        flight_data["ias"] > 50):
        
        flight_data["is_airborne"] = False
        flight_data["has_landed"] = False
        flight_data["landing_vs"] = 0.0
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

    # --- Lógica de Alertas Customizados ---
    
    bank_degrees = get_safe_value("PLANE_BANK_DEGREES")
    alerts["bank_alert"] = 1 if abs(bank_degrees) > 30 else 0
    
    alerts["g_force_alert"] = 1 if abs(flight_data["g_force"]) > 1.5 else 0

    # --- Alertas Por Motor (Indexados) ---
    engine_fire_status = []
    vibration_status = []
    
    for i in range(flight_data["engine_count"]):
        fire = get_safe_value(f"GENERAL_ENG_FIRE:{i}")
        if fire:
            engine_fire_status.append(i + 1)
            
        vibration = get_safe_value(f"GENERAL_ENG_VIBRATION:{i}")
        if vibration > 1000:
            vibration_status.append(i + 1)

    alerts["engine_fire"] = engine_fire_status
    alerts["engine_vibration_high"] = vibration_status


# =================================================================
# 3. INTERFACE GRÁFICA (TTKBOOTSTRAP)
# =================================================================

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
            
    def __init__(self, master):
        self.master = master
        # Configurações de janela (ajustadas para não sobrescrever o MainApplication)
        # Removido o .title e .geometry daqui, agora é feito em va_monitor.py
        master.resizable(False, False) 

        self._load_icons()

        self.running = True
        self.setup_ui()
        self._set_connection_indicator() 
        
        self.data_thread = threading.Thread(target=self.polling_loop, daemon=True)
        self.data_thread.start()

        self.master.after(500, self.update_ui)
        
        # self.master.protocol("WM_DELETE_WINDOW", self.on_closing) # Mantido para gerenciar o SimConnect

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
        # ... (código completo de setup_ui)
        main_frame = ttk.Frame(self.master, padding=15)
        main_frame.pack(fill=BOTH, expand=YES)
        
        # FONTE DIMINUÍDA (16 -> 11)
        ttk.Label(main_frame, text="Monitor de Voo Detalhado", font=("TkDefaultFont", 11, "bold")).pack(pady=(0, 15))

        # --- INDICADOR DE STATUS (COM IMAGEM) ---
        # FONTE DIMINUÍDA (11 -> 8)
        self.connection_status_label = ttk.Label(
            main_frame, 
            text="Iniciando...", 
            font=("-size 8 -weight bold"),
            bootstyle="info"
        )
        self.connection_status_label.pack(pady=5) 
        
        # --- Seção 1: Dados de Voo ---
        data_frame = ttk.Labelframe(main_frame, text="Dados Primários", padding=10)
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
        
        # --- Seção 2: Alertas e Warnings ---
        alert_frame = ttk.Labelframe(main_frame, text="Alertas do Sistema", padding=10)
        alert_frame.pack(fill=X, pady=10)
        
        self.alert_labels = {}
        alert_fields = [
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
        ]
        
        for i, (label_text, key) in enumerate(alert_fields):
            # FONTE DIMINUÍDA (10 -> 7)
            ttk.Label(alert_frame, text=label_text, font=("-size 7 -weight bold")).grid(
                row=i, column=0, padx=5, pady=4, sticky="w"
            )
            # FONTE DIMINUÍDA (11 -> 8)
            label = ttk.Label(alert_frame, text="NORMAL", bootstyle="success", font=("-size 8"))
            label.grid(row=i, column=1, padx=5, pady=4, sticky="e")
            self.alert_labels[key] = label

    def update_ui(self):
        # ... (código completo de update_ui)
        """Atualiza a interface com os dados mais recentes."""
        
        def update_alert_label(key, is_alert_on, message_on="ALERTA ATIVO", message_off="NORMAL"):
            style = "danger" if is_alert_on else "success"
            text = message_on if is_alert_on else message_off
            self.alert_labels[key].config(text=text, bootstyle=style)

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

        # --- 2. Atualiza Alertas e Status de Voo ---
        alerts = flight_data["alerts"]
        
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
        bank_msg = f"BANK {abs(get_safe_value('PLANE_BANK_DEGREES')):.0f}º"
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


        if self.running:
            self.master.after(500, self.update_ui)

    def polling_loop(self):
        """Loop de busca de dados rodando em segundo plano (a cada 0.1s)."""
        while self.running:
            fetch_all_data()
            time.sleep(0.1)

    def on_closing(self):
        """Encerra threads e fecha a conexão SimConnect. (Chamado pelo va_monitor)"""
        global sm
        self.running = False
        
        if sm and not isinstance(sm, MockSimConnect): 
            try:
                sm.exit()
            except:
                pass
        
        self.master.destroy()

# REMOVIDO: def initialize_app()
# REMOVIDO: if __name__ == "__main__":