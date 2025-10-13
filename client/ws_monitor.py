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
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Sucesso. DEP: {flight_plan['departureId']}, ARR: {flight_plan['arrivalId']}")
                    return flight_plan
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [IVAO FETCH] Erro ao buscar plano IVAO: {e}")

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
        self.last_tuned_freq: str = "N/A" 
        
        self.conn_thread: threading.Thread | None = None
        self.data_thread: threading.Thread | None = None


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
        
        # 1. Fecha o WebSocket (para que a thread de conexão saia)
        if self.ws_client:
            self.ws_client.close()
        
        # 2. Desconecta o rádio (limpeza de recursos de áudio)
        if self.radio_client:
             self.radio_client.disconnect()
        
        # 3. Espera as threads de fundo
        TIMEOUT = 1.0 
        
        # Espera a thread de dados
        if self.data_thread and self.data_thread.is_alive():
             self.data_thread.join(timeout=TIMEOUT) 
        
        # Espera a thread de conexão
        if self.conn_thread and self.conn_thread.is_alive():
             self.conn_thread.join(timeout=TIMEOUT)

        # 4. Limpeza do SimConnect global (garante que o handle seja liberado)
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

    def _on_open(self, ws):
        """Envia o pacote de identificação e inicia o loop de envio."""
        
        flight_plan = _fetch_network_flight_plan(self.vatsim_id, self.ivao_id)
        
        self.pilot_data['departureId'] = flight_plan['departureId']
        self.pilot_data['arrivalId'] = flight_plan['arrivalId']
        
        self.pilot_data['actual_network_id'] = flight_plan['networkUserId'] 
        
        if self.event_logger is None:
             self.event_logger = FlightEventLogger(self.display_name, self.pilot_data)

        initial_payload = json.dumps({
            "pilot_name": self.display_name, 
            "vatsim_id": self.vatsim_id, 
            "ivao_id": self.ivao_id,
            "departureId": flight_plan['departureId'], 
            "arrivalId": flight_plan['arrivalId'],     
            "packets_sent": 0, 
            "mb_sent": 0.0
        })
        ws.send(initial_payload)
        
        self.data_thread = threading.Thread(target=self._send_data_loop, daemon=True)
        self.data_thread.start()

    def _on_error(self, ws, error): 
        self.transmitting = False
        self.master_app.after(0, self.master_app.current_frame.update_status, False, "ERRO DE CONEXÃO")
        
    def _on_close(self, ws, close_status_code, close_msg): 
        self.transmitting = False 
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
        simconnect_fail_logged = False
        
        while self.running and self.ws_client.sock and self.ws_client.sock.connected:
            try:
                # 1. COLETAR DADOS DO SIMULADOR (Isto agora tentará RECONECTAR se estiver em SIMULADO)
                fetch_all_data()
                current_rounded = create_rounded_data(flight_data)
                
                # --- BLOCO REATIVO: GERENCIAMENTO DE ESTADO DO RÁDIO ---
                if CONN_STATUS == "REAL":
                    # ATUALIZA O STATUS DO SIMCONNECT NA UI PARA "REAL"
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, "REAL") 

                    if self.radio_client is None:
                        # Tenta instanciar e conectar o rádio
                        try:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO INFO] SimConnect REAL detectado. Tentando instanciar RadioClient...")
                            
                            self.radio_client = RadioClient() 
                            
                            # Se o PyAudio/PyGame falhou na inicialização (radio_ui_logic.py), self.p será None.
                            if self.radio_client.p is not None:
                                self.radio_client.connect()
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO INFO] Cliente de rádio inicializado e conectando automaticamente.")
                            else:
                                # Se a inicialização de dependências falhou, descartamos o cliente e informamos na UI.
                                self.radio_client = None
                                self.master_app.after(0, self.master_app.current_frame.update_status, False, "RÁDIO DESATIVADO (Dependência)")

                        except Exception as e:
                            # Captura erros de thread, memória, ou outros erros críticos na inicialização.
                            self.radio_client = None
                            self.master_app.after(0, self.master_app.current_frame.update_status, False, "RÁDIO FALHOU (Instância)")
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO CRÍTICO] Falha crítica ao instanciar RadioClient: {e}. Desativando o rádio.")
                else:
                    # Se o status for SIMULADO (o estado padrão ou após desconexão), atualiza a UI
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, "SIMULADO") 
                    
                    if self.radio_client:
                        # DESCONECTA O RÁDIO SE O STATUS VOLTAR PARA SIMULADO
                        self.radio_client.disconnect()
                        self.radio_client = None
                        
                # 2. SINTONIZAR O RÁDIO E ENVIAR POSIÇÃO (BLOCO TRY/EXCEPT DE USO)
                if self.radio_client:
                    try:
                        # SINTONIZAR: AGORA SINTONIZA NA COM2 (Prioriza COM2)
                        com2_freq = current_rounded.get('com2_active', 0.0) # <--- ALTERADO PARA COM2
                        
                        if com2_freq != 0.0:
                            new_freq_str = f"{com2_freq:.3f}"
                            
                            # Verifica se a frequência é válida e diferente da última sintonizada
                            if new_freq_str != self.last_tuned_freq:
                                self.radio_client.tune_frequency(new_freq_str)
                                self.last_tuned_freq = new_freq_str 
                        
                        # ENVIAR POSIÇÃO
                        lat = current_rounded.get('lat', 0.0)
                        lng = current_rounded.get('lng', 0.0)
                        self.radio_client.send_position(lat, lng)

                    except Exception as e:
                         # Erro de uso do rádio (e.g., erro de socket na conexão ou stream)
                         print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO CRÍTICO] Erro durante o uso do RadioClient: {e}. Desconectando o rádio.")
                         # Desconecta e descarta para forçar a re-inicialização
                         self.radio_client.disconnect()
                         self.radio_client = None 
                         self.master_app.after(0, self.master_app.current_frame.update_status, False, "RÁDIO FALHOU (Uso)")
                # --- FIM DO BLOCO REATIVO ---

                # 3. ATUALIZAR UI LOCAL (Sempre)
                self.master_app.after(0, self.master_app.current_frame.update_data, current_rounded)
                
                # 4. VERIFICAR STATUS DE TRANSMISSÃO (Bloqueia APENAS o Log de Eventos e o envio da Telemetria)
                if not self.transmitting:
                    time.sleep(0.1); continue 
                
                # Início da lógica que SÓ DEVE rodar se estiver transmitindo
                if self.event_logger:
                    self.event_logger.check_and_log_events(current_rounded) 

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
                # Captura a nova exceção de perda de conexão REAL de sim_data.py (Simulador fechado)
                simconnect_fail_logged = True
                self.transmitting = False 
                self.master_app.after(0, self.master_app.current_frame.update_status, False, "SIMULADOR DESCONECTADO")
                self.master_app.after(0, self.master_app.current_frame.update_sim_status, "SIMULADO (PERDA)") 
                
                last_data = current_rounded if 'current_rounded' in locals() else flight_data
                if self.event_logger:
                    self.event_logger.handle_session_end(last_data)
                
                # A próxima iteração chamará fetch_all_data(), que tentará a reconexão.
                time.sleep(0.1)
                
            except Exception as e: 
                # Lógica de falha original (erros não relacionados ao SimConnect)
                if CONN_STATUS == "REAL" and not simconnect_fail_logged:
                    simconnect_fail_logged = True
                    self.transmitting = False 
                    self.master_app.after(0, self.master_app.current_frame.update_status, False, "SIMULADOR DESCONECTADO")
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, f"{CONN_STATUS} (FALHA)")
                    
                    if self.radio_client:
                        self.radio_client.disconnect()
                        self.radio_client = None

                    last_data = current_rounded if 'current_rounded' in locals() else flight_data
                    if self.event_logger:
                        self.event_logger.handle_session_end(last_data)

                    if sm: 
                        try: sm.exit(); sm = None 
                        except Exception: pass 
                    
                    break
                
                time.sleep(0.1)