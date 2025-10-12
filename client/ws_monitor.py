# Arquivo: client/ws_monitor.py

import websocket
import threading
import json
import time
from datetime import datetime
from tkinter import messagebox
from typing import Dict, Any

# Importações de módulos locais CORRIGIDAS (devem ser relativas)
from .event_logic import FlightEventLogger 
from .sim_data import fetch_all_data, create_rounded_data, has_significant_change, flight_data, sm, CONN_STATUS


class FlightMonitor:
    def __init__(self, pilot_email: str, display_name: str, pilot_data: Dict[str, Any], master_app, websocket_url: str, heartbeat_interval: int):
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
        
        self.event_logger = FlightEventLogger(display_name, pilot_data)


    def start_monitor(self):
        """Inicia a thread de gerenciamento de conexão e reconexão."""
        global flight_data
        
        flight_data["pilot_name"] = self.display_name
        
        threading.Thread(target=self._connection_management_loop, daemon=True).start()
        
    def stop(self):
        """Encerra o monitor de forma segura."""
        self.running = False
        if self.ws_client:
            self.ws_client.close()

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
        initial_payload = json.dumps({
            "pilot_name": self.display_name, 
            "vatsim_id": self.vatsim_id, 
            "ivao_id": self.ivao_id,
            "packets_sent": 0, 
            "mb_sent": 0.0
        })
        ws.send(initial_payload)
        threading.Thread(target=self._send_data_loop, daemon=True).start()

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
        """Loop principal de coleta de dados, detecção de eventos e envio WebSocket."""
        global flight_data, sm, CONN_STATUS
        simconnect_fail_logged = False
        
        while self.running and self.ws_client.sock and self.ws_client.sock.connected:
            if not self.transmitting:
                time.sleep(0.1); continue 
            
            try:
                fetch_all_data()
                current_rounded = create_rounded_data(flight_data)
                
                self.event_logger.check_and_log_events(current_rounded) 

                force_send = (time.time() - self.last_send_time) >= self.heartbeat_interval

                if has_significant_change(current_rounded, self.last_sent_data) or force_send:
                    self.last_sent_data = current_rounded.copy()
                    self.packets_sent_count += 1
                    self.master_app.after(0, self.master_app.current_frame.update_data, current_rounded)
                    
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

            except Exception as e: 
                if CONN_STATUS == "REAL" and not simconnect_fail_logged:
                    simconnect_fail_logged = True
                    self.transmitting = False 
                    self.master_app.after(0, self.master_app.current_frame.update_status, False, "SIMULADOR DESCONECTADO")
                    self.master_app.after(0, self.master_app.current_frame.update_sim_status, f"{CONN_STATUS} (FALHA)")

                    last_data = current_rounded if 'current_rounded' in locals() else flight_data
                    self.event_logger.handle_session_end(last_data)

                    if sm: 
                        try: sm.exit(); sm = None 
                        except Exception: pass 
                    
                    break
                
                time.sleep(0.1)