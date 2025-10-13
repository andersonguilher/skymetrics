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
    """Painel de Monitoramento Detalhado e Dinâmico."""
    def __init__(self, master, pilot_name: str, conn_status: str, **kwargs):
        super().__init__(master, padding=20, **kwargs)
        self.pilot_name = pilot_name; self.vs_widget = None 
        
        # NOVO: Variável para a distância do rádio
        self.radio_dist_var = ttk.StringVar(value="N/A") 
        
        self.data_vars = {
            "alt_ind": ttk.StringVar(value="0 ft"), "vs": ttk.StringVar(value="0 fpm"), "ias": ttk.StringVar(value="0 kts"), 
            "agl": ttk.StringVar(value="0 ft"), "g_force": ttk.StringVar(value="1.0 g"), "fuel": ttk.StringVar(value="0 gal"),
            "com1_active": ttk.StringVar(value="N/A MHz"), 
            "com2_active": ttk.StringVar(value="N/A MHz")  
        }
        
        ttk.Label(self, text=f"Monitor de Telemetria", font=("TkDefaultFont", 12, "bold")).pack(pady=(0, 10))
        
        self.status_frame = ttk.Frame(self); self.status_frame.pack(fill='x', pady=5)
        ttk.Label(self.status_frame, text=f"Piloto: {pilot_name} | SimConnect:").grid(row=0, column=0, padx=5, sticky='w')
        self.sim_status_label = ttk.Label(self.status_frame, text=conn_status, bootstyle="info")
        self.sim_status_label.grid(row=0, column=1, sticky='e')
        
        # NOVO: Exibição da Distância TX
        self.radio_dist_frame = ttk.Frame(self); self.radio_dist_frame.pack(fill='x', pady=5)
        ttk.Label(self.radio_dist_frame, text="Distância TX (Mock):").grid(row=0, column=0, padx=5, sticky='w')
        ttk.Label(self.radio_dist_frame, textvariable=self.radio_dist_var, font=("-size 11 -weight bold"), bootstyle="light").grid(row=0, column=1, sticky='e')
        self.radio_dist_frame.columnconfigure(1, weight=1)
        
        ttk.Separator(self).pack(fill='x', pady=5)
        
        self.tx_status_label = ttk.Label(self, text="AGUARDANDO SERVIDOR...", font=("TkDefaultFont", 10, "bold"), bootstyle="warning")
        self.tx_status_label.pack(fill='x', pady=5)
        
        ttk.Separator(self).pack(fill='x', pady=10)
        
        data_frame = ttk.Frame(self); data_frame.pack(fill='both', expand=True)
        
        self._create_data_row(data_frame, "ALTITUDE (MSL):", "alt_ind", 0)
        self._create_data_row(data_frame, "VS (FPM):", "vs", 1)
        self._create_data_row(data_frame, "IAS (KTS):", "ias", 2)
        self._create_data_row(data_frame, "AGL (FT):", "agl", 3)
        self._create_data_row(data_frame, "G-FORCE:", "g_force", 4)
        self._create_data_row(data_frame, "TOTAL FUEL:", "fuel", 5)
        
        ttk.Separator(self).pack(fill='x', pady=10) 
        
        self._create_data_row(data_frame, "COM1 ACTIVE:", "com1_active", 6) 
        self._create_data_row(data_frame, "COM2 ACTIVE:", "com2_active", 7) 
        
        ttk.Separator(self).pack(fill='x', pady=10)
        
        # NOVO: Botão de Configuração do Rádio
        ttk.Button(self, text="Rádio Configurações", command=self._show_radio_config, bootstyle="info-outline").pack(pady=(5, 5))

        ttk.Button(self, text="Logoff", command=master._handle_logoff, bootstyle="danger-outline").pack(pady=(5, 0))

    def _show_radio_config(self):
        """Chama a função no MainApplication para abrir a janela de configuração do rádio."""
        self.master._show_radio_config_window() 

    def _create_data_row(self, parent, label_text: str, var_key: str, row_num: int):
        row = ttk.Frame(parent, padding=2); row.pack(fill='x')
        ttk.Label(row, text=label_text, width=15).pack(side='left', padx=(0, 10))
        value_widget = ttk.Label(row, textvariable=self.data_vars[var_key], font=("-size 11 -weight bold"), bootstyle="light")
        value_widget.pack(side='right', fill='x', expand=True)
        if var_key == "vs": self.vs_widget = value_widget


    def update_data(self, data: Dict[str, Any]):
        """Atualiza todas as variáveis da GUI com os novos dados."""
        self.data_vars["alt_ind"].set(f"{int(data['alt_ind']):,} ft".replace(',', '.'))
        self.data_vars["vs"].set(f"{int(data['vs']):,} fpm".replace(',', '.'))
        self.data_vars["ias"].set(f"{data['ias']:.1f} kts")
        self.data_vars["agl"].set(f"{int(data['agl']):,} ft".replace(',', '.'))
        self.data_vars["g_force"].set(f"{data['g_force']:.1f} g")
        self.data_vars["fuel"].set(f"{int(data['total_fuel']):,} gal".replace(',', '.'))
        
        # Atualiza as frequências COM
        self.data_vars["com1_active"].set(f"{data['com1_active']:.3f} MHz")
        self.data_vars["com2_active"].set(f"{data['com2_active']:.3f} MHz")
        
        if self.vs_widget:
            if data['vs'] > 100:
                self.data_vars["vs"].set(f"+{self.data_vars['vs'].get()}")
                self.vs_widget.config(bootstyle="success")
            elif data['vs'] < -100:
                self.vs_widget.config(bootstyle="danger")
            else:
                self.vs_widget.config(bootstyle="light")


    def update_status(self, is_transmitting: bool, message: str):
        """Atualiza o status de transmissão."""
        style = "success" if is_transmitting else "danger"
        self.tx_status_label.config(text=message, bootstyle=style)

    def update_sim_status(self, message: str):
        """Atualiza o status do SimConnect."""
        self.sim_status_label.config(text=message, bootstyle="info")
        
    def update_radio_distance(self, distance_km: float):
        """NOVO: Atualiza a distância do rádio na UI."""
        self.radio_dist_var.set(f"{distance_km:.1f} km")