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

# IMPORTAÇÕES CORRIGIDAS (AGORA RELATIVAS PARA PYTHON -M)
from .sim_data import CONN_STATUS, sm
from .auth_utils import load_credentials, save_credentials, delete_credentials, check_login, get_validated_pilot_data
from .update_logic import check_for_update_sync, DECISION_PROCEED_TO_LOGIN, DECISION_INITIATE_UPDATE
from .ws_monitor import FlightMonitor
from .gui import LoginFormFrame, MonitorFrame


# =================================================================
# 1. CONSTANTES E CONFIGURAÇÃO
# =================================================================
CONFIG_FILE = 'client_config.ini'
CLIENT_CONFIG_SECTION = 'CLIENT_CONFIG' 
CLIENT_LOGIN_SECTION = 'LOGIN_CREDENTIALS'
CURRENT_VERSION = "1.0.3" 
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


class MainApplication(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title(f"Monitor de Voo - Inicializando...")
        self.geometry("300x480"); self.resizable(False, False)
        self._update_in_progress = False; self.current_version = CURRENT_VERSION
        
        try: self.iconbitmap(ICON_PATH)
        except Exception: pass
            
        self.monitor: FlightMonitor | None = None
        self.current_frame: ttk.Frame | None = None
        self.current_pilot_email: str | None = None 
        self.protocol("WM_DELETE_WINDOW", self._on_app_closing)
        
        self.tray_icon: pystray.Icon | None = None
        self.minimized_to_tray = False
        
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
        self.stop_monitor_and_simconnect()
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
        self.geometry("350x380"); self.resizable(False, False); self._center_window()
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
        self.stop_monitor_and_simconnect()
        if self.current_pilot_email: delete_credentials(self.current_pilot_email, clear_email=False, config_file=CONFIG_FILE)
        self.deiconify(); self._show_login_form()


if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()