# vps_ws_server.py - Roda na sua VPS (FINAL com JSON Debugging)
import asyncio
import websockets
import json
import os 
from datetime import datetime, timedelta

# =========================================================
# 1. CONFIGURAÇÃO GERAL
# =========================================================
HOST = "0.0.0.0"
PORT = 8765
PHP_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t.php"
FULL_JSON_SNAPSHOT_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t_full_payload.json" # NOVO CAMINHO

# Contadores globais
packets_received_count = 0
total_bytes_received = 0.0

# Variáveis de Estado
SERVER_START_TIME = None 
USERS = set()
WORST_CASE_RATE_MBH = 12.3 
CLIENT_FLIGHT_STATES = {} 

# =========================================================
# 2. FUNÇÕES AUXILIARES
# =========================================================

async def register(websocket):
    """Adiciona um novo cliente ao conjunto de usuários ativos."""
    USERS.add(websocket)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] NOVO CLIENTE CONECTADO: {websocket.remote_address}. Total: {len(USERS)}")

async def unregister(websocket):
    """Remove um cliente do conjunto de usuários ativos."""
    if websocket in USERS:
        USERS.remove(websocket)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] CLIENTE DESCONECTADO: {websocket.remote_address}. Total: {len(USERS)}")

def print_event(pilot_id: str, event_name: str, description: str):
    """Exibe o evento na tela do console com timestamp."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [EVENTO] Piloto {pilot_id}: {event_name} -> {description}")

def format_number(value, decimals):
    """Formata um número para string com separador de milhares."""
    if isinstance(value, (int, float)):
        if decimals == 0:
            return f"{value:,.0f}".replace(",", "_TEMP_").replace(".", ",").replace("_TEMP_", ".")
        else:
            return f"{value:,.{decimals}f}".replace(",", "_TEMP_").replace(".", ",").replace("_TEMP_", ".")
    return "N/A"

def generate_estimated_data_table(average_rate_mbh):
    """Gera a tabela HTML com a estimativa de consumo."""
    global WORST_CASE_RATE_MBH
    hours = [2, 4, 6, 8]
    rows_html = ""
    rate_to_use = average_rate_mbh if average_rate_mbh > 0 else WORST_CASE_RATE_MBH 
    for h in hours:
        estimated_mb = h * rate_to_use
        formatted_mb = format_number(estimated_mb, 2) 
        rows_html += f"""<tr class="stats-row"><td>{h} Horas</td><td class="stats-value">{formatted_mb} MB</td></tr>"""
    return rows_html

def update_php_monitor_file(data, received_count, total_bytes_received): 
    """Sobrescreve o arquivo HTML/PHP com os dados mais recentes, SEM REFRESH."""
    global SERVER_START_TIME 
    
    time_elapsed = datetime.now() - SERVER_START_TIME
    time_elapsed_hours = time_elapsed.total_seconds() / 3600
    
    if time_elapsed_hours > 0 and total_bytes_received > 0:
        total_mb_received = total_bytes_received / (1024 * 1024)
        average_rate_mbh = total_mb_received / time_elapsed_hours
    else: average_rate_mbh = 0.0

    pilot_id = data.get("pilot_id", "N/A")
    # NOVO: Coleta Lat/Lng e IDs de Rede
    lat = format_number(data.get("lat", 0.0), 4)
    lng = format_number(data.get("lng", 0.0), 4)
    vatsim_id = data.get("vatsim_id", "N/A")
    ivao_id = data.get("ivao_id", "N/A")

    alt_ind = format_number(data.get("alt_ind", 0), 0) + ' ft'
    vs = format_number(data.get("vs", 0), 0) + ' fpm'
    ias = format_number(data.get("ias", 0), 0) + ' kts'
    g_force = format_number(data.get("g_force", 1.00), 2) + ' G'
    total_fuel = format_number(data.get("total_fuel", 0), 0) + ' gal'
    eng_status = 'LIGADO (1)' if data.get("eng_combustion", 0) == 1 else 'DESLIGADO (0)'
    
    sent_count = data.get("packets_sent", 0)
    sent_mb = format_number(data.get("mb_sent", 0.0), 4) 
    received_mb = format_number(total_bytes_received / (1024 * 1024), 4) 
    
    alerts = data.get("alerts", {})
    beacon_alert_on = alerts.get("beacon_off_engine_on", 0) == 1
    alert_text = 'ALERTA ATIVO!' if beacon_alert_on else 'NORMAL'
    alert_class = 'alert-active' if beacon_alert_on else 'alert-normal'
    status_class = 'status-connected'

    estimated_table_rows = generate_estimated_data_table(average_rate_mbh)
    rate_status_text = format_number(average_rate_mbh, 4) + " MB/hora"

    php_content = f"""<?php
// Arquivo gerado em {datetime.now().isoformat()} pelo Servidor Python
?>
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Voo Skymetrics - Live Snapshot</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #2c3e50; color: #ecf0f1; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #34495e; padding: 25px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4); }}
        h1 {{ text-align: center; color: #e74c3c; border-bottom: 2px solid #e74c3c; padding-bottom: 10px; margin-bottom: 20px; }}
        .data-table {{ width: 100%; border-collapse: collapse; }}
        .data-table th, .data-table td {{ padding: 12px; border-bottom: 1px solid #7f8c8d; text-align: left; }}
        .data-table th {{ background-color: #2c3e50; color: #f1c40f; }}
        .data-value {{ font-weight: bold; text-align: right; font-size: 1.1em; }}
        .status-box {{ text-align: center; padding: 10px; border-radius: 4px; margin-bottom: 15px; font-weight: bold; }}
        .status-connected {{ background-color: #27ae60; }}
        .status-disconnected {{ background-color: #e74c3c; }}
        .alert-active {{ color: #e74c3c; font-weight: bold; }}
        .alert-normal {{ color: #2ecc71; }}
        .stats-row {{ background-color: #2c3e50; font-size: 0.9em; }}
        .stats-label {{ font-weight: normal; }}
        .stats-value {{ font-weight: bold; color: #f1c40f; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Monitor de Voo Skymetrics (Snapshot)</h1>
        
        <div id="status" class="status-box {status_class}">ULTIMA ATUALIZAÇÃO: {datetime.now().strftime('%H:%M:%S')}</div>

        <table class="data-table">
            <thead>
                <tr>
                    <th>Métrica</th>
                    <th>Valor Atual</th>
                </tr>
            </thead>
            <tbody>
                <tr><td>Pilot ID</td><td class="data-value">{pilot_id}</td></tr>
                <tr><td>VATSIM ID</td><td class="data-value">{vatsim_id}</td></tr>
                <tr><td>IVAO ID</td><td class="data-value">{ivao_id}</td></tr>
                <tr><td>Latitude</td><td class="data-value">{lat}</td></tr>
                <tr><td>Longitude</td><td class="data-value">{lng}</td></tr>
                
                <tr class="stats-row"><td class="stats-label">Pacotes Enviados (Cliente)</td><td class="stats-value">{sent_count}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Enviados (MB)</td><td class="stats-value">{sent_mb} MB</td></tr>
                
                <tr class="stats-row"><td class="stats-label">Pacotes Recebidos (Servidor)</td><td class="stats-value">{received_count}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Recebidos (MB)</td><td class="stats-value">{received_mb} MB</td></tr>
                
                <tr><td>Altitude Indicada (ft)</td><td class="data-value">{alt_ind}</td></tr>
                <tr><td>Velocidade Vertical (fpm)</td><td class="data-value">{vs}</td></tr>
                <tr><td>IAS (kts)</td><td class="data-value">{ias}</td></tr>
                <tr><td>G-Force (G)</td><td class="data-value">{g_force}</td></tr>
                <tr><td>Combustível Total (gal)</td><td class="data-value">{total_fuel}</td></tr>
                <tr><td>Status Motor</td><td class="data-value">{eng_status}</td></tr>
                <tr><td>**ALERTA: Beacon/Motor**</td><td class="data-value {alert_class}">{alert_text}</td></tr>
            </tbody>
        </table>

        <h2 style="margin-top: 30px; color: #f39c12; font-size: 1.2em;">Taxa Média de Uso: {rate_status_text}</h2>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Projeção</th>
                    <th>Consumo Estimado</th>
                </tr>
            </thead>
            <tbody>
                {estimated_table_rows}
            </tbody>
        </table>
        
        <p style="text-align: center; font-size: 0.8em; margin-top: 20px;">
            A página mostra o estado exato da aeronave no momento em que a página foi carregada. Para atualizar, pressione F5.
        </p>
    </div>
</body>
</html>
"""
    
    try:
        os.makedirs(os.path.dirname(PHP_FILE_PATH), exist_ok=True)
        with open(PHP_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(php_content)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO ESCREVER ARQUIVO PHP: {e}")

def write_full_json_snapshot(data, received_count, total_bytes_received):
    """Escreve um arquivo JSON completo com todos os dados recebidos para debugging."""
    global FULL_JSON_SNAPSHOT_PATH
    
    snapshot_data = data.copy()
    snapshot_data['server_snapshot_time'] = datetime.now().isoformat()
    snapshot_data['total_bytes_received'] = total_bytes_received
    snapshot_data['packets_received_count'] = received_count
    
    try:
        os.makedirs(os.path.dirname(FULL_JSON_SNAPSHOT_PATH), exist_ok=True)
        with open(FULL_JSON_SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
            json.dump(snapshot_data, f, indent=4)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO ESCREVER ARQUIVO JSON COMPLETO: {e}")

# =========================================================
# 3. HANDLER PRINCIPAL (Lógica de Estado do Voo)
# =========================================================
async def handle_flight_data(websocket): 
    """Recebe os dados do cliente Python e atualiza o arquivo PHP."""
    global packets_received_count, total_bytes_received, CLIENT_FLIGHT_STATES
    
    await register(websocket) 

    pilot_id = ""
    
    try:
        async for message in websocket:
            
            # --- 1. ATUALIZAÇÃO DOS CONTADORES DO SERVIDOR ---
            message_size = len(message.encode('utf-8'))
            total_bytes_received += message_size
            packets_received_count += 1 
            
            data = json.loads(message)
            pilot_id = str(data.get("pilot_id", "ANON"))
            
            # LOG CONCISO DE DEBUG
            altitude = data.get("alt_ind", 0); ias = data.get("ias", 0); vs = data.get('vs', 0); bank = data.get('plane_bank_degrees', 0)
            overspeed = data.get('alerts', {}).get('overspeed_warning', 0); stall = data.get('alerts', {}).get('stall_warning', 0)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [DADOS BRUTOS] Piloto: {pilot_id} | Alt: {altitude:.0f} ft | VS: {vs:.0f} fpm | IAS: {ias:.1f} kts | Bank: {bank:.1f} deg | ALERTS: OSPD={overspeed}, STALL={stall}")


            # --- INICIALIZAÇÃO E ATUALIZAÇÃO DE ESTADO (Eventos) ---
            if pilot_id not in CLIENT_FLIGHT_STATES:
                 CLIENT_FLIGHT_STATES[pilot_id] = {
                     'is_airborne': False, 'has_landed': True, 'initial_fuel_logged': False, 'landing_vs': None, 'last_vs': 0.0
                 }
            
            current_state = CLIENT_FLIGHT_STATES[pilot_id]
            
            # Detecção de Eventos (Decolagem, Pouso, Alertas)
            current_agl = data.get('agl', 0); current_ias = data.get('ias', 0); current_vs = data.get('vs', 0); current_on_ground = data.get('on_ground', 0); current_bank = data.get('plane_bank_degrees', 0)
            
            if (not current_state['is_airborne'] and current_agl > 50 and current_ias > 40):
                current_state['is_airborne'] = True; current_state['has_landed'] = False
                print_event(pilot_id, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.")

            # ... (Demais lógicas de eventos omitidas por brevidade) ...
            
            current_state['last_vs'] = current_vs 
            
            # 4. Atualiza o arquivo PHP (para visualização)
            update_php_monitor_file(data, packets_received_count, total_bytes_received)
            
            # 5. NOVO: Escreve o JSON de debugging completo (para inspeção)
            write_full_json_snapshot(data, packets_received_count, total_bytes_received)
            
    except Exception: pass 
    finally: await unregister(websocket)

# =========================================================
# 4. FUNÇÃO MAIN
# =========================================================
def create_initial_php_file():
    """Cria o arquivo PHP inicial com valores vazios e inicia o rastreamento de tempo."""
    global SERVER_START_TIME
    
    SERVER_START_TIME = datetime.now() 
    
    try:
        os.makedirs(os.path.dirname(PHP_FILE_PATH), exist_ok=True)
        # Passa um dicionário inicial com 0 bytes/pacotes
        update_php_monitor_file(
            {"alt_ind": 0, "vs": 0, "ias": 0, "g_force": 0, "total_fuel": 0, "eng_combustion": 0, "light_beacon_on": 0, "alerts": {}, "packets_sent": 0, "mb_sent": 0.0, "lat": 0.0, "lng": 0.0, "vatsim_id": "N/A", "ivao_id": "N/A"}, 
            0,
            0.0
        )
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCESSO: Arquivo PHP criado em: {PHP_FILE_PATH}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO CRIAR ARQUIVO PHP INICIAL: {e}")

async def main():
    """Função principal para iniciar o servidor."""
    
    create_initial_php_file()
    
    async with websockets.serve(handle_flight_data, HOST, PORT):
        print(f"*** Servidor WebSocket Skymetrics iniciado. Escutando em ws://{HOST}:{PORT} ***")
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServidor encerrado por Ctrl+C.")
