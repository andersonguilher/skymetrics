# Arquivo: client/ws_monitor.py

import websocket
import threading
import json
import time
from datetime import datetime
from tkinter import messagebox
from typing import Dict, Any
import requests 
import sys # Importa sys para checar módulos

# Importações de módulos locais 
from event_logic import FlightEventLogger 
from sim_data import fetch_all_data, create_rounded_data, has_significant_change, flight_data, sm, CONN_STATUS
from radio_ui_logic import RadioClient # Importa a classe, mas trata falha na inicialização


# CONSTANTES (Portadas do node_server/config.js)
IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup" 
VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"
# -----------------------------

def _fetch_network_flight_plan(vatsim_id: str, ivao_id: str) -> Dict[str, str]:
    """Busca o plano de voo (DEP/ARR) nas redes VATSIM ou IVAO, priorizando o IVAO."""
    flight_plan = {"departureId": "N/A", "arrivalId": "N/A", "networkUserId": "N/A"}
    
    # 1. Tenta IVAO
    if ivao_id and ivao_id.upper() not in ('N/A', '', '0'):
        try:
            response = requests.get(IVAO_DATA_URL, timeout=8, verify=False)
            response.raise_for_status()
            data = response.json()
            ivao_id_int = int(ivao_id.strip())
            
            for client in data.get('clients', {}).get('pilots', []):
                if client.get('userId') == ivao_id_int and client.get('flightPlan'):
                    fp = client['flightPlan']
                    flight_plan["departureId"] = fp.get('departureId', "N/A").strip().upper()
                    flight_plan["arrivalId"] = fp.get('arrivalId', "N/A").strip().upper()
                    flight_plan["networkUserId"] = ivao_id
                    # Condição de sucesso: Departure e Arrival devem ser válidos
                    if flight_plan["departureId"] != "N/A" and flight_plan["arrivalId"] != "N/A":
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Sucesso. DEP: {flight_plan['departureId']}, ARR: {flight_plan['arrivalId']}")
                        return flight_plan
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Erro ao buscar plano IVAO: {e}")

    # Retorna o plano de voo (pode ser N/A se não encontrado)
    return flight_plan


class FlightMonitor:
    def __init__(self, pilot_email: str, display_name: str, pilot_data: Dict[str, Any], master_app, websocket_url: str, heartbeat_interval: int):
        super().__init__()
        self.pilot_email = pilot_email
        self.display_name = display_name 
        self.vatsim_id = pilot_data.get('vatsim_id', 'N/A')
        self.ivao_id = pilot_data.get('ivao_id', 'N/A')
        self.running = True
        self.ws_client = None
        self.last_sent_data: Dict[str, Any] | None = None
        self.packets_sent_count = 0
        self.total_bytes_sent = 0.0
        self.last_send_time = time.time() 
        self.master_app = master_app
        self.transmitting = False 
        
        self.websocket_url = websocket_url
        self.heartbeat_interval = heartbeat_interval
        
        self.event_logger: FlightEventLogger | None = None
        self.pilot_data = pilot_data 
        
        self.radio_client: RadioClient | None = None
        self.last_tuned_com2_freq: str = "N/A" 
        self.network_id_for_radio: str = "N/A"
        self.radio_was_connected = False
        self.last_position_send_time = 0.0
        
        self.conn_thread: threading.Thread | None = None
        self.data_thread: threading.Thread | None = None
        
        self.last_network_check_time = 0.0 


    def start_monitor(self):
        global flight_data
        flight_data["pilot_name"] = self.display_name
        self.conn_thread = threading.Thread(target=self._connection_management_loop, daemon=True)
        self.conn_thread.start()
        
    def stop(self):
        self.running = False
        if self.ws_client:
            self.ws_client.close()
        if self.radio_client:
             self.radio_client.disconnect()
        
        TIMEOUT = 1.0 
        if self.data_thread and self.data_thread.is_alive():
             self.data_thread.join(timeout=TIMEOUT) 
        if self.conn_thread and self.conn_thread.is_alive():
             self.conn_thread.join(timeout=TIMEOUT)

        global sm, CONN_STATUS
        if sm:
            try: 
                sm.exit()
            except: 
                pass
            sm = None
            CONN_STATUS = "SIMULADO" 

    def _connection_management_loop(self):
        RETRY_DELAY = 5 
        while self.running:
            self.ws_client = websocket.WebSocketApp(
                self.websocket_url, 
                on_open=self._on_open, 
                on_error=self._on_error, 
                on_close=self._on_close,
                on_message=self._on_message 
            )
            self.ws_client.run_forever(ping_interval=self.heartbeat_interval) 
            if self.running:
                self.last_sent_data = None 
                time.sleep(RETRY_DELAY)

    def _update_pilot_data_with_flight_plan(self, flight_plan: Dict[str, str]):
        self.pilot_data['departureId'] = flight_plan['departureId']
        self.pilot_data['arrivalId'] = flight_plan['arrivalId']
        
        final_network_id = flight_plan.get('networkUserId', 'N/A')
        if not final_network_id or final_network_id == 'N/A':
             final_network_id = self.vatsim_id or self.ivao_id
             
        self.network_id_for_radio = final_network_id
        self.pilot_data['actual_network_id'] = final_network_id
        
        if self.event_logger:
             self.event_logger.departure_id = flight_plan['departureId']
             self.event_logger.arrival_id = flight_plan['arrivalId']


    def _on_open(self, ws):
        flight_plan = _fetch_network_flight_plan(self.vatsim_id, self.ivao_id)
        self._update_pilot_data_with_flight_plan(flight_plan)
        
        # --- LÓGICA DE TRANSMISSÃO AUTÔNOMA ---
        if flight_plan.get("departureId", "N/A") != "N/A" and flight_plan.get("arrivalId", "N/A") != "N/A":
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENTE] Plano de voo válido encontrado. Iniciando transmissão.")
            self.transmitting = True
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENTE] Plano de voo não encontrado. Aguardando verificação periódica.")
            self.transmitting = False
        # --- FIM DA LÓGICA ---
        
        self.last_network_check_time = time.time()
        if self.event_logger is None:
             self.event_logger = FlightEventLogger(self.display_name, self.pilot_data)

        initial_payload = json.dumps({
            "pilot_name": self.display_name, 
            "vatsim_id": self.vatsim_id, 
            "ivao_id": self.ivao_id,
            "departureId": self.pilot_data['departureId'],
            "arrivalId": self.pilot_data['arrivalId'],
            "packets_sent": 0, 
            "mb_sent": 0.0
        })
        ws.send(initial_payload)
        
        self.data_thread = threading.Thread(target=self._send_data_loop, daemon=True)
        self.data_thread.start()

    def _on_error(self, ws, error): 
        self.transmitting = False
        if self.radio_client:
            self.radio_client.disconnect()
            self.radio_client = None
        
    def _on_close(self, ws, close_status_code, close_msg): 
        self.transmitting = False 
        if self.radio_client:
            self.radio_client.disconnect()
            self.radio_client = None

    def _on_message(self, ws, message):
        """Recebe comandos de controle do servidor (agora apenas STOP_TX)."""
        try:
            data = json.loads(message)
            command = data.get("command") 
            if command == "STOP_TX": 
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [SERVIDOR] Comando STOP_TX recebido. Transmissão pausada.")
                self.transmitting = False
        except Exception:
            pass

    def _send_data_loop(self):
        global flight_data, sm, CONN_STATUS
        
        while self.running and self.ws_client and self.ws_client.sock and self.ws_client.sock.connected:
            try:
                fetch_all_data()
                current_rounded = create_rounded_data(flight_data)
                
                if (time.time() - self.last_network_check_time) >= 60.0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENTE] Verificação periódica de plano de voo...")
                    flight_plan = _fetch_network_flight_plan(self.vatsim_id, self.ivao_id)
                    self._update_pilot_data_with_flight_plan(flight_plan)
                    self.last_network_check_time = time.time()

                    # Se a transmissão não estiver ativa, verifica se um plano de voo foi encontrado agora
                    if not self.transmitting and flight_plan.get("departureId", "N/A") != "N/A" and flight_plan.get("arrivalId", "N/A") != "N/A":
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [CLIENTE] Plano de voo encontrado na verificação. Iniciando transmissão.")
                        self.transmitting = True

                if CONN_STATUS == "REAL":
                    if self.radio_client is None:
                        try:
                            self.radio_client = RadioClient(master_app=self.master_app, pilot_id=self.network_id_for_radio)
                            if self.radio_client.p: self.radio_client.connect()
                            else: self.radio_client = None
                        except Exception as e:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO CRÍTICO] Falha ao instanciar RadioClient: {e}")
                            self.radio_client = None
                    
                    if self.radio_client:
                        is_connected = self.radio_client.sio.connected
                        if is_connected and not self.radio_was_connected: self.last_tuned_com2_freq = None
                        self.radio_was_connected = is_connected

                        if is_connected:
                            current_com2_freq = f"{current_rounded.get('com2_active', 0.0):.3f}"
                            if current_com2_freq != self.last_tuned_com2_freq:
                                self.radio_client.tune_frequency(current_com2_freq)
                                self.last_tuned_com2_freq = current_com2_freq
                            
                            if (time.time() - self.last_position_send_time) >= 2.0:
                                self.radio_client.send_position(current_rounded.get('lat', 0.0), current_rounded.get('lng', 0.0))
                                self.last_position_send_time = time.time()
                else: 
                    if self.radio_client:
                        self.radio_client.disconnect()
                        self.radio_client = None

                if self.event_logger:
                    self.event_logger.check_and_log_events(current_rounded) 

                if self.master_app.current_frame:
                    statuses = {
                        "simconnect": CONN_STATUS == "REAL",
                        "socket": self.ws_client.sock.connected,
                        "radio": self.radio_client is not None and self.radio_client.sio.connected,
                        "online": self.transmitting,
                        "motor": self.event_logger.initial_fuel_logged if self.event_logger else False,
                        "taxi": (self.event_logger._flight_sequence_started and not self.event_logger.is_airborne) if self.event_logger else False,
                        "decolagem": (self.event_logger.is_airborne and not self.event_logger.has_landed) if self.event_logger else False,
                        "pouso": (self.event_logger.has_landed and self.event_logger._flight_sequence_started) if self.event_logger else False,
                    }
                    self.master_app.after(0, self.master_app.current_frame.update_all_indicators, statuses)

                if self.transmitting:
                    force_send = (time.time() - self.last_send_time) >= self.heartbeat_interval
                    if has_significant_change(current_rounded, self.last_sent_data) or force_send:
                        self.last_sent_data = current_rounded.copy()
                        self.packets_sent_count += 1
                        
                        payload_to_send = json.dumps({
                            **current_rounded, 
                            'mb_sent': self.total_bytes_sent / (1024 * 1024),
                            'packets_sent': self.packets_sent_count
                        })

                        message_size = len(payload_to_send.encode('utf-8'))
                        self.total_bytes_sent += message_size
                        self.ws_client.send(payload_to_send)
                        self.last_send_time = time.time() 
                
                time.sleep(0.1)

            except ConnectionError as e:
                print(f"[ERRO] Conexão com o simulador perdida: {e}")
                if self.event_logger:
                    self.event_logger.handle_session_end(flight_data) 
                if self.radio_client:
                    self.radio_client.disconnect()
                    self.radio_client = None
                time.sleep(1) 
                
            except Exception as e: 
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro no loop de dados: {e}")
                if self.radio_client:
                    self.radio_client.disconnect()
                    self.radio_client = None
                time.sleep(1)