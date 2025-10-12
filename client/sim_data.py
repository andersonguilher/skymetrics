# Arquivo: client/sim_data.py

import time
import random
from datetime import datetime
from typing import Any, Dict, Tuple

# --- CONSTANTES E ESTADO GLOBAL ---
CONN_STATUS = "REAL" 
sm = None 
aq = None 

DATA_PRECISION = { 
    "alt_ind": 0, "vs": 0, "ias": 1, "gs": 1, "tas": 1, "agl": 0, "on_ground": 0, 
    "total_fuel": 0, "gear_left_pos": 0, "g_force": 1, 
    "engine_count": 0, "lat": 3, "lng": 3, "eng_combustion": 0, 
    "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, 
    "plane_bank_degrees": 0, "engine_vibration_1": 0,
    "com1_active": 3, # Frequência COM1 ativa (MHz)
    "com2_active": 3, # Frequência COM2 ativa (MHz)
}

flight_data: Dict[str, Any] = {
    "alt_ind": 0, "vs": 0.0, "ias": 0, "gs": 0.0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, 
    "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0, "lat": 0.0, "lng": 0.0, 
    "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, 
    "plane_bank_degrees": 0.0, "engine_vibration_1": 0.0,
    "com1_active": 0.0, 
    "com2_active": 0.0, 
    "pilot_name": "N/A", "vatsim_id": "", "ivao_id": "", 
    "alerts": {"overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, 
               "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0,},
    "client_disconnect": 0, 
}

# --- FUNÇÃO DE DECODIFICAÇÃO BCD16 ---
def decode_com_frequency(raw_value: int | float) -> float:
    """
    Decodifica o valor da frequência COM.
    Assume que se o valor for menor que 1000 (o que é o caso para 122.8),
    ele já está em MHz e deve ser retornado diretamente.
    """
    if raw_value == 0:
        return 0.0
    
    # CORREÇÃO: Se o valor lido é um float pequeno (e.g., 122.8), ele já está em MHz.
    if raw_value < 1000: 
        return float(raw_value)
        
    # Se o valor for grande (e.g., 122800000), ele é em Hertz e precisa de conversão para MHz.
    return raw_value / 1000000.0


# --- MOCKUP / SIMCONNECT SETUP ---
class MockSimConnect:
    def exit(self): pass

class MockAircraftRequests:
    def __init__(self, sm=None): self._start_time = time.time()
    def get(self, var: str) -> Any:
        # Lógica de Mock de dados aqui (simplificada)
        t = time.time() - self._start_time
        if var == "VERTICAL_SPEED": return 1000 if t % 60 > 10 and t % 60 < 50 else 0
        if var == "PLANE_LATITUDE": return -23.5505 + (t % 3600) / 1000000 
        if var == "PLANE_LONGITUDE": return -46.6333
        if var == "PLANE_ALTITUDE": return 10000 if t > 10 else 0
        if var == "AIRSPEED_INDICATED": return 215 if t > 10 else 0
        if var == "GPS_GROUND_SPEED": return 250 if t > 10 else (12 if t > 5 and t < 15 and t % 60 > 50 else 0) 
        if var == "SIM_ON_GROUND": return 1 if t < 20 or t % 60 > 50 else 0
        if var == "GENERAL_ENG_COMBUSTION:1": return 1 if t > 5 else 0
        if var == "G_FORCE": return 1.0 + 0.1 * random.random()
        # Mock corrigido para o valor float que o usuário viu no log (snake_case)
        if var == "COM_ACTIVE_FREQUENCY:1": return 122.8 
        # Mock corrigido para o valor com espaços (se for o caso)
        if var == "COM_ACTIVE_FREQUENCY:2": return 118.5 
        # --- Variáveis Originais Omitidas ---
        if var == "AIRSPEED_TRUE": return 230 if t > 10 else 0
        if var == "PLANE_ALT_ABOVE_GROUND": return 9950 if t > 10 else 0
        if var == "FUEL_TOTAL_QUANTITY": return 3000 if t > 10 else 0
        if var == "GEAR_HANDLE_POSITION": return 1 if t < 20 or t % 60 > 50 else 0
        if var == "NUMBER_OF_ENGINES": return 2
        if var == "PLANE_BANK_DEGREES": return 5.0
        if var == "GENERAL_ENG_VIBRATION:1": return 0.05 if t > 10 else 0.0
        if var == "LIGHT_BEACON_ON": return 1 if t > 5 else 0
        if var == "LIGHT_LANDING_ON": return 1
        if var == "LIGHT_STROBE_ON": return 1
        if var == "OVERSPEED_WARNING": return 0
        if var == "STALL_WARNING": return 0
        if var == "GENERAL_ENG_FIRE:1": return 0
        if var == "STALL_PROTECTION_ACTIVE": return 0
        if var == "GPWS_WARNING": return 0
        if var == "FLAPS_SPEED_EXCEEDED": return 0
        if var == "GEAR_WARNING_SYSTEM_ACTIVE": return 0
        return 0

# --- INICIALIZAÇÃO REAL/MOCK ---
try:
    from SimConnect import SimConnect, AircraftRequests
    sm = SimConnect(); aq = AircraftRequests(sm); CONN_STATUS = "REAL" 
except Exception as e:
    sm = MockSimConnect(); aq = MockAircraftRequests(sm); CONN_STATUS = "SIMULADO" 

def get_safe_value(var_name: str, default: Any = 0) -> Any:
    """Busca um valor do SimConnect/Mock, levantando exceção se a conexão real falhar."""
    global aq, CONN_STATUS
    try:
        value = aq.get(var_name)
        return value if value is not None else default
    except Exception as e: 
        if CONN_STATUS == "REAL": raise e
        return default

def fetch_all_data():
    """Busca dados COMPLETOS do simulador e atualiza o dicionário global `flight_data`."""
    global flight_data
    
    # 1. Coleta de VS e Coerção de Zero 
    flight_data["vs"] = get_safe_value("VERTICAL_SPEED")
    if abs(flight_data["vs"]) < 0.5: flight_data["vs"] = 0.0 
         
    # Coleta de Lat/Lng (Garantido) 
    flight_data["lat"] = get_safe_value("PLANE_LATITUDE", default=0.0)
    flight_data["lng"] = get_safe_value("PLANE_LONGITUDE", default=0.0)
    
    # Coleta de Dados Primários 
    flight_data["alt_ind"] = get_safe_value("PLANE_ALTITUDE")
    flight_data["ias"] = get_safe_value("AIRSPEED_INDICATED")
    flight_data["gs"] = get_safe_value("GPS_GROUND_SPEED", default=0.0) 
    flight_data["tas"] = get_safe_value("AIRSPEED_TRUE")
    flight_data["agl"] = get_safe_value("PLANE_ALT_ABOVE_GROUND")
    flight_data["on_ground"] = get_safe_value("SIM_ON_GROUND")
    flight_data["g_force"] = get_safe_value("G_FORCE")
    flight_data["total_fuel"] = get_safe_value("FUEL_TOTAL_QUANTITY")
    flight_data["gear_left_pos"] = round(get_safe_value("GEAR_HANDLE_POSITION") * 100, 0)
    flight_data["engine_count"] = int(get_safe_value("NUMBER_OF_ENGINES", default=0))
    flight_data["plane_bank_degrees"] = get_safe_value("PLANE_BANK_DEGREES", default=0.0)
    flight_data["engine_vibration_1"] = get_safe_value("GENERAL_ENG_VIBRATION:1", default=0.0)

    # Coleta das frequências COM ativas:
    # O log de diagnóstico mostrou que raw_com1 (snake_case) retorna o valor correto em float (122.8)
    raw_com1 = get_safe_value("COM_ACTIVE_FREQUENCY:1", default=0)
    raw_com2 = get_safe_value("COM_ACTIVE_FREQUENCY:2", default=0) # Mantém este com espaços para consistência de SimVar

    flight_data["com1_active"] = decode_com_frequency(raw_com1)
    flight_data["com2_active"] = decode_com_frequency(raw_com2)
    
    # Coleta de Status e Luzes e Lógica de Alertas (Original)
    flight_data["eng_combustion"] = get_safe_value("GENERAL_ENG_COMBUSTION:1", default=0)
    flight_data["light_beacon_on"] = get_safe_value("LIGHT_BEACON_ON", default=0)
    flight_data["light_landing_on"] = get_safe_value("LIGHT_LANDING_ON", default=0)
    flight_data["light_strobe_on"] = get_safe_value("LIGHT_STROBE_ON", default=0)

    # Coleta de Alertas (Original)
    flight_data["alerts"]["overspeed_warning"] = get_safe_value("OVERSPEED_WARNING", default=0)
    flight_data["alerts"]["stall_warning"] = get_safe_value("STALL_WARNING", default=0)
    # Lógica customizada para Beacon/Engine
    flight_data["alerts"]["beacon_off_engine_on"] = (get_safe_value("LIGHT_BEACON_ON", default=1) == 0 and get_safe_value("GENERAL_ENG_COMBUSTION:1", default=0) == 1)
    # As chaves de alerta restantes precisam ser resolvidas para a API SimConnect
    flight_data["alerts"]["engine_fire"] = get_safe_value("GENERAL_ENG_FIRE:1", default=0)
    flight_data["alerts"]["stall_protection_active"] = get_safe_value("STALL_PROTECTION_ACTIVE", default=0)
    flight_data["alerts"]["gpws_warning"] = get_safe_value("GPWS_WARNING", default=0)
    flight_data["alerts"]["flaps_speed_exceeded"] = get_safe_value("FLAPS_SPEED_EXCEEDED", default=0)
    flight_data["alerts"]["gear_warning_system_active"] = get_safe_value("GEAR_WARNING_SYSTEM_ACTIVE", default=0)
    
def create_rounded_data(source_data: Dict[str, Any]) -> Dict[str, Any]:
    """Cria um novo dicionário com as métricas arredondadas para a precisão definida."""
    rounded = source_data.copy()
    for key, precision in DATA_PRECISION.items():
        if key in rounded and isinstance(rounded[key], (float, int)):
            rounded[key] = round(rounded[key], precision)
    return rounded

def has_significant_change(current_data: Dict[str, Any], last_data: Dict[str, Any] | None) -> bool:
    """Verifica se há alterações significativas nos dados arredondados."""
    if last_data is None: return True
    return current_data != last_data