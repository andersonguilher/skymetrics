# Arquivo: client/gui.py

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from typing import Callable, Dict, Any
import threading

class LoginFormFrame(ttk.Frame):
    """Formulário de Login com delegação de lógica."""
    def __init__(self, master, on_success: Callable[[str, str, str, Dict[str, Any]], None], load_credentials_func: Callable, check_login_func: Callable, get_validated_pilot_data_func: Callable, save_credentials_func: Callable, delete_credentials_func: Callable, va_key: str, **kwargs):
        super().__init__(master, padding=30, **kwargs)
        self.on_success = on_success
        
        self._load_credentials_func = load_credentials_func
        self._check_login_func = check_login_func
        self._get_validated_pilot_data_func = get_validated_pilot_data_func
        self._save_credentials_func = save_credentials_func
        self._delete_credentials_func = delete_credentials_func
        
        self.email_var = ttk.StringVar(); self.password_var = ttk.StringVar()
        self.remember_var = ttk.BooleanVar(value=False) 
        self.current_version = master.current_version
        self.va_key = va_key
        
        ttk.Label(self, text=f"Login: {self.va_key}", font=("TkDefaultFont", 18, "bold")).pack(pady=(20, 5))
        ttk.Label(self, text=f"Versão: {self.current_version}", font=("TkDefaultFont", 10)).pack(pady=(0, 15))
        
        form_frame = ttk.Frame(self); form_frame.pack(pady=10, fill='x'); form_frame.columnconfigure(0, weight=1); form_frame.columnconfigure(1, weight=1)
        ttk.Label(form_frame, text="E-mail ou Username:", anchor='w').grid(row=0, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.email_var, width=40).grid(row=1, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        ttk.Label(form_frame, text="Senha:", anchor='w').grid(row=2, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.password_var, show="*", width=40).grid(row=3, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        ttk.Checkbutton(form_frame, text="Lembrar E-mail e Senha", variable=self.remember_var, bootstyle="round-toggle").grid(row=4, column=0, columnspan=2, pady=15, padx=5, sticky='w') 
        self.status_label = ttk.Label(form_frame, text="", bootstyle="info", font=("-size 10 -weight bold"), anchor='center')
        self.status_label.grid(row=5, column=0, columnspan=2, pady=(15, 5), sticky='ew') 
        ttk.Button(form_frame, text="Entrar", command=self._handle_login, bootstyle="success").grid(row=6, column=0, columnspan=2, pady=(5, 10))
        
        self._load_saved_credentials()

    def _load_saved_credentials(self):
         email_saved, password_saved, remember_me = self._load_credentials_func()
         if email_saved: self.email_var.set(email_saved)
         if remember_me:
             if password_saved: self.password_var.set(password_saved); self.remember_var.set(True)
             self.status_label.config(text="Credenciais salvas carregadas.", bootstyle="info")
            
    def _handle_login(self):
        email = self.email_var.get().strip(); password = self.password_var.get().strip(); remember = self.remember_var.get()
        if not email or not password: self.status_label.config(text="Preenchimento obrigatório.", bootstyle="danger"); return
        threading.Thread(target=self._process_login, args=(email, password, remember), daemon=True).start()
        
    def _process_login(self, email: str, password: str, remember: bool):
        self.master.after(0, lambda: self.status_label.config(text="Verificando credenciais (1/3)...", bootstyle="info"))
        
        if not self._check_login_func(email, password):
            self.master.after(0, lambda: self.status_label.config(text="Falha no login. Verifique e-mail/senha.", bootstyle="danger"))
            self._delete_credentials_func(email); return 
            
        self.master.after(0, lambda: self.status_label.config(text="Login OK. Verificando status de piloto (2/3)...", bootstyle="info"))
        
        pilot_data = self._get_validated_pilot_data_func(email) 
        if not pilot_data:
            self.master.after(0, lambda: self.status_label.config(text="Login OK, mas piloto não está na lista de validados.", bootstyle="warning"))
            self._delete_credentials_func(email); return 
        
        display_name = pilot_data.get('display_name', 'PILOTO DESCONHECIDO')

        if remember: self._save_credentials_func(email, password)
        else: self._delete_credentials_func(email) 
            
        self.master.after(0, lambda: self.status_label.config(text=f"Piloto Validado! Nome: {display_name}", bootstyle="success"))
        self.master.after(1000, lambda: self.on_success(email, password, display_name, pilot_data))


class MonitorFrame(ttk.Frame):
    """Painel de Monitoramento com Indicadores de Status Visuais."""
    def __init__(self, master, pilot_name: str, **kwargs):
        super().__init__(master, padding=20, **kwargs)
        self.pilot_name = pilot_name
        self.status_indicators: Dict[str, ttk.Frame] = {}

        # --- CABEÇALHO ---
        ttk.Label(self, text="Painel de Status", font=("TkDefaultFont", 16, "bold")).pack(pady=(0, 10))
        ttk.Label(self, text=f"Piloto: {pilot_name}", font=("TkDefaultFont", 11)).pack(pady=(0, 15))
        ttk.Separator(self).pack(fill='x', pady=5)

        # --- FRAME DOS INDICADORES ---
        indicators_frame = ttk.Frame(self)
        indicators_frame.pack(fill='both', expand=True, pady=10)

        # --- CRIAÇÃO DOS INDICADORES ---
        self._create_indicator_row(indicators_frame, "SimConnect", "simconnect", 0)
        self._create_indicator_row(indicators_frame, "Socket (Servidor)", "socket", 1)
        self._create_indicator_row(indicators_frame, "Rádio", "radio", 2)
        
        ttk.Separator(indicators_frame).grid(row=3, column=0, columnspan=2, sticky='ew', pady=10)

        self._create_indicator_row(indicators_frame, "Online (Rede)", "online", 4)
        self._create_indicator_row(indicators_frame, "Motor", "motor", 5)
        self._create_indicator_row(indicators_frame, "Táxi para decolagem", "taxi", 6)
        self._create_indicator_row(indicators_frame, "Decolado", "decolagem", 7)
        self._create_indicator_row(indicators_frame, "Pousado", "pouso", 8)
        
        # --- BOTÕES ---
        ttk.Button(self, text="Rádio Configurações", command=self.master._show_radio_config_window, bootstyle="info-outline").pack(pady=(15, 5))
        ttk.Button(self, text="Logoff", command=master._handle_logoff, bootstyle="danger-outline").pack(pady=(5, 0))

    def _create_indicator_row(self, parent, label_text: str, key: str, row_num: int):
        """Cria uma linha com um label e um indicador de status colorido."""
        # Label
        ttk.Label(parent, text=f"{label_text}:", font=("-size 10")).grid(row=row_num, column=0, sticky='w', padx=10, pady=5)
        
        # Indicador (um pequeno frame que mudará de cor)
        indicator = ttk.Frame(parent, width=20, height=20, bootstyle="danger") # Inicia como vermelho
        indicator.grid(row=row_num, column=1, sticky='w', padx=10)
        
        self.status_indicators[key] = indicator

    def update_indicator(self, key: str, status: bool):
        """
        Atualiza a cor de um indicador de status.
        Verde para True (ativo/conectado), Vermelho para False (inativo/desconectado).
        """
        if key in self.status_indicators:
            widget = self.status_indicators[key]
            new_style = "success" if status else "danger"
            widget.config(bootstyle=new_style)

    def update_all_indicators(self, statuses: Dict[str, bool]):
        """Atualiza todos os indicadores de uma vez a partir de um dicionário."""
        for key, status in statuses.items():
            self.update_indicator(key, status)