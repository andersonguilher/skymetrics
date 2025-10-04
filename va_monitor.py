# va_monitor.py (Arquivo principal de inicialização)
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import gui_elements
from sim_data_monitor import AircraftMonitorApp # Importa o Monitor de Voo

class MainApplication(ttk.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("Monitor de Voo VA")
        self.geometry("400x300") 
        self.resizable(False, False)
        
        self.current_frame = None
        self.va_key_selected = None
        self.pilot_email = None
        
        # --- NOVO: Lógica de Auto-Login ---
        va_key, email, _, remember_me = gui_elements.load_credentials()
        
        if remember_me and va_key and email:
            # Auto-login: Vai direto para o monitor se as credenciais estiverem salvas.
            self.va_key_selected = va_key
            self.pilot_email = email
            # Usa 'after' para garantir que a janela Tkinter esteja totalmente inicializada
            self.after(100, self._show_flight_monitor)
        else:
            # Inicia na seleção da VA
            self._show_va_selection()
        # --- Fim da Lógica de Auto-Login ---
        
        # Adiciona o protocolo de fechamento na janela principal para limpar SimConnect
        self.protocol("WM_DELETE_WINDOW", self.on_app_closing)

    def _clear_frame(self):
        """Remove o frame atual."""
        if self.current_frame:
            self.current_frame.destroy()
            self.current_frame = None

    def _show_va_selection(self):
        """Exibe a tela de seleção da VA."""
        self._clear_frame()
        self.geometry("400x300")
        self.current_frame = gui_elements.VASwitcherFrame(self, self._on_va_selected)
        self.current_frame.pack(fill=BOTH, expand=YES)

    def _on_va_selected(self, va_key: str):
        """Callback após a seleção da VA. Avança para o Login."""
        self.va_key_selected = va_key
        self._show_login_form()

    def _show_login_form(self):
        """Exibe a tela de login."""
        self._clear_frame()
        self.geometry("450x450")
        self.current_frame = gui_elements.LoginFormFrame(
            self, 
            self.va_key_selected, 
            self._on_login_success,
            self._show_va_selection # Volta para a seleção da VA
        )
        self.current_frame.pack(fill=BOTH, expand=YES)
        
    def _on_login_success(self, va_key: str, email: str):
        """Callback após o login e validação. Abre o Monitor."""
        self.va_key_selected = va_key
        self.pilot_email = email
        self._show_flight_monitor()

    def _show_flight_monitor(self):
        """Exibe a tela de monitoramento de voo."""
        self._clear_frame()
        self.geometry("300x550") 
        
        # Instancia o monitor de voo, passando a si mesmo (self) como master.
        self.monitor = AircraftMonitorApp(self, self.va_key_selected, self.pilot_email) 
        self.title(f"Monitor de Dados de Aeronave ({self.va_key_selected} - {self.pilot_email})")
        
        # NOVO: Vincula F12 à função de alternar a janela de alertas
        self.bind("<F12>", lambda event: self.monitor.toggle_alerts_window())

    def on_app_closing(self):
        """Função chamada ao fechar a janela. Limpa o SimConnect/Thread do monitor."""
        if hasattr(self, 'monitor'):
            # Chama o método de limpeza do monitor. Se for fechamento manual, 
            # monitor.on_closing() destrói a janela principal.
            self.monitor.on_closing() 

        # REMOÇÃO DA DESTRUIÇÃO REDUNDANTE: Não há necessidade de chamar self.destroy()
        # aqui, pois ela já foi chamada pelo monitor.on_closing() no fechamento manual.
        pass

if __name__ == "__main__":
    app = MainApplication()
    app.mainloop()