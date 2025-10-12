# Arquivo: client/main.py (Ponto de Entrada Principal)

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import configparser
import os 
import sys
import threading
from typing import Dict, Any
from subprocess import Popen 
import time
from tkinter import messagebox 


# --- CORREÇÃO FINAL DE IMPORTAÇÃO PARA AMBIENTE DE TESTES ---
# A chave é adicionar o diretório PAI do 'client' para tratar 'client' como um pacote em ambientes específicos.
if not getattr(sys, 'frozen', False):
    # Obtém o diretório PARENT (onde fica 'client' e 'node_server').
    # __file__ -> '.../client/main.py'
    # os.path.dirname(__file__) -> '.../client'
    # os.path.dirname(os.path.dirname(__file__)) -> '.../' (a pasta raiz do projeto)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Adiciona a raiz do projeto ao sys.path, permitindo que 'client' seja tratado como um módulo de topo.
    if project_root not in sys.path:
        sys.path.append(project_root)
        
    # Quando rodando com 'python -m client.main', o sys.path já está modificado
    # pelo -m. Se a sintaxe for quebrada, a injeção falha. Vamos tentar uma última vez
    # a injeção direta no caso de teste.
    # Esta linha é redundante no PyInstaller, mas é uma defesa extra para testes.
    client_dir = os.path.dirname(os.path.abspath(__file__))
    if client_dir not in sys.path:
        sys.path.append(client_dir)
# --------------------------------------------------------------------------------


# IMPORTAÇÕES DIRETAS (A sintaxe para módulos internos permanece a mesma para PyInstaller)
from sim_data import CONN_STATUS, sm
from auth_utils import load_credentials, save_credentials, delete_credentials, check_login, get_validated_pilot_data
from update_logic import check_for_update_sync, DECISION_PROCEED_TO_LOGIN, DECISION_INITIATE_UPDATE
from ws_monitor import FlightMonitor
from gui import LoginFormFrame, MonitorFrame
from radio_ui_logic import RadioConfigWindow, RadioClient 


# =================================================================
# 1. CONSTANTES E CONFIGURAÇÃO
# =================================================================
CONFIG_FILE = 'client_config.ini'
CLIENT_CONFIG_SECTION = 'CLIENT_CONFIG' 
CLIENT_LOGIN_SECTION = 'LOGIN_CREDENTIALS'
CURRENT_VERSION = "1.0.5" 
UPDATE_EXECUTABLE_NAME = "updater.exe" 

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

if CLIENT_CONFIG_SECTION not in config: config[CLIENT_CONFIG_SECTION] = {}
if CLIENT_LOGIN_SECTION not in config: config[CLIENT_LOGIN_SECTION] = {}

VA_KEY = config.get(CLIENT_CONFIG_SECTION, 'va_key', fallback='KAFLY')
WEBSOCKET_URL = config.get(CLIENT_CONFIG_SECTION, 'websocket_url', fallback='ws://www.kafly.com.br:8765')
HEARTBEAT_INTERVAL = config.getint(CLIENT_CONFIG_SECTION, 'heartbeat_interval', fallback=5)
UPDATE_CHECK_URL = config.get(CLIENT_CONFIG_SECTION, 'update_check_url', fallback="https://kafly.com.br/skymetrics/update/current_version.txt")


# --- FUNÇÕES AUXILIARES ---
def _get_resource_path(relative_path: str) -> str:
    """ Obtém o caminho absoluto para um recurso, funcionando com PyInstaller. """
    try:
        if getattr(sys, 'frozen', False):
            return os.path.join(sys._MEIPASS, 'assets', relative_path)
        else:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', relative_path)
    except Exception:
        return relative_path

ICON_PATH = _get_resource_path('icons/skymetrics.ico')

# Tenta importar pystray (para a System Tray)
try:
    from PIL import Image
    import pystray 
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False
except Exception:
    PYSTRAY_AVAILABLE = False


class MainApplication(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title(f"Monitor de Voo - Inicializando...")
        self.geometry("350x550"); self.resizable(False, False)
        self._update_in_progress = False; self.current_version = CURRENT_VERSION
        
        try: self.iconbitmap(ICON_PATH)
        except Exception: pass
            
        self.monitor: FlightMonitor | None = None
        self.current_frame: ttk.Frame | None = None
        self.current_pilot_email: str | None = None 
        self.protocol("WM_DELETE_WINDOW", self._on_app_closing)
        
        self.tray_icon: pystray.Icon | None = None
        self.minimized_to_tray = False
        
        self.radio_config_window: RadioConfigWindow | None = None
        
        self._center_window()
        threading.Thread(target=self._initial_flow_thread, daemon=True).start()

    def _center_window(self):
        self.update_idletasks()
        width = self.winfo_width(); height = self.winfo_height()
        screen_width = self.winfo_screenwidth(); screen_height = self.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        self.geometry(f'+{x}+{y}')
        
    def _initial_flow_thread(self):
        # Delegate calls to auth_utils in the main app object
        self._set_delegated_auth_funcs() 
        decision, latest_version = check_for_update_sync(self, CURRENT_VERSION, UPDATE_CHECK_URL)
        self.after(0, self._handle_update_decision, decision, latest_version)
        
    def _handle_update_decision(self, decision: int, latest_version: str | None):
        self.title(f"Monitor de Voo - Login {VA_KEY}")

        if decision == DECISION_INITIATE_UPDATE and latest_version:
            self._initiate_update_final_step(latest_version)
        
        elif decision == DECISION_PROCEED_TO_LOGIN:
            email, password, remember_me = self._auth_funcs['load']()
            self.after(60 * 60 * 1000, self.start_periodic_update_check)
            if email and password and remember_me: self._attempt_auto_login(email, password)
            else: self._show_login_form()

    def _initiate_update_final_step(self, latest_version: str):
        self._update_in_progress = True
        self.stop_monitor_and_simconnect()
        try: Popen([UPDATE_EXECUTABLE_NAME, latest_version])
        except Exception: pass
        self.destroy()

    def _set_delegated_auth_funcs(self):
        """Define e armazena as funções delegadas de auth para o formulário."""
        self._auth_funcs = {
            'load': lambda: load_credentials(CONFIG_FILE),
            'check': lambda e, p: check_login(e, p, CONFIG_FILE),
            'get_pilot': lambda e: get_validated_pilot_data(e, CONFIG_FILE),
            'save': lambda e, p: save_credentials(e, p, CONFIG_FILE),
            'delete': lambda e, c=True: delete_credentials(e, c, CONFIG_FILE),
        }

    # --- LÓGICA RÁDIO CONFIGURAÇÃO ---
    def _show_radio_config_window(self):
        """
        Abre a janela de configuração do rádio. 
        Se o cliente de rádio não foi inicializado (modo SIMULADO ou falha), 
        cria um RadioClient temporário para a janela de configurações.
        """
        print("[DIAG] Tentativa de abrir RadioConfigWindow...") 
        
        if self.radio_config_window and self.radio_config_window.winfo_exists():
            self.radio_config_window.lift()
            return
            
        target_radio_client = None
        
        # 1. Tenta usar o cliente já inicializado pelo monitor (se REAL e bem-sucedido)
        if self.monitor and self.monitor.radio_client:
            target_radio_client = self.monitor.radio_client
            print(f"[DIAG] Usando RadioClient ativo do Monitor.")
        
        # 2. Se não houver cliente ativo (SIMULADO ou falha inicial)
        elif self.monitor:
            print("[DIAG] Tentando criar RadioClient temporário para configuração.")
            try:
                # Importa a classe DENTRO do método para evitar falha no __init__ do MainApplication
                from radio_ui_logic import RadioClient 
                target_radio_client = RadioClient()
            except Exception as e:
                # Falha ao instanciar o RadioClient devido a dependências ausentes (o caso original do usuário)
                messagebox.showerror("Erro ao Abrir Configurações do Rádio", 
                                     f"Falha ao instanciar o RadioClient: {e}. Verifique as dependências (pyaudio, pygame, etc.) e se o PTT/Dispositivos estão configurados corretamente.")
                return

        if target_radio_client:
            try:
                self.radio_config_window = RadioConfigWindow(self, target_radio_client)
            except Exception as e:
                # Este bloco captura erros que ocorrem durante o setup da janela TK (ex: falha de áudio GUI)
                messagebox.showerror("Erro ao Abrir Configurações do Rádio", 
                                     f"Falha ao iniciar a janela de configurações: {e}. O RadioClient temporário será desconectado.")
                # Se o cliente temporário foi criado, desconecte-o. Se era o cliente do monitor, ele fica ativo.
                if target_radio_client is not self.monitor.radio_client:
                    target_radio_client.disconnect() 
                self.radio_config_window = None 
        else:
             # Este caso é um fallback, mas não deve ocorrer se o monitor estiver ativo
             messagebox.showerror("Erro de Inicialização do Rádio", "O cliente de rádio não foi inicializado corretamente. Verifique se as dependências (PyAudio, SocketIO) foram instaladas.")
             print("[DIAG] Falha na inicialização: self.monitor.radio_client é None.")
            
    def _on_radio_config_closing(self):
        """Callback de fechamento da janela de rádio para atualizar o estado do cliente."""
        # A janela de rádio é responsável por salvar a config no client_config.json
        if self.monitor and self.monitor.radio_client:
            self.monitor.radio_client.update_audio_streams() # Re-inicia os streams com novos dispositivos, se necessário

    # --- LÓGICA DA BANDEJA ---
    def _show_window_from_tray(self, icon: pystray.Icon, item: pystray.MenuItem):
        if self.tray_icon: icon.stop(); self.tray_icon = None
        self.after(0, self.deiconify); self.minimized_to_tray = False

    def _on_logoff_from_tray(self, icon: pystray.Icon, item: pystray.MenuItem):
        if self.tray_icon: icon.stop(); self.tray_icon = None
        self.minimized_to_tray = False; self.after(0, self._handle_logoff)

    def _on_quit_from_tray(self, icon: pystray.Icon, item: pystray.MenuItem):
        if self.tray_icon: icon.stop(); self.tray_icon = None
        self.minimized_to_tray = False; self.after(0, self._on_app_closing)

    def _start_tray_icon(self):
        if not PYSTRAY_AVAILABLE or self.tray_icon: return
        self.withdraw(); self.minimized_to_tray = True
        menu = (
            pystray.MenuItem('Mostrar Monitor', self._show_window_from_tray, default=True),
            pystray.MenuItem('Logoff', self._on_logoff_from_tray),
            pystray.MenuItem('Sair', self._on_quit_from_tray)
        )
        try:
            from PIL import Image # Re-import here for thread safety
            icon_image = Image.open(ICON_PATH)
            self.tray_icon = pystray.Icon('skymetrics_monitor', icon_image, 'SkyMetrics Monitor', menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.deiconify(); self.minimized_to_tray = False

    # --- LÓGICA DE GERENCIAMENTO DE ESTADO ---
    def stop_monitor_and_simconnect(self):
        if self.monitor: self.monitor.stop()
        global sm
        if CONN_STATUS == "REAL" and sm: 
            try: sm.exit(); sm = None
            except Exception: pass

    def start_periodic_update_check(self):
        if not self.winfo_exists() or self._update_in_progress: return
        def periodic_check(): check_for_update_sync(self, CURRENT_VERSION, UPDATE_CHECK_URL)
        threading.Thread(target=periodic_check, daemon=True).start()
        self.after(60 * 60 * 1000, self.start_periodic_update_check)

    def _attempt_auto_login(self, email: str, password: str):
        self._show_login_form()
        time.sleep(0.1) 
        self.login_frame.status_label.config(text="Tentando Login Automático...", bootstyle="info")
        threading.Thread(target=self.login_frame._process_login, args=(email, password, True), daemon=True).start()

    def _on_app_closing(self):
        if self._update_in_progress: self.destroy(); return
        if self.tray_icon: self.tray_icon.stop()
        if self.monitor and self.monitor.event_logger:
             last_data = self.monitor.last_sent_data if self.monitor.last_sent_data else {}
             self.monitor.event_logger.handle_session_end(last_data)
        # NOVO: Chamada para desconexão do rádio
        if self.monitor and self.monitor.radio_client:
             self.monitor.radio_client.disconnect()
             
        self.stop_monitor_and_simconnect()
        # A linha de exclusão de credenciais (delete_credentials) DEVE ESTAR AUSENTE AQUI.
        self.destroy()

    def _show_login_form(self):
        if self.current_frame: self.current_frame.destroy()
        self.geometry("300x480"); self.resizable(False, False); self._center_window()
        self.login_frame = LoginFormFrame(
            self, on_success=self._on_login_success, va_key=VA_KEY, 
            load_credentials_func=self._auth_funcs['load'], check_login_func=self._auth_funcs['check'],
            get_validated_pilot_data_func=self._auth_funcs['get_pilot'], save_credentials_func=self._auth_funcs['save'],
            delete_credentials_func=self._auth_funcs['delete']
        )
        self.login_frame.pack(fill=BOTH, expand=YES); self.current_frame = self.login_frame
        if self.minimized_to_tray: self.deiconify(); self.minimized_to_tray = False

    def _on_login_success(self, email: str, password: str, display_name: str, pilot_data: Dict[str, Any]):
        if self.current_frame: self.current_frame.destroy()
        self.current_pilot_email = email
        self.geometry("350x550"); self.resizable(False, False); self._center_window()
        self.title(f"Monitor de Voo {VA_KEY} - Piloto: {display_name}")
        self.monitor = FlightMonitor(email, display_name, pilot_data, self, WEBSOCKET_URL, HEARTBEAT_INTERVAL)
        self.monitor.start_monitor()
        monitor_frame = MonitorFrame(self, display_name, CONN_STATUS)
        monitor_frame.pack(fill=BOTH, expand=YES); self.current_frame = monitor_frame
        self.after(500, self._start_tray_icon)

    def _handle_logoff(self):
        if self.tray_icon: self.tray_icon.stop(); self.tray_icon = None; self.minimized_to_tray = False
        if self.monitor and self.monitor.event_logger:
             last_data = self.monitor.last_sent_data if self.monitor.last_sent_data else {}
             self.monitor.event_logger.handle_session_end(last_data)
        # NOVO: Desconexão do rádio no logoff
        if self.monitor and self.monitor.radio_client:
             self.monitor.radio_client.disconnect()
             
        self.stop_monitor_and_simconnect()
        self.deiconify(); self._show_login_form()


if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()