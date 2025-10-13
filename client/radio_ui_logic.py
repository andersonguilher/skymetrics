# Arquivo: client/radio_ui_logic.py

import pyaudio
import socketio
import threading
import keyboard
import time
import tkinter as tk
from tkinter import ttk, messagebox
import json
import os 
import numpy as np 
import sys # Para diagnósticos

# --- Importação e Verificação de Módulos (Inicialização Resiliente) ---
# Variáveis globais de controle
JOYSTICK_AVAILABLE = False
radio_dsp = None 
pygame = None

try:
    # 1. Importação do PyGame e inicialização
    import pygame
    from pygame import locals
    
    # 2. Importação do módulo local DSP (deve estar no diretório 'client/')
    import radio_dsp as dsp_module  # CORRIGIDO para importação direta
    radio_dsp = dsp_module 
    
    # 3. Inicialização de recursos (Pygame)
    pygame.init()
    
    # Tentativa de inicializar PyAudio (pode falhar se não houver drivers ou permissão)
    p_check = pyaudio.PyAudio()
    p_check.terminate() # Termina imediatamente após a checagem
         
    JOYSTICK_AVAILABLE = True
    print("[RÁDIO INFO] Módulos DSP, PyAudio e PyGame importados e inicializados com sucesso.")
    
except Exception as e:
    # Este bloco captura falhas no import (ModuleNotFoundError) ou na inicialização (pygame.init/pyaudio)
    print(f"[RÁDIO CRÍTICO] Falha na importação/inicialização do Rádio ({type(e).__name__}): {e}. O rádio não funcionará.")
    JOYSTICK_AVAILABLE = False


# --- CONSTANTES DE CONFIGURAÇÃO ---
CONFIG_FILE = 'client_config.json'
DEFAULT_SERVER_URL = 'http://www.kafly.com.br:3000' 
PTT_KEY_DEFAULT = 'space'

# --- Configurações de Áudio (Herdadas do projeto rádio) ---
CHUNK = 2048 # Alterado para máxima estabilidade
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 23000
MAX_INT_16 = np.iinfo(np.int16).max # Assumindo numpy está instalado

# NOVO: Constantes de Alcance (Replicando o Servidor)
MAX_RANGE_KM = 150.0 
MIN_RANGE_KM = 5.0

# --- FUNÇÕES AUXILIARES ---
def get_device_name_by_index(index, device_map):
    """Encontra o nome do dispositivo dado o índice."""
    if index is None: return None
    for name, idx in device_map.items():
        if idx == index:
            return name
    return None

# --- LÓGICA DE GERENCIAMENTO DE ÁUDIO ---

def get_audio_devices():
    """Lista todos os dispositivos de entrada e saída disponíveis."""
    try:
        p = pyaudio.PyAudio()
    except Exception:
        return {}, {} # Retorna vazio se PyAudio falhar
        
    input_devs = {}
    output_devs = {}
    
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get('maxInputChannels') > 0:
            input_devs[f"{info.get('name')} (Index {i})"] = i
        if info.get('maxOutputChannels') > 0:
            output_devs[f"{info.get('name')} (Index {i})"] = i
            
    p.terminate()
    return input_devs, output_devs

def load_config():
    """Carrega as configurações salvas do ficheiro JSON."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {}

def save_config(config_data):
    """Salva a URL, os índices dos dispositivos, a tecla PTT e o volume TX/RX e o Loopback."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)

# Função para calcular o fator de degradação localmente (replicando o servidor)
def calculate_loopback_factor(distance_km: float) -> float:
    """Calcula o fator de degradação (0.0 a 1.0) para a distância dada."""
    distance_km = max(0.0, float(distance_km))
    if distance_km <= MIN_RANGE_KM: return 0.0
    if distance_km >= MAX_RANGE_KM: return 1.0
    
    # Interpolação linear
    return (distance_km - MIN_RANGE_KM) / (MAX_RANGE_KM - MIN_RANGE_KM)

# --- CLASSES AUXILIARES (VolumeKnob e outras mantidas) ---

class VolumeKnob(tk.Canvas):
    # A classe Knob foi mantida como a lógica de UI mais complexa
    def __init__(self, master, var, command, min_val=0.0, max_val=2.0, size=70, *args, **kwargs):
        super().__init__(master, width=size, height=size, bg='#2C3E50', highlightthickness=0, *args, **kwargs)
        self.value = var
        self.command = command
        self.min_val = min_val
        self.max_val = max_val
        self.size = size
        self.center = size / 2
        self.radius = size / 2.5
        self.angle = 0 

        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        
        self._set_pointer_from_value(var.get())

    def _draw_knob(self):
        self.delete("all")
        
        self.create_oval(self.center - self.radius, self.center - self.radius,
                         self.center + self.radius, self.center + self.radius,
                         fill='#7F8C8D', outline='#34495E', width=1)
        
        x = self.center + self.radius * 0.7 * np.sin(np.deg2rad(self.angle))
        y = self.center - self.radius * 0.7 * np.cos(np.deg2rad(self.angle))
        
        self.create_line(self.center, self.center, x, y, fill='#E74C3C', width=3, tags="pointer")
        
        self.create_oval(self.center - 3, self.center - 3, self.center + 3, self.center + 3, fill='black')


    def _set_pointer_from_value(self, value):
        range_val = self.max_val - self.min_val
        normalized_value = (value - self.min_val) / range_val
        
        new_angle = 270 * normalized_value - 135
        self.angle = new_angle
        
        self._draw_knob()

    def _on_press(self, event):
        self.start_y = event.y
        self.start_angle = self.angle
        
    def _on_drag(self, event):
        dy = self.start_y - event.y
        angle_change = dy * 1.5
        new_angle = self.start_angle + angle_change
        
        new_angle = max(-135, min(135, new_angle))
        
        self.angle = new_angle
        
        normalized_value = (new_angle + 135) / 270
        range_val = self.max_val - self.min_val
        new_value = (normalized_value * range_val) + self.min_val
        
        self.value.set(new_value)
        self.command(new_value) 
        
        self._draw_knob()


# --- CLASSE PRINCIPAL DO CLIENTE RÁDIO ---

class RadioClient:
    def __init__(self):
        # Apenas inicializa PyAudio se a importação foi bem-sucedida
        if not JOYSTICK_AVAILABLE:
            self.p = None
            self.sio = socketio.Client() # SocketIO é necessário para a UI, mesmo que o áudio falhe
            return
            
        self.config = load_config()
        self.p = pyaudio.PyAudio()
        self.sio = socketio.Client()
        self.is_ptt_active = False
        # ALTERADO: Remove a dependência de 'last_freq' do config, pois a frequência inicial 
        # será enviada pelo monitor (com2_active do simulador).
        self.current_frequency = 'N/A' 
        self.stream_in = None
        self.stream_out = None
        self.joystick_thread = None
        self.is_listening_for_ptt = False
        self.current_joystick = None
        
        # Variáveis de volume/PTT
        self.mic_volume_factor = self.config.get('mic_volume_factor', 1.0)
        self.rx_volume_factor = self.config.get('rx_volume_factor', 1.0)
        self.ptt_key = self.config.get('ptt_key', PTT_KEY_DEFAULT)
        self.loopback_active = self.config.get('loopback_active', False)
        
        # Configuração para Loopback com Distância
        self.loopback_distance_km = self.config.get('loopback_distance_km', 0.0)
        
        # REMOVIDO: self.com_volume
        
        self.setup_socketio_events()
        
        # Pygame já foi inicializado no bloco try/except
        
    def setup_socketio_events(self):
        self.sio.on('connect', self._on_connect)
        self.sio.on('disconnect', self._on_disconnect)
        self.sio.on('broadcast_audio', self._on_broadcast_audio)
        self.sio.on('frequency_changed', self._on_frequency_changed)
    
    # REMOVIDO: update_com_volume

    # --- Métodos SocketIO ---

    def _on_connect(self):
        print("--- CONECTADO ao Servidor de Rádio ---")
        # REMOVIDO: Não emite mais a frequência inicial aqui. O ws_monitor fará isso com os dados do simulador.
        # self.sio.emit('change_frequency', self.current_frequency)
        if JOYSTICK_AVAILABLE:
            self.set_ptt_hotkeys(self.ptt_key, True)
            self.start_joystick_monitor()
        
    def _on_disconnect(self):
        print("--- DESCONECTADO do Servidor de Rádio ---")
        self.stop_transmission()
        if JOYSTICK_AVAILABLE:
            self.set_ptt_hotkeys(self.ptt_key, False)
            
    # NOVO MÉTODO: Envia a posição para o servidor de rádio
    def send_position(self, lat: float, lng: float):
        """Envia a posição atual para o servidor para cálculo de degradação."""
        if self.sio.connected and lat != 0.0 and lng != 0.0:
            try:
                self.sio.emit('update_position', {'lat': lat, 'lng': lng})
            except Exception:
                pass # Ignora erros de emissão

    # MODIFICAR: _on_broadcast_audio (Aplica o rx_volume_factor e degradação)
    def _on_broadcast_audio(self, data):
        if not JOYSTICK_AVAILABLE or self.p is None: return
        
        # NOVO: Recebe um objeto { audio: Buffer, factor: float }
        audio_data = data.get('audio')
        degradation_factor = data.get('factor', 0.0) 
        
        if not audio_data: return
        
        if self.stream_out and self.stream_out.is_active() and not self.is_ptt_active:
            try:
                processed_data = audio_data 
                
                # 1. APLICAÇÃO DE DEGRADAÇÃO BASEADA NA DISTÂNCIA (Recebida do Server)
                if degradation_factor > 0.0 and radio_dsp:
                     # O DSP aplica a degradação (ajustando voz e ruído) e o ganho final (OUTPUT_GAIN fixo)
                     processed_data = radio_dsp.apply_degradation(audio_data, RATE, degradation_factor)
                
                # 2. APLICA O VOLUME RX DO KNOB DA UI (Se houver degradação, é aplicado sobre o áudio degradado/reconstruído)
                if self.rx_volume_factor != 1.0 and hasattr(np, 'frombuffer'):
                    audio_np = np.frombuffer(processed_data, dtype=np.int16)
                    audio_np = (audio_np * self.rx_volume_factor).astype(np.int16)
                    processed_data = audio_np.tobytes()
                
                self.stream_out.write(processed_data)

            except Exception:
                pass

    def _on_frequency_changed(self, freq):
        self.current_frequency = freq
        # O MonitorFrame do Skymetrics não é atualizado diretamente aqui, apenas o estado interno.
        print(f"[RÁDIO] Frequência sintonizada: {self.current_frequency}")

    # --- Métodos de Conexão e Desconexão ---

    def connect(self):
        """Inicia a conexão Socket.IO e os streams de áudio."""
        server_url = self.config.get('server_url', DEFAULT_SERVER_URL)
        
        if JOYSTICK_AVAILABLE: # Apenas tenta streams se o áudio estiver disponível
            if not self.start_audio_streams():
                return
        
        if not self.sio.connected:
            threading.Thread(target=lambda: self.sio.connect(server_url, transports=['websocket', 'polling']), daemon=True).start()

    def disconnect(self):
        """Desconecta e para todos os processos."""
        self.stop_transmission()
        
        if JOYSTICK_AVAILABLE:
            self.stop_audio_streams()
            self.set_ptt_hotkeys(self.ptt_key, False)
        
        if self.sio.connected:
            self.sio.disconnect()
        
        if self.joystick_thread and self.joystick_thread.is_alive():
            pass

    def tune_frequency(self, new_freq_str):
        """Envia a nova frequência ao servidor (chamada pelo ws_monitor)."""
        new_freq = new_freq_str.strip()
        try:
            float(new_freq)
            if self.sio.connected:
                self.sio.emit('change_frequency', new_freq)
                self.current_frequency = new_freq
        except ValueError:
            print(f"[RÁDIO] Tentativa de sintonizar frequência inválida: {new_freq_str}")
        
    # --- Métodos de Áudio e Streaming ---

    def start_audio_streams(self):
        """Inicializa APENAS o stream de SAÍDA de PyAudio (RX)."""
        if not JOYSTICK_AVAILABLE or self.p is None: return False
        
        self.stop_audio_streams() 
        
        output_index = self.config.get('output_device_index')
        
        if output_index is not None:
            try:
                self.stream_out = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE, output=True, output_device_index=output_index)
                return True
            except Exception as e:
                print(f"[RÁDIO] ERRO ao iniciar Saída: {e}")
                return False
        return False

    def stop_audio_streams(self):
        """Fecha os streams de PyAudio."""
        if self.p is None: return
        for stream in [self.stream_in, self.stream_out]:
            if stream:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                    stream.close()
                except: pass
        self.stream_in = None
        self.stream_out = None
        
    def start_transmission_ptt(self):
        """Inicia a gravação e transmissão de áudio (chamado pelo keyboard/joystick hotkey)."""
        if not JOYSTICK_AVAILABLE or self.p is None: return
        
        if not self.sio.connected or self.is_ptt_active:
            return

        self.is_ptt_active = True
        
        input_index = self.config.get('input_device_index')
        if input_index is None:
            self.is_ptt_active = False
            return
            
        try:
            self.stream_in = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK, input_device_index=input_index)
            threading.Thread(target=self.transmit_audio, daemon=True).start()
        except Exception as e:
            print(f"[RÁDIO] ERRO ao iniciar Microfone: {e}")
            self.is_ptt_active = False

    def transmit_audio(self):
        """Loop para ler, aplicar DSP e ENVIAR. (AJUSTADO PARA LOOPBACK)"""
        if not radio_dsp: 
            self.stop_transmission()
            return
            
        while self.is_ptt_active and self.sio.connected:
            try:
                raw_audio_data = self.stream_in.read(CHUNK, exception_on_overflow=False)
                
                # 1. APLICAÇÃO DE GANHO DE MICROFONE
                if self.mic_volume_factor != 1.0:
                    audio_np = np.frombuffer(raw_audio_data, dtype=np.int16)
                    audio_np = (audio_np * self.mic_volume_factor).astype(np.int16)
                    raw_audio_data = audio_np.tobytes()
                
                # 2. PROCESSAMENTO DSP (filtro, ruído, clipping - Standard Radio Effect)
                # Usa o OUTPUT_GAIN fixo
                processed_audio_data = radio_dsp.apply_radio_effect(raw_audio_data, RATE)
                
                # 3. CONTROLE DE LOOPBACK (AJUSTADO)
                if self.loopback_active and self.stream_out and self.stream_out.is_active():
                    
                    # Calcula o fator de degradação com a distância virtual
                    degradation_factor = calculate_loopback_factor(self.loopback_distance_km)
                    
                    # Aplica a degradação e o volume RX do cliente
                    if radio_dsp:
                         # 1. Aplica a degradação (usa OUTPUT_GAIN fixo)
                         loopback_audio = radio_dsp.apply_degradation(raw_audio_data, RATE, degradation_factor)

                         # 2. Aplica o volume RX da UI por cima
                         if self.rx_volume_factor != 1.0 and hasattr(np, 'frombuffer'):
                             audio_np = np.frombuffer(loopback_audio, dtype=np.int16)
                             audio_np = (audio_np * self.rx_volume_factor).astype(np.int16)
                             loopback_audio = audio_np.tobytes()
                    else:
                        loopback_audio = raw_audio_data # Fallback

                    self.stream_out.write(loopback_audio) 
                
                # 4. Envia o chunk processado (volume 1.0) ao servidor
                self.sio.emit('audio_chunk', processed_audio_data)
                
            except Exception as e:
                # CORREÇÃO: Trata a exceção e limpa os streams antes de quebrar o loop.
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [RÁDIO CRÍTICO] Falha no thread de transmissão: {e}. Executando stop_transmission().")
                self.stop_transmission() 
                break
            
            time.sleep(CHUNK / RATE / 2) 

    def stop_transmission(self):
        """Para a gravação e transmissão de áudio (PTT desativado) e envia o squelch tail."""
        if not self.is_ptt_active:
            return
        
        # NOVO: SQUELCH TAIL (Chiado de Desligamento)
        if JOYSTICK_AVAILABLE and self.stream_out and self.stream_out.is_active() and radio_dsp:
            # O burst é 1 chunk longo (aprox. 89ms com CHUNK=2048, RATE=23000)
            try:
                # O módulo DSP precisa de CHUNK e RATE. Eles estão disponíveis globalmente aqui.
                # A função generate_squelch_tail_burst foi adicionada ao radio_dsp.py
                squelch_burst = radio_dsp.generate_squelch_tail_burst(CHUNK, RATE) 
                
                # Aplica o volume RX do knob da UI (se houver degradação, é aplicado sobre o áudio degradado/reconstruído)
                if self.rx_volume_factor != 1.0 and hasattr(np, 'frombuffer'):
                    audio_np = np.frombuffer(squelch_burst, dtype=np.int16)
                    audio_np = (audio_np * self.rx_volume_factor).astype(np.int16)
                    squelch_burst = audio_np.tobytes()

                self.stream_out.write(squelch_burst)
            except Exception as e:
                # Se falhar, continua o processo de desligamento
                print(f"[RÁDIO] Falha ao gerar/enviar Squelch Tail: {e}")

        self.is_ptt_active = False
        
        if self.stream_in:
            try:
                self.stream_in.stop_stream()
                self.stream_in.close()
            except: pass
            self.stream_in = None
            
    # --- Lógica PTT e Joystick (mantida) ---
    
    def set_ptt_hotkeys(self, key_name, register):
        """Registra/desregistra as hotkeys PTT (apenas para TECLADO)."""
        if not JOYSTICK_AVAILABLE: return
        
        if key_name.startswith('JOY_BUTTON_'):
            return True 
        
        start_func = self.start_transmission_ptt
        stop_func = self.stop_transmission
        
        if key_name and isinstance(key_name, str):
            try:
                keyboard.remove_hotkey(key_name)
            except:
                pass
                
            if register:
                keyboard.add_hotkey(key_name, start_func, suppress=True)
                keyboard.add_hotkey(key_name, stop_func, suppress=True, trigger_on_release=True)
                
    def start_joystick_monitor(self):
        """Inicia a thread de monitoramento de joystick se não estiver ativa."""
        if not JOYSTICK_AVAILABLE: return
        
        if (self.joystick_thread is None or not self.joystick_thread.is_alive()):
            self.joystick_thread = threading.Thread(target=self.joystick_monitor_loop, daemon=True)
            self.joystick_thread.start()

    def joystick_monitor_loop(self):
        """Thread dedicada para monitorar o estado do joystick (COMPLETO)."""
        if not JOYSTICK_AVAILABLE:
            return

        try:
            # 1. Inicialização e Busca do Joystick
            pygame.joystick.init()
            joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        except Exception:
            joysticks = []
            
        if not joysticks:
            self.current_joystick = None
            return

        self.current_joystick = joysticks[0]
        self.current_joystick.init()
        
        # Lógica de PTT e Captura no Loop
        while True:
            try:
                pygame.event.pump() # Permite ao Pygame processar eventos
                
                # A) Lógica de Captura (Prioridade)
                if self.is_listening_for_ptt:
                    for event in pygame.event.get():
                        if event.type == pygame.locals.JOYBUTTONDOWN:
                            if event.joy == self.current_joystick.get_id():
                                # Chama a função de finalização da captura no thread principal
                                tk.get_default_root().after(0, lambda: self._end_ptt_capture(f"JOY_BUTTON_{event.button}"))
                                return 

                # B) Lógica de Ativação PTT (Polling de Estado)
                elif self.ptt_key.startswith('JOY_BUTTON_'):
                    try:
                        target_button_index = int(self.ptt_key.split('_')[-1])
                    except ValueError:
                        time.sleep(0.01); continue # PTT Key mal formatada

                    button_state = self.current_joystick.get_button(target_button_index)

                    if button_state and not self.is_ptt_active:
                        tk.get_default_root().after(0, self.start_transmission_ptt) 
                    
                    elif not button_state and self.is_ptt_active:
                        tk.get_default_root().after(0, self.stop_transmission) 
                
                else:
                     pygame.event.get() # Limpa o buffer de eventos se PTT não for Joystick

            except Exception:
                break # Sai do loop em caso de erro no joystick

            time.sleep(0.01) # Poll rate (100 vezes por segundo)

        # Cleanup
        try:
            pygame.joystick.quit()
            pygame.quit()
        except Exception:
            pass
        self.current_joystick = None
        
    def _end_ptt_capture(self, captured_key: str | None):
        """Finaliza o modo de escuta e define a nova tecla (chamado do joystick/teclado)."""
        if not self.is_listening_for_ptt:
            return

        self.client.is_listening_for_ptt = False
        
        if captured_key:
            new_key = captured_key.lower()
        else:
            new_key = self.ptt_key

        # 1. Atualiza a configuração do cliente
        self.client.ptt_key = new_key
        self.client.config['ptt_key'] = new_key
        save_config(self.client.config)
        
        # 3. Re-registra os hotkeys
        self.client.set_ptt_hotkeys(new_key, True)
        
        # 4. Força a atualização da UI (o objeto da janela de configurações precisa ser atualizado)
        # É uma simulação da chamada que o objeto RadioConfigWindow faria.
        pass # A UI é atualizada diretamente pela janela de configurações, que está na thread principal.

        
    # --- Métodos de Configuração (Volume, PTT Key) ---
    
    def update_mic_volume_config(self, value):
        self.mic_volume_factor = float(value)
        self.config['mic_volume_factor'] = self.mic_volume_factor
        save_config(self.config) 

    def update_rx_volume_config(self, value):
        self.rx_volume_factor = float(value)
        self.config['rx_volume_factor'] = self.rx_volume_factor
        save_config(self.config) 
        
    def update_audio_streams(self):
        """Re-inicializa os streams após a mudança de dispositivos na config UI."""
        if not JOYSTICK_AVAILABLE: return
        
        self.start_audio_streams()
        self.set_ptt_hotkeys(self.ptt_key, False)
        self.set_ptt_hotkeys(self.ptt_key, True)
        
    # NOVO: Método de atualização da distância virtual
    def update_loopback_distance(self, value):
        self.loopback_distance_km = float(value)
        self.config['loopback_distance_km'] = self.loopback_distance_km
        save_config(self.config)


# --- CLASSE DA JANELA DE CONFIGURAÇÃO TKINTER (Mantida) ---

class RadioConfigWindow(tk.Toplevel):
    def __init__(self, master, client: RadioClient):
        print("[DIAG:WND] 1. Tentando criar TopLevel...") # Diagnóstico
        super().__init__(master)
        self.title("KAFly Comm Radio Configurações")
        self.client = client
        self.transient(master) # Mantém acima do master
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        if not JOYSTICK_AVAILABLE:
             messagebox.showerror("Erro de Dependência", "O cliente de rádio não pode ser configurado. PyAudio ou PyGame falharam ao carregar na inicialização.")
             self.destroy()
             return
        
        # Variáveis GUI
        self.input_var = tk.StringVar(self)
        self.output_var = tk.StringVar(self)
        self.ptt_key_var = tk.StringVar(self)
        self.loopback_active_var = tk.BooleanVar(self, value=client.loopback_active)
        self.mic_volume_var = tk.DoubleVar(self, value=client.mic_volume_factor)
        self.rx_volume_var = tk.DoubleVar(self, value=client.rx_volume_factor)
        
        # NOVO: Variável para Distância Virtual
        self.loopback_distance_var = tk.DoubleVar(self, value=client.loopback_distance_km)
        
        # Estilos (Usando o tema do Master)
        self.configure(bg=master.cget('bg'))
        
        print("[DIAG:WND] 2. Iniciando carregamento de dispositivos...") # Diagnóstico
        self._load_and_setup_devices()
        
        print("[DIAG:WND] 3. Criando widgets e botões...") # Diagnóstico
        self._create_widgets()
        
        print("[DIAG:WND] 4. Janela criada com sucesso.") # Diagnóstico


    def _load_and_setup_devices(self):
        """Carrega e define as variáveis com os dispositivos e configurações atuais."""
        # Se PyAudio falhar aqui, o programa travará.
        self.input_devices, self.output_devices = get_audio_devices()
        
        # Tratamento de erro se nenhum dispositivo for encontrado
        if not self.input_devices or not self.output_devices:
             messagebox.showerror("Erro de Áudio", "Nenhum dispositivo de áudio encontrado. Verifique a instalação do PyAudio.")
             self.destroy()
             return

        # Definições iniciais de variáveis
        self.input_var.set(get_device_name_by_index(self.client.config.get('input_device_index'), self.input_devices) or list(self.input_devices.keys())[0])
        self.output_var.set(get_device_name_by_index(self.client.config.get('output_device_index'), self.output_devices) or list(self.output_devices.keys())[0])
        self.ptt_key_var.set(self.client.ptt_key.upper())


    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding=15)
        main_frame.pack(fill='both', expand=True)

        # --- Seção 1: Áudio I/O ---
        ttk.Label(main_frame, text="Dispositivos de Áudio", font=('TkDefaultFont', 12, 'bold')).pack(anchor='w', pady=(0, 5))
        
        audio_frame = ttk.Frame(main_frame)
        audio_frame.pack(fill='x', pady=5)
        
        ttk.Label(audio_frame, text="Microfone:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        ttk.OptionMenu(audio_frame, self.input_var, self.input_var.get(), *self.input_devices.keys(), command=self._on_device_change).grid(row=0, column=1, padx=5, sticky='ew')
        
        ttk.Label(audio_frame, text="Alto-falante:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        ttk.OptionMenu(audio_frame, self.output_var, self.output_var.get(), *self.output_devices.keys(), command=self._on_device_change).grid(row=1, column=1, padx=5, sticky='ew')

        audio_frame.columnconfigure(1, weight=1)
        
        ttk.Separator(main_frame).pack(fill='x', pady=10)

        # --- Seção 2: Volume e Loopback (MODIFICADA) ---
        ttk.Label(main_frame, text="Controles de Volume e PTT", font=('TkDefaultFont', 12, 'bold')).pack(anchor='w', pady=(0, 5))
        
        volume_frame = ttk.Frame(main_frame)
        volume_frame.pack(fill='x', pady=10)
        
        # Ganho TX
        mic_label = ttk.Label(volume_frame, text="Ganho TX:")
        mic_label.grid(row=0, column=0, sticky='w', padx=10)
        VolumeKnob(volume_frame, self.mic_volume_var, self._on_mic_volume_change, size=60).grid(row=1, column=0, padx=10, pady=5)
        
        # Volume RX
        rx_label = ttk.Label(volume_frame, text="Volume RX (UI):")
        rx_label.grid(row=0, column=1, sticky='w', padx=10)
        VolumeKnob(volume_frame, self.rx_volume_var, self._on_rx_volume_change, size=60).grid(row=1, column=1, padx=10, pady=5)
        
        # Loopback Checkbutton
        ttk.Checkbutton(volume_frame, text="Monitorar Voz (Loopback)", variable=self.loopback_active_var, command=self._on_loopback_change).grid(row=2, column=0, columnspan=2, pady=(10, 5), sticky='w')

        # NOVO: Controle de Distância Virtual para Loopback
        ttk.Label(volume_frame, text=f"Distância Virtual (km, Max: {MAX_RANGE_KM:.0f}):", anchor='w').grid(row=3, column=0, padx=5, pady=(5,0), sticky='w')
        ttk.Scale(volume_frame, from_=0, to=MAX_RANGE_KM, orient='horizontal', variable=self.loopback_distance_var, command=self._on_loopback_distance_change, length=150, value=self.loopback_distance_var.get()).grid(row=4, column=0, padx=5, sticky='ew')
        self.distance_value_label = ttk.Label(volume_frame, textvariable=self.loopback_distance_var, width=5)
        self.distance_value_label.grid(row=4, column=1, padx=5, sticky='w')
        self.loopback_distance_var.trace_add("write", lambda *args: self.distance_value_label.config(text=f"{self.loopback_distance_var.get():.1f} km")) # Atualiza label em tempo real
        
        volume_frame.columnconfigure(1, weight=1)

        ttk.Separator(main_frame).pack(fill='x', pady=10)

        # --- Seção 3: PTT ---
        
        ttk.Label(main_frame, text="Tecla PTT Atual:").pack(anchor='w', pady=(0, 5))
        
        ptt_ctrl_frame = ttk.Frame(main_frame)
        ptt_ctrl_frame.pack(fill='x', pady=5)
        
        self.ptt_entry = ttk.Entry(ptt_ctrl_frame, textvariable=self.ptt_key_var, width=15, state='readonly')
        self.ptt_entry.pack(side=tk.LEFT, padx=5)
        
        self.capture_button = ttk.Button(ptt_ctrl_frame, text="Capturar", command=self._start_ptt_capture)
        self.capture_button.pack(side=tk.LEFT, padx=5)


    # --- Callbacks ---

    def _on_device_change(self, selected_device):
        """Atualiza a config do cliente e notifica o RadioClient para reiniciar streams."""
        
        # 1. Atualiza IDs na Config do Cliente
        input_index = self.input_devices[self.input_var.get()]
        output_index = self.output_devices[self.output_var.get()]
        
        self.client.config['input_device_index'] = input_index
        self.client.config['output_device_index'] = output_index
        save_config(self.client.config)
        
        # 2. Re-inicializa os streams de áudio no cliente
        self.client.update_audio_streams()


    def _on_mic_volume_change(self, value):
        self.client.update_mic_volume_config(value)

    def _on_rx_volume_change(self, value):
        self.client.update_rx_volume_config(value)
        
    def _on_loopback_change(self):
        self.client.config['loopback_active'] = self.loopback_active_var.get()
        self.client.loopback_active = self.loopback_active_var.get()
        save_config(self.client.config)

    def _on_loopback_distance_change(self, value):
        """Callback para o ajuste da escala de distância."""
        self.client.update_loopback_distance(value)
        
    # --- PTT Capture Logic (mantida) ---
    def _start_ptt_capture(self):
        # Desativa os hotkeys globais existentes (se for teclado)
        self.client.set_ptt_hotkeys(self.client.ptt_key, False)
        
        # Prepara a UI
        self.ptt_entry.config(state='normal')
        self.ptt_entry.delete(0, tk.END)
        self.ptt_entry.insert(0, "Aguardando...")
        self.capture_button.config(state=tk.DISABLED)
        self.client.is_listening_for_ptt = True
        
        # Inicia a escuta de eventos (teclado e joystick)
        keyboard.hook(self._on_key_capture)
        # O timer é usado como fallback caso nenhum botão seja apertado
        self.after(10000, lambda: self._end_ptt_capture(None))

    def _on_key_capture(self, event):
        if not self.client.is_listening_for_ptt or event.event_type != keyboard.KEY_DOWN:
            return

        captured_key = event.name.lower()
        if captured_key in ['ctrl', 'alt', 'shift', 'left alt', 'right alt']:
            return

        keyboard.unhook_all()
        self._end_ptt_capture(captured_key)

    def _end_ptt_capture(self, captured_key):
        if not self.client.is_listening_for_ptt:
            return

        self.client.is_listening_for_ptt = False
        
        if captured_key:
            new_key = captured_key.lower()
        else:
            new_key = self.ptt_key

        # 1. Atualiza a configuração do cliente
        self.client.ptt_key = new_key
        self.client.config['ptt_key'] = new_key
        save_config(self.client.config)
        
        # 3. Re-registra os hotkeys
        self.client.set_ptt_hotkeys(new_key, True)
        
        # 4. Força a atualização da UI (o objeto da janela de configurações precisa ser atualizado)
        # É uma simulação da chamada que o objeto RadioConfigWindow faria.
        pass # A UI é atualizada diretamente pela janela de configurações, que está na thread principal.


    def _on_closing(self):
        """Salva a configuração final e fecha a janela."""
        self.client.config['loopback_active'] = self.loopback_active_var.get()
        save_config(self.client.config)
        self.client.update_audio_streams() # Re-inicia os streams com novos settings, se for o caso
        self.destroy()