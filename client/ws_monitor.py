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
            # NOTA: O 'verify=False' aqui pode ser um problema de segurança, mas é mantido 
            # se for necessário para acessar a URL em certos ambientes.
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
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Sucesso. DEP: {flight_plan['departureId']}, ARR: {flight_plan['arrivalId']}")
                    return flight_plan
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Erro ao buscar plano IVAO: {e}")

    # 2. (A busca VATSIM original foi omitida, mas a busca por IVAO e o retorno permanecem)
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
        
        # Atributos para controle do rádio
        self.radio_client: RadioClient | None = None
        self.last_tuned_com2_freq: str = "N/A" 
        self.network_id_for_radio: str = "N/A"
        self.radio_was_connected = False
        self.last_position_send_time = 0.0
        
        self.conn_thread: threading.Thread | None = None
        self.data_thread: threading.Thread | None = None
        
        # NOVO: Para controle da checagem periódica do plano de voo
        self.last_network_check_time = 0.0 


    def start_monitor(self):
        """
        Inicia a thread de gerenciamento de conexão e reconexão.
        """
        global flight_data
        
        flight_data["pilot_name"] = self.display_name
        
        self.conn_thread = threading.Thread(target=self._connection_management_loop, daemon=True)
        self.conn_thread.start()
        
    def stop(self):
        """Encerra o monitor de forma segura, espera pelas threads e limpa o SimConnect globalmente."""
        self.running = False
        
        if self.ws_client:
            self.ws_client.close()
        
        # Manter a lógica de desconexão do rádio aqui (já existia)
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
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [SIMCONNECT] Limpeza final do SimConnect concluída.")
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
        """Função auxiliar para atualizar dados do piloto e do logger com o plano de voo encontrado."""
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
        """Envia o pacote de identificação e inicia o loop de envio."""
        
        flight_plan = _fetch_network_flight_plan(self.vatsim_id, self.ivao_id)
        
        # Usa nova função auxiliar para definir os IDs e a ID de rede para o rádio
        self._update_pilot_data_with_flight_plan(flight_plan)
        
        self.last_network_check_time = time.time() # Inicia o timer para a checagem periódica

        if self.event_logger is None:
             self.event_logger = FlightEventLogger(self.display_name, self.pilot_data)

        initial_payload = json.dumps({
            "pilot_name": self.display_name, 
            "vatsim_id": self.vatsim_id, 
            "ivao_id": self.ivao_id,
            "departureId": self.pilot_data['departureId'], # Usar dados atualizados
            "arrivalId": self.pilot_data['arrivalId'],     # Usar dados atualizados
            "packets_sent": 0, 
            "mb_sent": 0.0
        })
        ws.send(initial_payload)
        
        self.data_thread = threading.Thread(target=self._send_data_loop, daemon=True)
        self.data_thread.start()

    def _on_error(self, ws, error): 
        self.transmitting = False
        # CORREÇÃO: Desconecta o rádio se o WebSocket falhar
        if self.radio_client:
            self.radio_client.disconnect()
            self.radio_client = None
        self.master_app.after(0, self.master_app.current_frame.update_status, False, "ERRO DE CONEXÃO")
        
    def _on_close(self, ws, close_status_code, close_msg): 
        self.transmitting = False 
        # CORREÇÃO: Desconecta o rádio se o WebSocket fechar
        if self.radio_client:
            self.radio_client.disconnect()
            self.radio_client = None
        self.master_app.after(0, self.master_app.current_frame.update_status, False, "DESCONECTADO")

    def _on_message(self, ws, message):
            """Recebe comandos de controle (START_TX / STOP_TX)."""
            try:
                data = json.loads(message)
                command = data.get("command") 
                if command == "START_TX":
                    self.transmitting = True
                    self.master_app.after(0, self.master_app.current_frame.update_status, True, "TRANSMITINDO (Online Rede)")
                elif command == "STOP_TX": 
                    self.transmitting = False
                    self.master_app.after(0, self.master_app.current_frame.update_status, False, "PAUSADO (Offline/Solo)")
            except Exception:
                pass

    def _send_data_loop(self):
        """Loop principal de coleta de dados, detecção de eventos, envio WebSocket e SINTONIA DO RÁDIO."""
        global flight_data, sm, CONN_STATUS
        
        while self.running and self.ws_client and self.ws_client.sock and self.ws_client.sock.connected:
            try:
                fetch_all_data()
                current_rounded = create_rounded_data(flight_data)
                
                # NOVO: LÓGICA DE CHECK PERIÓDICO (a cada 60s)
                if (time.time() - self.last_network_check_time) >= 60.0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [NETWORK CHECK] Verificação periódica do plano de voo.")
                    flight_plan = _fetch_network_flight_plan(self.vatsim_id, self.ivao_id)
                    self._update_pilot_data_with_flight_plan(flight_plan)
                    self.last_network_check_time = time.time()
                # FIM NOVO

                # --- LÓGICA DO RÁDIO (Correta para ser controlada por CONN_STATUS) ---
                # A conexão do rádio é iniciada quando CONN_STATUS == "REAL"
                if CONN_STATUS == "REAL":
                    if self.radio_client is None:
                        try:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO INFO] SimConnect REAL detectado. Instanciando RadioClient...")
                            self.radio_client = RadioClient(master_app=self.master_app, pilot_id=self.network_id_for_radio)
                            if self.radio_client.p:
                                self.radio_client.connect()
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO INFO] RadioClient conectado.")
                            else:
                                self.radio_client = None
                        except Exception as e:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO CRÍTICO] Falha ao instanciar RadioClient: {e}")
                            self.radio_client = None
                    
                    if self.radio_client:
                        is_connected = self.radio_client.sio.connected
                        if is_connected and not self.radio_was_connected:
                            self.last_tuned_com2_freq = None # Força a resincronização da frequência na reconexão
                        self.radio_was_connected = is_connected

                        if is_connected:
                            # Sincroniza COM2
                            current_com2_freq = f"{current_rounded.get('com2_active', 0.0):.3f}"
                            if current_com2_freq != self.last_tuned_com2_freq:
                                self.radio_client.tune_frequency(current_com2_freq)
                                self.last_tuned_com2_freq = current_com2_freq
                            
                            # Envia posição (com otimização)
                            if (time.time() - self.last_position_send_time) >= 2.0:
                                self.radio_client.send_position(current_rounded.get('lat', 0.0), current_rounded.get('lng', 0.0))
                                self.last_position_send_time = time.time()
                else: # Se CONN_STATUS != "REAL"
                    # O rádio é desconectado e limpo quando a conexão SimConnect cai.
                    if self.radio_client:
                        self.radio_client.disconnect()
                        self.radio_client = None
                # --- FIM DA LÓGICA DO RÁDIO ---

                self.master_app.after(0, self.master_app.current_frame.update_data, current_rounded)
                self.master_app.after(0, self.master_app.current_frame.update_sim_status, CONN_STATUS)

                # --- INÍCIO DA CORREÇÃO ---
                # A lógica de eventos agora é executada independentemente do estado de transmissão.
                if self.event_logger:
                    self.event_logger.check_and_log_events(current_rounded) 
                # --- FIM DA CORREÇÃO ---

                # A telemetria é enviada apenas se o servidor permitir (self.transmitting é True após START_TX)
                if not self.transmitting:
                    time.sleep(0.1)
                    continue 

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

            except ConnectionError:
                # Ocorre quando a SimConnect REAL falha em sim_data.py
                self.master_app.after(0, self.master_app.current_frame.update_status, False, "SIMULADOR DESCONECTADO")
                if self.event_logger:
                    # Envio final de logs em caso de perda de conexão
                    self.event_logger.handle_session_end(flight_data) 
                
                # Garante que o rádio está desconectado em caso de SimConnect.ConnectionError
                if self.radio_client:
                    self.radio_client.disconnect()
                    self.radio_client = None
                time.sleep(1) # Aguarda antes de tentar reconectar
                
            except Exception as e: 
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Erro no loop de dados: {e}")
                if self.radio_client:
                    self.radio_client.disconnect()
                    self.radio_client = None
                time.sleep(1)