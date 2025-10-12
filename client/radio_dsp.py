import numpy as np
from scipy import signal
import pyaudio 

# --- Constantes do Rádio de Aviação ---
# Frequências típicas de voz de rádio: 300 Hz a 3000 Hz (banda estreita)
LOW_CUT = 300
HIGH_CUT = 3000

# Amplitude do ruído branco. Ajuste este valor (0.005 a 0.05)
# para controlar a intensidade do chiado de fundo.
NOISE_LEVEL = 0.015 # MANTIDO: Conforme solicitado

# Fator de escala para clipping/distorção (compressão AM). Valores menores distorcem mais.
CLIPPING_FACTOR = 0.92 # MANTIDO: Último valor para manter a fonia da aviação

# NOVO: Ganho de saída para aumentar o volume geral (50% mais alto)
OUTPUT_GAIN = 8.0 

# Máximo valor de 16-bit para normalização
MAX_INT_16 = np.iinfo(np.int16).max


def apply_bandpass_filter(data_np, sample_rate):
    """Aplica o filtro passa-banda de 300-3000 Hz."""
    nyquist = 0.5 * sample_rate
    low = LOW_CUT / nyquist
    high = HIGH_CUT / nyquist
    
    # Ordem do filtro (define a inclinação do corte)
    order = 7 
    
    try:
        # Filtro Butterworth (btype='band' para corte de banda)
        b, a = signal.butter(order, [low, high], btype='band', analog=False)
        return signal.lfilter(b, a, data_np)
    except ValueError:
        # Retorna o áudio original se o filtro falhar (ocorre com taxas de amostragem incomuns)
        return data_np


def apply_radio_effect(audio_data, sample_rate):
    """
    Aplica os efeitos de rádio de aviação (filtro, ruído e clipping).
    O ruído é filtrado para remover agudos.
    """
    if not audio_data:
        return audio_data

    # 1. Conversão e Normalização (bytes -> float32)
    audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    audio_norm = audio_np / MAX_INT_16
    
    # 2. FILTRAGEM DE VOZ: Aplica o corte de banda para o som metálico/abafado
    audio_filtered_voice = apply_bandpass_filter(audio_norm, sample_rate)
    
    # 3. GERAÇÃO E FILTRAGEM DO RUÍDO:
    # Gera ruído branco
    noise_raw = np.random.normal(0, NOISE_LEVEL, audio_filtered_voice.shape).astype(np.float32)
    
    # FILTRAGEM DO RUÍDO: Aplica o mesmo filtro para remover agudos excessivos do chiado.
    noise_filtered = apply_bandpass_filter(noise_raw, sample_rate)
    
    # 4. SOMA: Combina a voz filtrada com o ruído filtrado
    audio_with_noise = audio_filtered_voice + noise_filtered
    
    # 5. Clipping (Distorção AM) - Simula a compressão do transmissor
    audio_clipped = np.clip(audio_with_noise, -CLIPPING_FACTOR, CLIPPING_FACTOR)
    
    # 6. Conversão de volta (float32 -> paInt16)
    # APLICAÇÃO DO GANHO: Multiplica por 1.5 para aumentar o volume geral
    audio_rescaled = audio_clipped * OUTPUT_GAIN * MAX_INT_16 
    
    # Garante que os valores estejam dentro do range de 16-bit
    audio_final = np.clip(audio_rescaled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)

    return audio_final.tobytes()


def add_static_noise_only(audio_data, sample_rate):
    """
    Função auxiliar para adicionar apenas ruído (pode ser usada se o servidor retransmitir
    silêncio, ou para simular o chiado do rádio quando não há transmissão).
    """
    if not audio_data:
        return audio_data

    audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    audio_norm = audio_np / MAX_INT_16

    # Adiciona ruído filtrado
    noise_raw = np.random.normal(0, NOISE_LEVEL * 1.5, audio_norm.shape).astype(np.float32)
    noise_filtered = apply_bandpass_filter(noise_raw, sample_rate)
    
    audio_with_noise = audio_norm + noise_filtered
    
    # Clipping (MANTIDO)
    audio_clipped = np.clip(audio_with_noise, -CLIPPING_FACTOR, CLIPPING_FACTOR)
    
    # APLICAÇÃO DO GANHO
    audio_rescaled = audio_clipped * OUTPUT_GAIN * MAX_INT_16
    
    audio_final = np.clip(audio_rescaled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)
    
    return audio_final.tobytes()