# gui_elements.py
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from typing import Callable
import va_auth
import keyring 
import configparser 
import os 
# REMOVIDA A LINHA ABAIXO PARA EVITAR O ImportError:
# from keyring.errors import NoPasswordFoundError 

# Constantes de Configuração
CONFIG_FILE = 'config.ini'
CONFIG_SECTION = 'LOGIN'
KEYRING_SERVICE_ID = 'VA_Monitor_Pilot_Password'


def save_credentials(va_key: str, email: str, password: str):
    """Salva o email e a VA no config.ini e a senha no keyring."""
    try:
        # 1. Salvar email e VA no config.ini
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        
        if CONFIG_SECTION not in config:
            config[CONFIG_SECTION] = {}

        config[CONFIG_SECTION]['remember_me'] = 'True'
        config[CONFIG_SECTION]['pilot_email'] = email
        config[CONFIG_SECTION]['va_key_selected'] = va_key
        
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
            
        # 2. Salvar senha no keyring
        keyring_username = f"{va_key}:{email}"
        keyring.set_password(KEYRING_SERVICE_ID, keyring_username, password)
        
    except Exception as e:
        print(f"Erro ao salvar credenciais: {e}")

def load_credentials() -> tuple[str, str, str, bool]:
    """Carrega o email e a VA do config.ini, e a senha do keyring."""
    email = ""
    va_key = ""
    password = ""
    remember_me = False
    
    try:
        # 1. Carregar do config.ini
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        
        if CONFIG_SECTION in config:
            email = config.get(CONFIG_SECTION, 'pilot_email', fallback="")
            va_key = config.get(CONFIG_SECTION, 'va_key_selected', fallback="")
            remember_me = config.getboolean(CONFIG_SECTION, 'remember_me', fallback=False)

        if email and va_key and remember_me:
            # 2. Carregar senha do keyring
            keyring_username = f"{va_key}:{email}"
            password = keyring.get_password(KEYRING_SERVICE_ID, keyring_username)
            
    except Exception as e:
        print(f"Erro ao carregar credenciais: {e}")
        return "", "", "", False 
        
    return va_key, email, password, remember_me

def delete_credentials(va_key: str, email: str):
    """Deleta o registro de 'lembrar' do config.ini e a senha do keyring."""
    try:
        # 1. Deletar do keyring
        keyring_username = f"{va_key}:{email}"
        try:
            keyring.delete_password(KEYRING_SERVICE_ID, keyring_username)
        except Exception: # USANDO EXCEPTION GENÉRICA
            pass 
            
        # 2. Atualizar config.ini (desliga o remember_me, mantém o email)
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        
        if CONFIG_SECTION in config:
            config[CONFIG_SECTION]['remember_me'] = 'False'
            
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
            
    except Exception as e:
        print(f"Erro ao deletar credenciais: {e}")


class VASwitcherFrame(ttk.Frame):
    """Tela inicial para escolher a VA."""
    def __init__(self, master, on_va_select: Callable[[str], None]):
        super().__init__(master, padding=30)
        self.on_va_select = on_va_select

        ttk.Label(self, text="Selecione sua VA", font=("TkDefaultFont", 14, "bold")).pack(pady=20)

        # KAFLY (CORRIGIDO)
        ttk.Button(
            self, 
            text="KAFLY (kafly.com.br)", 
            command=lambda: self._select_va("KAFLY"), 
            bootstyle="primary",
            width=30
        ).pack(pady=10)
        
        # CUBANA
        ttk.Button(
            self, 
            text="CUBANA (cubana-va.com)", 
            command=lambda: self._select_va("CUBANA"), 
            bootstyle="info",
            width=30
        ).pack(pady=10)
        
        # Tenta carregar credenciais salvas e avança para login se VA estiver salva
        va_key_saved, _, _, remember_me = load_credentials()
        if remember_me and va_key_saved:
             # Pequeno atraso para dar tempo de renderizar a tela.
             self.master.after(100, lambda: self._select_va(va_key_saved))


    def _select_va(self, va_key):
        self.on_va_select(va_key)

class LoginFormFrame(ttk.Frame):
    """Tela de formulário de login para a VA escolhida (Layout Aprimorado)."""
    def __init__(self, master, va_key: str, on_success: Callable[[str, str], None], on_back: Callable):
        super().__init__(master, padding=30)
        self.va_key = va_key
        self.on_success = on_success
        self.on_back = on_back
        
        # Variáveis de entrada
        self.email_var = ttk.StringVar()
        self.password_var = ttk.StringVar()
        self.remember_var = ttk.BooleanVar(value=False) 

        # --- Cabeçalho e Título ---
        ttk.Label(self, text=f"Login: {va_key}", font=("TkDefaultFont", 18, "bold")).pack(pady=20)
        
        # --- Frame para centralizar o formulário ---
        # Alterado pady para dar mais espaço vertical.
        form_frame = ttk.Frame(self)
        form_frame.pack(pady=10, fill='x') 
        form_frame.columnconfigure(0, weight=1)
        form_frame.columnconfigure(1, weight=1) 
        
        # Linha 0: E-mail/Username
        ttk.Label(form_frame, text="E-mail ou Username:", anchor='w').grid(row=0, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.email_var, width=40).grid(row=1, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        
        # Linha 2: Senha
        ttk.Label(form_frame, text="Senha:", anchor='w').grid(row=2, column=0, columnspan=2, pady=(10, 0), padx=5, sticky='w')
        ttk.Entry(form_frame, textvariable=self.password_var, show="*", width=40).grid(row=3, column=0, columnspan=2, pady=5, ipady=3, padx=5, sticky='ew')
        
        # Linha 4: Checkbox "Lembrar E-mail e Senha"
        ttk.Checkbutton(
            form_frame, 
            text="Lembrar E-mail e Senha", 
            variable=self.remember_var, 
            bootstyle="round-toggle" 
        ).grid(row=4, column=0, columnspan=2, pady=15, padx=5, sticky='w') 

        # --- Status Label (MOVIDO PARA O form_frame) ---
        self.status_label = ttk.Label(form_frame, text="", bootstyle="info", font=("-size 10 -weight bold"), anchor='center')
        # Row 5: Centraliza e estica para caber no frame
        self.status_label.grid(row=5, column=0, columnspan=2, pady=(15, 5), sticky='ew') 

        # --- Botões (MOVIDO PARA O form_frame) ---
        button_frame = ttk.Frame(form_frame)
        # Row 6: Posiciona a frame dos botões (que usa pack internamente)
        button_frame.grid(row=6, column=0, columnspan=2, pady=(5, 10))
        
        ttk.Button(button_frame, text="Voltar", command=self.on_back, bootstyle="secondary").pack(side=LEFT, padx=10)
        ttk.Button(button_frame, text="Entrar", command=self._handle_login, bootstyle="success").pack(side=LEFT, padx=10)
        
        # Carrega credenciais salvas APÓS a UI ser criada
        self._load_saved_credentials()

    def _load_saved_credentials(self):
        """Tenta carregar email e senha salvos para a VA atual."""
        va_key_saved, email_saved, password_saved, remember_me = load_credentials()
        
        if va_key_saved == self.va_key:
            if email_saved:
                 self.email_var.set(email_saved)
                 
            if remember_me:
                if password_saved:
                    self.password_var.set(password_saved)
                    
                self.remember_var.set(True)
                self.status_label.config(text="Credenciais salvas carregadas.", bootstyle="info")
            
    def _handle_login(self):
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        remember = self.remember_var.get()
        
        if not email or not password:
            self.status_label.config(text="Preencha todos os campos.", bootstyle="danger")
            return
            
        self.status_label.config(text="Verificando credenciais...", bootstyle="info")
        self.update() 

        # 1. Checagem de Login
        if not va_auth.check_login(self.va_key, email, password):
            self.status_label.config(text="Falha no login. Verifique e-mail/senha.", bootstyle="danger")
            delete_credentials(self.va_key, email) 
            return

        # 2. Verificação de Piloto Validado
        self.status_label.config(text="Login OK. Verificando status de piloto validado...", bootstyle="info")
        self.update()

        if not va_auth.is_pilot_validated(self.va_key, email):
            self.status_label.config(text="Login OK, mas piloto não está na lista de validados.", bootstyle="warning")
        else:
            self.status_label.config(text="Piloto Autenticado e Validado! Iniciando Monitor...", bootstyle="success")

        # 3. Gerenciamento de Credenciais
        if remember:
            save_credentials(self.va_key, email, password)
        else:
            delete_credentials(self.va_key, email)
            # Salva o email mesmo assim para a próxima tentativa (sem marcar remember_me)
            try:
                config = configparser.ConfigParser()
                config.read(CONFIG_FILE)
                if CONFIG_SECTION not in config:
                    config[CONFIG_SECTION] = {}
                config[CONFIG_SECTION]['pilot_email'] = email
                config[CONFIG_SECTION]['va_key_selected'] = self.va_key
                with open(CONFIG_FILE, 'w') as configfile:
                    config.write(configfile)
            except Exception as e:
                 print(f"Erro ao salvar email (sem remember): {e}")


        # Sucesso
        self.after(500, lambda: self.on_success(self.va_key, email))