# Arquivo: client/radio_dsp.py

import numpy as np
from scipy import signal
import pyaudio 

# --- Constantes do Rádio de Aviação ---
# Frequências típicas de voz de rádio: 300 Hz a 3000 Hz (banda estreita)
LOW_CUT = 300
HIGH_CUT = 3800

# Amplitude do ruído branco. Ajuste este valor (0.005 a 0.05)
# para controlar a intensidade do chiado de fundo.
NOISE_LEVEL = 0.001

# Fator de escala para clipping/distorção (compressão AM). Valores menores distorcem mais.
CLIPPING_FACTOR = 0.03

# REVERTIDO: BASE_GAIN é o novo OUTPUT_GAIN fixo.
OUTPUT_GAIN = 8.0 

# Máximo valor de 16-bit para normalização
MAX_INT_16 = np.iinfo(np.int16).max

# Constantes para o limite de degradação
MAX_DEGRADATION_NOISE_LEVEL = 0.005  # Nível máximo de ruído para degradação total
MIN_VOICE_GAIN = 0.1                # Ganho mínimo da voz para degradação total (10% do original)


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
    Aplica os efeitos de rádio de aviação (filtro, ruído e clipping) com o OUTPUT_GAIN fixo.
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
    # APLICAÇÃO DO GANHO: Multiplica pelo OUTPUT_GAIN fixo
    audio_rescaled = audio_clipped * OUTPUT_GAIN * MAX_INT_16 
    
    # Garante que os valores estejam dentro do range de 16-bit
    audio_final = np.clip(audio_rescaled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)

    return audio_final.tobytes()

def apply_degradation(audio_data, sample_rate, degradation_factor):
    """
    Aplica degradação ajustando o ruído e o volume da voz, usando o OUTPUT_GAIN fixo.
    """
    if not audio_data:
        return audio_data

    # 1. Conversão e Normalização (bytes -> float32)
    audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    audio_norm = audio_np / MAX_INT_16
    
    # 2. FILTRAGEM DE VOZ: Aplica o corte de banda
    audio_filtered_voice = apply_bandpass_filter(audio_norm, sample_rate)
    
    # --- Lógica de Degradação Baseada no Fator (0.0 a 1.0) ---
    
    # A. Ajuste do Nível de Ruído (aumenta o ruído com o fator)
    current_noise_level = NOISE_LEVEL + (MAX_DEGRADATION_NOISE_LEVEL - NOISE_LEVEL) * degradation_factor
    
    # B. Ajuste do Ganho de Voz (reduz o ganho de voz com o fator)
    voice_gain_factor = 1.0 - (1.0 - MIN_VOICE_GAIN) * degradation_factor
    
    # Aplica o novo ganho na voz filtrada
    audio_filtered_voice *= voice_gain_factor 

    # 3. GERAÇÃO E FILTRAGEM DO RUÍDO:
    noise_raw = np.random.normal(0, current_noise_level, audio_filtered_voice.shape).astype(np.float32)
    noise_filtered = apply_bandpass_filter(noise_raw, sample_rate)
    
    # 4. SOMA: Combina a voz degradada com o ruído ajustado
    audio_with_noise = audio_filtered_voice + noise_filtered
    
    # 5. Clipping (Distorção AM) - Mantém para o efeito de rádio
    audio_clipped = np.clip(audio_with_noise, -CLIPPING_FACTOR, CLIPPING_FACTOR)
    
    # 6. Conversão de volta (float32 -> paInt16)
    # APLICAÇÃO DO GANHO FINAL: Usa o OUTPUT_GAIN fixo
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

def generate_squelch_tail_burst(chunk_size: int, sample_rate: int) -> bytes:
    """
    Gera um burst de ruído estático filtrado para o 'squelch tail' de rádio.
    """
    # 1. Cria uma base de silêncio para simular o buffer
    audio_norm = np.zeros(chunk_size, dtype=np.float32)
    
    # 2. GERAÇÃO E FILTRAGEM DO RUÍDO (Ruído 1.5x mais alto que o NOISE_LEVEL padrão para o burst)
    noise_raw = np.random.normal(0, NOISE_LEVEL * 10, audio_norm.shape).astype(np.float32)
    noise_filtered = apply_bandpass_filter(noise_raw, sample_rate)
    
    # 3. SOMA: Apenas o ruído, já que o audio_norm é zero
    audio_with_noise = audio_norm + noise_filtered
    
    # 4. Clipping (MANTIDO)
    audio_clipped = np.clip(audio_with_noise, -CLIPPING_FACTOR, CLIPPING_FACTOR)
    
    # 5. APLICAÇÃO DO GANHO
    audio_rescaled = audio_clipped * OUTPUT_GAIN * MAX_INT_16
    
    # 6. Conversão final
    audio_final = np.clip(audio_rescaled, np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype(np.int16)
    
    return audio_final.tobytes()