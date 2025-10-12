# Arquivo: client/event_logic.py

import requests
import json
import time
from typing import Dict, Any, List
from datetime import datetime
from threading import Lock

# --- CONSTANTES DE LÓGICA DE VOO ---
IAS_TAXI_START_KTS = 10        
ALERT_RATE_LIMIT_SECONDS = 60  
SUBMIT_LOG_URL = "https://kafly.com.br/dash/utils/submit_flight_log.php"

def format_number(value, decimals):
    """Formata um número para string com separador de milhares para logs."""
    if value is None: return "N/A"
    try:
        return f"{value:,.{decimals}f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return str(value)

class FlightEventLogger:
    def __init__(self, pilot_name: str, pilot_data: Dict[str, Any]):
        self.pilot_name = pilot_name
        self.log_user_id = pilot_data.get('vatsim_id') if pilot_data.get('vatsim_id') not in ('', 'N/A') else pilot_data.get('ivao_id', 'N/A')
        self.log_lock = Lock()
        
        self.is_airborne = False
        self.has_landed = True
        self.initial_fuel_logged = False
        self.landing_vs = None
        self.last_vs = 0.0
        self.flight_ended = True 
        self.event_log: List[Dict[str, Any]] = []
        self.last_alert_timestamps: Dict[str, float] = {}

        self.departure_id = pilot_data.get('departureId', pilot_name[:4]).upper()
        self.arrival_id = pilot_data.get('arrivalId', pilot_name[-4:]).upper()

        self._log_event("INICIO_SESSAO", f"Sessão de telemetria iniciada. DEP: {self.departure_id}, ARR: {self.arrival_id}. (Usando ID de Rede {self.log_user_id})", {})
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [LOGIC] Logger inicializado para {self.pilot_name}.")


    def _log_event(self, event_name: str, description: str, snapshot: Dict[str, Any]):
        """Armazena o evento localmente no buffer."""
        with self.log_lock:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [EVENTO] {self.pilot_name}: {event_name} -> {description}")

            lat_string = str(snapshot.get('lat', 0.0))
            lng_string = str(snapshot.get('lng', 0.0))

            log_entry = {
                "userId": self.log_user_id,
                "departureId": self.departure_id,
                "arrivalId": self.arrival_id,
                "data_hora": datetime.now().isoformat(),
                "evento": event_name,
                "lat": lat_string,
                "lng": lng_string,
                "descricao": description,
            }

            safe_total_fuel = snapshot.get('total_fuel', 0.0)

            if event_name == 'VS_NO_TOQUE':
                vs_value = snapshot.get('landing_vs', 0.0) 
                log_entry['landing_vs'] = int(vs_value)
                
            elif event_name in ('COMBUSTIVEL_INICIAL', 'COMBUSTIVEL_FINAL'):
                log_entry['total_fuel'] = int(safe_total_fuel) 
                
            self.event_log.append(log_entry)

    def _should_log_alert(self, alert_name: str) -> bool:
        """Controla o rate limiting para alertas."""
        current_time = time.time()
        if current_time - self.last_alert_timestamps.get(alert_name, 0.0) >= ALERT_RATE_LIMIT_SECONDS:
            self.last_alert_timestamps[alert_name] = current_time
            return True
        return False

    def check_and_log_events(self, data: Dict[str, Any]):
        """Executa a detecção e o registro de todos os eventos de voo."""
        current_agl = data.get('agl', 0); current_ias = data.get('ias', 0)
        current_vs = data.get('vs', 0); current_on_ground = data.get('on_ground', 0)
        current_bank = data.get('plane_bank_degrees', 0); eng_combustion = data.get('eng_combustion', 0)
        alerts = data.get('alerts', {})

        # A. INÍCIO DO VOO
        if self.has_landed and not self.is_airborne and not self.initial_fuel_logged and eng_combustion == 1 and current_on_ground == 1 and current_ias >= IAS_TAXI_START_KTS:
            self._log_event("INICIO_VOO", f"Início de taxi detectado.", data)
            self._log_event("COMBUSTIVEL_INICIAL", f"Motor ligado. Combustível: {format_number(data.get('total_fuel', 0), 0)} gal", data)
            self.initial_fuel_logged = True; self.has_landed = False; self.flight_ended = False

        # B. DECOLAGEM
        if not self.is_airborne and self.initial_fuel_logged and current_agl > 50 and current_ias > 40:
            self.is_airborne = True; self.has_landed = False; self.flight_ended = False
            self._log_event("DECOLAGEM", "Decolagem detectada. Aeronave no ar.", data)

        # C. POUSO
        if self.is_airborne and current_on_ground == 1 and current_agl < 100 and not self.has_landed:
            if self.landing_vs is None: data['landing_vs'] = self.last_vs; self.landing_vs = self.last_vs
            if current_ias < 10:
                self.has_landed = True; self.is_airborne = False
                vs_no_toque = self.landing_vs if self.landing_vs is not None else current_vs
                data['landing_vs'] = vs_no_toque 
                self._log_event("VS_NO_TOQUE", f"Velocidade vertical no toque detectada: {vs_no_toque:.0f} fpm.", data)
                self._log_event("POUSO_FINALIZADO", f"Pouso concluído. VS no toque final: {vs_no_toque:.0f} fpm", data)

        # D. ALERTA: BANK ANGLE (> 30°)
        if abs(current_bank) > 30 and self._should_log_alert("ALERTA:BANK_ANGLE_HIGH"):
            self._log_event("ALERTA:BANK_ANGLE_HIGH", f"Ângulo de inclinação excessivo: {abs(current_bank):.1f} graus.", data)

        # E. OUTROS ALERTAS
        if alerts.get('stall_warning', 0) == 1 and self._should_log_alert("ALERTA:STALL_WARNING"):
            self._log_event("ALERTA:STALL_WARNING", "Alerta de estol (stall warning) ativo.", data)
        # ... (Outros alertas)

        # G. VOO FINALIZADO
        if self.initial_fuel_logged and self.has_landed and not self.flight_ended and eng_combustion == 0:
            self.flight_ended = True
            self._log_event("COMBUSTIVEL_FINAL", f"Motor desligado. Combustível final: {format_number(data.get('total_fuel', 0), 0)} gal", data)
            self._log_event("VOO_FINALIZADO", "Fim da sessão de voo. Log de voo será enviado.", data)
            self.post_full_flight_log() 
            self.is_airborne = False; self.has_landed = True; self.initial_fuel_logged = False
            self.landing_vs = None; self.last_alert_timestamps = {}
            
        # H. POUSO RESET (Touch-and-Go)
        if self.initial_fuel_logged and self.has_landed and current_on_ground == 1 and current_ias >= IAS_TAXI_START_KTS:
            if self.event_log:
                self._log_event("SEGMENTO_CONCLUIDO", "Segmento de voo anterior concluído (Touch-and-Go ou re-takeoff). Enviando logs acumulados.", data)
                self.post_full_flight_log() 

            self.is_airborne = False; self.has_landed = False; self.initial_fuel_logged = False
            self.landing_vs = None; self.flight_ended = False
            self._log_event("RESET_VOO", "Voando novamente ou táxi rápido após pouso. Reiniciando estado de voo.", data)

        self.last_vs = current_vs

    def post_full_flight_log(self, reason: str = ""):
        """Envia todos os eventos acumulados para o endpoint PHP sequencialmente."""
        MAX_RETRIES = 3; RETRY_DELAY_MS = 5000 
        with self.log_lock: log_copy = self.event_log[:]
        if not log_copy: return

        for attempt in range(1, MAX_RETRIES + 1):
            all_events_succeeded = True
            for log_entry in log_copy:
                try:
                    response = requests.post(SUBMIT_LOG_URL, data=log_entry, timeout=5)
                    response_json = response.json()
                    if response.status_code != 200 or response_json.get('status') in ['error', 'not_found']:
                        all_events_succeeded = False; break 
                except requests.exceptions.RequestException:
                    all_events_succeeded = False; break 
                except json.JSONDecodeError:
                    all_events_succeeded = False; break
            
            if all_events_succeeded:
                with self.log_lock: self.event_log = []
                return
            if attempt < MAX_RETRIES: time.sleep(RETRY_DELAY_MS / 1000)

    def handle_session_end(self, data: Dict[str, Any]):
        """Chamado no encerramento do cliente."""
        if self.initial_fuel_logged and not self.flight_ended:
            self._log_event("CONEXAO_PERDIDA", "Conexão encerrada abruptamente.", data)
            self.post_full_flight_log("CONEXAO_PERDIDA")
            self.flight_ended = True
        with self.log_lock: self.event_log = []