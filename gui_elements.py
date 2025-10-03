# gui_elements.py
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from typing import Callable
import va_auth

class VASwitcherFrame(ttk.Frame):
    """Tela inicial para escolher a VA."""
    def __init__(self, master, on_va_select: Callable[[str], None]):
        super().__init__(master, padding=30)
        self.on_va_select = on_va_select

        ttk.Label(self, text="Selecione sua VA", font=("TkDefaultFont", 14, "bold")).pack(pady=20)

        # KAFLT
        ttk.Button(
            self, 
            text="KAFLT (kafly.com.br)", 
            command=lambda: self._select_va("KAFLT"), 
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

    def _select_va(self, va_key):
        self.on_va_select(va_key)

class LoginFormFrame(ttk.Frame):
    """Tela de formulário de login para a VA escolhida."""
    def __init__(self, master, va_key: str, on_success: Callable[[str, str], None], on_back: Callable):
        super().__init__(master, padding=30)
        self.va_key = va_key
        self.on_success = on_success
        self.on_back = on_back
        
        self.email_var = ttk.StringVar()
        self.password_var = ttk.StringVar()

        ttk.Label(self, text=f"Login: {va_key}", font=("TkDefaultFont", 14, "bold")).pack(pady=10)
        
        # E-mail/Username
        ttk.Label(self, text="E-mail ou Username:").pack(pady=(10, 0), anchor='w')
        ttk.Entry(self, textvariable=self.email_var, width=40).pack(pady=5, ipady=3)
        
        # Senha
        ttk.Label(self, text="Senha:").pack(pady=(10, 0), anchor='w')
        ttk.Entry(self, textvariable=self.password_var, show="*", width=40).pack(pady=5, ipady=3)
        
        self.status_label = ttk.Label(self, text="", bootstyle="danger", font=("-size 9"))
        self.status_label.pack(pady=10)

        # Botões
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)

        ttk.Button(button_frame, text="Voltar", command=self.on_back, bootstyle="secondary").pack(side=LEFT, padx=5)
        ttk.Button(button_frame, text="Entrar", command=self._handle_login, bootstyle="success").pack(side=LEFT, padx=5)
        
    def _handle_login(self):
        email = self.email_var.get().strip()
        password = self.password_var.get().strip()
        
        if not email or not password:
            self.status_label.config(text="Preencha todos os campos.", bootstyle="danger")
            return
            
        self.status_label.config(text="Verificando credenciais...", bootstyle="info")
        self.update() 

        # 1. Checagem de Login
        if not va_auth.check_login(self.va_key, email, password):
            self.status_label.config(text="Falha no login. Verifique e-mail/senha.", bootstyle="danger")
            return

        # 2. Verificação de Piloto Validado
        self.status_label.config(text="Login OK. Verificando status de piloto validado...", bootstyle="info")
        self.update()

        if not va_auth.is_pilot_validated(self.va_key, email):
            self.status_label.config(text="Login OK, mas piloto não está na lista de validados.", bootstyle="warning")
            # Se for estritamente obrigatório ser validado para usar o monitor, você pode adicionar 'return' aqui.
            # Por enquanto, ele prossegue com o aviso.
            pass
            
        # Sucesso
        self.status_label.config(text="Piloto Autenticado e Validado! Iniciando Monitor...", bootstyle="success")
        self.after(500, lambda: self.on_success(self.va_key, email))