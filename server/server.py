# vps_ws_server.py - Roda na sua VPS (FINAL: Estrutura Corrigida e Funcional)
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
HTML_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t.php" # Arquivo estático (apenas para exibição)
JSON_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t.json" # Arquivo de dados (atualização em tempo real)

# Contadores globais
packets_received_count = 0
total_bytes_received = 0.0

# Variáveis de Estado
SERVER_START_TIME = None 
USERS = set()
WORST_CASE_RATE_MBH = 12.3 
CLIENT_FLIGHT_STATES = {} 
ALL_PILOT_SNAPSHOTS = {} 
LAST_JSON_UPDATE_TIME = datetime.min # CHAVE: Nova variável de controle de tempo

# =========================================================
# 2. FUNÇÕES AUXILIARES
# =========================================================

async def register(websocket):
    """Adiciona um novo cliente ao conjunto de usuários ativos."""
    USERS.add(websocket)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] NOVO CLIENTE CONECTADO: {websocket.remote_address}. Total: {len(USERS)}")

async def unregister(websocket):
    """Remove um cliente do conjunto de usuários ativos."""
    global ALL_PILOT_SNAPSHOTS
    
    if websocket in USERS:
        pilot_id = next((p_id for p_id, ws in websocket.pilot_info.items()), "ANON") if hasattr(websocket, 'pilot_info') else "ANON"
        if pilot_id in ALL_PILOT_SNAPSHOTS:
            del ALL_PILOT_SNAPSHOTS[pilot_id]
             
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

def generate_pilot_summary_rows():
    """Gera as linhas HTML para a tabela resumo de todos os pilotos ativos."""
    global ALL_PILOT_SNAPSHOTS
    rows_html = ""
    
    if not ALL_PILOT_SNAPSHOTS:
        return '<tr><td colspan="6" style="text-align:center; color: #95a5a6;">Nenhum voo ativo no momento.</td></tr>'

    for pilot_id, data in ALL_PILOT_SNAPSHOTS.items():
        # Dados essenciais para o resumo
        alt = format_number(data.get('alt_ind', 0), 0); vs = format_number(data.get('vs', 0), 0); ias = format_number(data.get('ias', 0), 0); vatsim = data.get('vatsim_id', 'N/A')
        
        # Lógica de Status Visual
        is_airborne = data.get('on_ground', 1) == 0 and data.get('alt_ind', 0) > 100
        is_taxiing = data.get('on_ground', 1) == 1 and data.get('ias', 0) > 5 and data.get('eng_combustion', 0) == 1
        is_cold = data.get('eng_combustion', 0) == 0
        
        if is_airborne: status_text = "EM VOO"; status_class = "status-airborne"
        elif is_taxiing: status_text = "TAXIANDO"; status_class = "status-taxiing"
        elif not is_cold: status_text = "EM SOLO (Engine On)"; status_class = "status-ready"
        else: status_text = "OFFLINE/COLD"; status_class = "status-cold"
             
        rows_html += f"""
                <tr class="pilot-row {status_class}">
                    <td class="pilot-id">{pilot_id}</td>
                    <td>V: {vatsim} / I: {data.get('ivao_id', 'N/A')}</td>
                    <td>{status_text}</td>
                    <td>{alt} ft</td>
                    <td>{vs} fpm</td>
                    <td>{ias} kts</td>
                </tr>"""
                 
    return rows_html


def generate_realtime_data_json(data, received_count, total_bytes_received):
    """Gera o arquivo JSON com os dados em tempo real para o frontend."""
    global SERVER_START_TIME, LAST_JSON_UPDATE_TIME

    # Verifica se já se passaram 60 segundos desde a última atualização
    time_since_last_update = datetime.now() - LAST_JSON_UPDATE_TIME
    if time_since_last_update < timedelta(seconds=60):
        # Se não, não atualiza o arquivo JSON e retorna
        return
    
    # Atualiza o timestamp da última escrita
    LAST_JSON_UPDATE_TIME = datetime.now()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [JSON_WRITE] Atualizando t.json para Lat/Lng.")


    time_elapsed = datetime.now() - SERVER_START_TIME
    time_elapsed_hours = time_elapsed.total_seconds() / 3600
    
    if time_elapsed_hours > 0 and total_bytes_received > 0:
        total_mb_received = total_bytes_received / (1024 * 1024)
        average_rate_mbh = total_mb_received / time_elapsed_hours
    else: average_rate_mbh = 0.0

    pilot_id = data.get("pilot_id", "N/A")
    
    json_data = {
        "timestamp": datetime.now().isoformat(),
        "pilot_id": pilot_id,
        "lat": data.get("lat", 0.0),
        "lng": data.get("lng", 0.0),
        "alt_ind": data.get("alt_ind", 0),
        "vs": data.get("vs", 0),
        "ias": data.get("ias", 0),
        "g_force": data.get("g_force", 1.0),
        "total_fuel": data.get("total_fuel", 0),
        "eng_combustion": data.get("eng_combustion", 0),
        "packets_received_count": received_count,
        "total_bytes_received_mb": total_bytes_received / (1024 * 1024),
        "average_rate_mbh": average_rate_mbh,
    }
    
    try:
        os.makedirs(os.path.dirname(JSON_FILE_PATH), exist_ok=True)
        with open(JSON_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(json_data, f)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO ESCREVER ARQUIVO JSON: {e}")


def update_monitor_files(data, received_count, total_bytes_received): 
    """Gera o HTML principal (estático) e o JSON (tempo real, se o tempo permitir)."""
    global SERVER_START_TIME, ALL_PILOT_SNAPSHOTS
    
    # 1. GERAÇÃO DO JSON (Controlada pelo tempo dentro da função)
    generate_realtime_data_json(data, received_count, total_bytes_received)


    # 2. GERAÇÃO DO HTML (Só atualiza a estrutura, estatísticas estáticas e o resumo da tabela)

    time_elapsed = datetime.now() - SERVER_START_TIME
    time_elapsed_hours = time_elapsed.total_seconds() / 3600
    
    if time_elapsed_hours > 0 and total_bytes_received > 0:
        total_mb_received = total_bytes_received / (1024 * 1024)
        average_rate_mbh = total_mb_received / time_elapsed_hours
    else: average_rate_mbh = 0.0

    rate_status_text = format_number(average_rate_mbh, 4) + " MB/hora"
    estimated_table_rows = generate_estimated_data_table(average_rate_mbh)
    pilot_summary_rows = generate_pilot_summary_rows() 
    
    received_mb = format_number(total_bytes_received / (1024 * 1024), 4) 
    sent_count = format_number(data.get("packets_sent", 0), 0)
    sent_mb = format_number(data.get("mb_sent", 0.0), 4) 

    html_content = f"""<?php
// Arquivo gerado em {datetime.now().isoformat()} pelo Servidor Python
// O mapa agora usa AJAX para ler t.json para dados em tempo real
?>
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Voos Ativos Skymetrics</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
    
    <style>
        /* Base e Fundo */
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1c1c1c; color: #e0e0e0; margin: 0; padding: 20px; }}
        .container {{ max-width: 900px; margin: 0 auto; background-color: #242424; padding: 30px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5); }}
        
        /* Títulos */
        h1 {{ text-align: center; color: #00bcd4; border-bottom: 2px solid #00bcd4; padding-bottom: 10px; margin-bottom: 25px; font-weight: 300; letter-spacing: 1px; }}
        h2 {{ color: #ff9800; font-size: 1.2em; border-bottom: 1px solid #ff980040; padding-bottom: 5px; margin-top: 30px; }}

        /* Tabelas */
        .data-table {{ width: 100%; border-collapse: collapse; margin-bottom: 30px; border-radius: 8px; overflow: hidden; }}
        .data-table th, .data-table td {{ padding: 14px; text-align: left; border-bottom: 1px solid #333; }}
        .data-table th {{ background-color: #383838; color: #ffffff; font-weight: 600; text-transform: uppercase; }}
        #map {{ height: 400px; width: 100%; border-radius: 8px; margin-top: 20px; }}
        /* Cores Dinâmicas */
        .pilot-row.status-airborne {{ background-color: #43a04730; color: #81c784; }} 
        .pilot-row.status-taxiing {{ background-color: #ffb30030; color: #ffb300; }} 
        .pilot-row.status-ready {{ background-color: #1e88e530; color: #64b5f6; }} 
        .pilot-row.status-cold {{ background-color: #333333; color: #999; }} 
    </style>
</head>
<body>
    <div class="container">
        <h1>Monitor de Voos Ativos Skymetrics</h1>
        
        <div id="status" class="status-box status-connected">ESTADO DO SERVIDOR: {datetime.now().strftime('%H:%M:%S')}</div>

        <h2>Resumo de Voos Ativos ({len(ALL_PILOT_SNAPSHOTS)} Piloto(s))</h2>
        <table class="data-table">
            <thead>
                <tr>
                    <th>ID Piloto</th>
                    <th>VATSIM / IVAO</th>
                    <th>Status Voo</th>
                    <th>Altitude</th>
                    <th>VS</th>
                    <th>IAS</th>
                </tr>
            </thead>
            <tbody>
                {pilot_summary_rows}
            </tbody>
        </table>

        <h2 style="margin-top: 30px;">Localização (Último Piloto Ativo)</h2>
        <div id="map"></div>
        
        <script>
            var map;
            var marker = null;
            var initialLat = 0.0;
            var initialLng = 0.0;
            
            // CHAVE: Usa a Fetch API para buscar o t.json
            const JSON_URL = 't.json';

            async function fetchInitialData() {{
                try {{
                    // Usa um timestamp para garantir que o primeiro dado lido seja o mais novo
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime()); 
                    const data = await response.json();
                    initialLat = data.lat;
                    initialLng = data.lng;
                    initMap();
                    // Inicia a atualização contínua do marcador a cada 2 segundos
                    setInterval(updateMarkerPosition, 2000); 
                }} catch (error) {{
                    console.error("Erro ao carregar dados iniciais do mapa:", error);
                    // Caso falhe, tenta inicializar com dados padrão
                    initMap();
                    // Continua tentando atualizar, caso o arquivo apareça depois
                    setInterval(updateMarkerPosition, 2000); 
                }}
            }}

            function initMap() {{ 
                
                if (!document.getElementById('map')) return; 

                if (map) {{ map.remove(); }}

                // Tenta centralizar em uma posição válida, caso contrário, usa 0,0
                var mapCenter = [initialLat || -23.5505, initialLng || -46.6333];
                map = L.map('map').setView(mapCenter, 10);
                
                // --- DEFINIÇÃO DE CAMADAS BASE ---
                var osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '© OpenStreetMap' }});
                var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{ maxZoom: 19, attribution: 'Tiles &copy; Esri' }});
                
                osm.addTo(map);

                var baseLayers = {{ "Estrada (OSM)": osm, "Satélite (Esri)": satellite }};
                L.control.layers(baseLayers).addTo(map);

                // O marcador inicial será criado ou movido na primeira chamada de updateMarkerPosition
                marker = L.marker(mapCenter).addTo(map)
                    .bindPopup('Aguardando dados...')
                    .openPopup();
            }}

            // FUNÇÃO CHAVE: Busca novos dados (Lat/Lng) do t.json e move o marcador
            async function updateMarkerPosition() {{
                try {{
                    // Adiciona um timestamp para evitar cache do navegador
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime());
                    const data = await response.json();

                    var newLatLng = L.latLng(data.lat, data.lng);

                    if (marker) {{
                        // Move o marcador
                        marker.setLatLng(newLatLng);
                        // Atualiza o popup
                        marker.getPopup().setContent(`<b>Piloto: ${{data.pilot_id}}</b><br>Alt: ${{data.alt_ind}} ft<br>IAS: ${{data.ias}} kts`);
                        
                        // Opcional: move o mapa para seguir o marcador (descomente se desejar)
                        // map.panTo(newLatLng);
                        
                        // Opcional: Atualiza o contador de pacotes recebidos
                        document.getElementById('pacotes-recebidos').textContent = data.packets_received_count;

                    }}

                }} catch (error) {{
                    // A maioria dos erros aqui é devido ao servidor ainda não ter criado o t.json ou problemas de rede.
                    console.warn("Aguardando dados em t.json ou erro de leitura.", error.message);
                }}
            }}

            window.onload = fetchInitialData;
        </script>
        
        <h2 style="margin-top: 30px;">Estatísticas de Tráfego Global</h2>
        <table class="data-table" style="max-width: 500px;">
            <tbody>
                <tr class="stats-row"><td class="stats-label">Pacotes Enviados (Cliente)</td><td class="stats-value">{sent_count}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Enviados (MB)</td><td class="stats-value">{sent_mb} MB</td></tr>
                <tr class="stats-row"><td class="stats-label">Pacotes Recebidos (Servidor)</td><td class="stats-value" id="pacotes-recebidos">{received_count}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Recebidos (MB)</td><td class="stats-value">{received_mb} MB</td></tr>
            </tbody>
        </table>

        <h2 style="margin-top: 30px;">Projeção de Consumo (Baseado na Taxa Atual: {rate_status_text})</h2>
        <table class="data-table" style="max-width: 400px;">
            <thead>
                <tr><th>Projeção</th><th>Consumo Estimado</th></tr>
            </thead>
            <tbody>{estimated_table_rows}</tbody>
        </table>
        
        <p style="text-align: center; font-size: 0.8em; margin-top: 20px; color: #95a5a6;">
            Dados do mapa atualizados em tempo real via t.json. O servidor atualiza o t.json a cada 60 segundos.
        </p>
    </div>
</body>
</html>
"""
    
    try:
        os.makedirs(os.path.dirname(HTML_FILE_PATH), exist_ok=True)
        with open(HTML_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(html_content)
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO ESCREVER ARQUIVO HTML: {e}")

# =========================================================
# 3. HANDLER PRINCIPAL (Lógica de Estado do Voo)
# =========================================================
async def handle_flight_data(websocket): 
    """Recebe os dados do cliente Python e atualiza os arquivos de monitoramento."""
    global packets_received_count, total_bytes_received, CLIENT_FLIGHT_STATES, ALL_PILOT_SNAPSHOTS
    
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
            
            websocket.pilot_info = {pilot_id: True}
            
            # LOG CONCISO DE DEBUG
            altitude = data.get("alt_ind", 0); ias = data.get("ias", 0); vs = data.get('vs', 0); bank = data.get('plane_bank_degrees', 0)
            overspeed = data.get('alerts', {}).get('overspeed_warning', 0); stall = data.get('alerts', {}).get('stall_warning', 0)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [DADOS BRUTOS] Piloto: {pilot_id} | Alt: {altitude:.0f} ft | VS: {vs:.0f} fpm | IAS: {ias:.1f} kts | Bank: {bank:.1f} deg | ALERTS: OSPD={overspeed}, STALL={stall}")


            # --- 2. INICIALIZAÇÃO E ATUALIZAÇÃO DE ESTADO ---
            if pilot_id not in CLIENT_FLIGHT_STATES:
                CLIENT_FLIGHT_STATES[pilot_id] = {
                    'is_airborne': False, 'has_landed': True, 'initial_fuel_logged': False, 'landing_vs': None, 'last_vs': 0.0
                }
            
            # CHAVE: Armazena o snapshot completo e atualizado para este piloto
            ALL_PILOT_SNAPSHOTS[pilot_id] = data 
            
            current_state = CLIENT_FLIGHT_STATES[pilot_id]
            
            # --- DETECÇÃO DE EVENTOS DE VOO ---
            current_agl = data.get('agl', 0); current_ias = data.get('ias', 0); current_vs = data.get('vs', 0); current_on_ground = data.get('on_ground', 0); current_bank = data.get('plane_bank_degrees', 0)
            
            # A. DECOLAGEM
            if (not current_state['is_airborne'] and current_agl > 50 and current_ias > 40):
                current_state['is_airborne'] = True; current_state['has_landed'] = False
                print_event(pilot_id, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.")

            # B. POUSO (Toque e Parada)
            if (current_state['is_airborne'] and current_on_ground == 1 and data.get('agl', 0) < 100 and not current_state['has_landed']):
                if current_state['landing_vs'] is None: current_state['landing_vs'] = current_state['last_vs']
                if current_ias < 10: 
                    current_state['has_landed'] = True; current_state['is_airborne'] = False
                    vs_no_toque = current_state.get('landing_vs', data.get('vs', 0))
                    print_event(pilot_id, "POUSO_FINALIZADO", f"Pouso concluído. VS no toque: {vs_no_toque:.0f} fpm")

            # C. COMBUSTÍVEL INICIAL
            if data.get('eng_combustion', 0) == 1 and not current_state['initial_fuel_logged']:
                print_event(pilot_id, "COMBUSTIVEL_INICIAL", f"Motor ligado. Combustível: {data.get('total_fuel', 0):,.0f} gal")
                current_state['initial_fuel_logged'] = True 
                    
            # D. ALERTA: BANK ANGLE (> 30°)
            if abs(current_bank) > 30:
                print_event(pilot_id, "ALERTA:BANK_ANGLE_HIGH", f"Ângulo de inclinação excessivo: {abs(current_bank):.1f} graus.")

            # E. ALERTA: STALL WARNING 
            if data.get('alerts', {}).get('stall_warning', 0) == 1:
                print_event(pilot_id, "ALERTA:STALL_WARNING", "Alerta de estol (stall warning) ativo.")

            # F. OUTROS ALERTAS
            if data.get('alerts', {}).get('beacon_off_engine_on', 0) == 1:
                print_event(pilot_id, "ALERTA:BEACON_OFF_ENGINE_ON", "Beacon Lights desligadas com o motor em funcionamento.")
            if data.get('alerts', {}).get('engine_fire', 0) == 1:
                print_event(pilot_id, "ALERTA:ENG_FIRE", "Incêndio detectado no Motor.")
            
            # G. POUSO RESET
            if current_state['has_landed'] and current_on_ground == 1 and current_ias > 50:
                current_state['is_airborne'] = False; current_state['has_landed'] = False; current_state['initial_fuel_logged'] = False; current_state['landing_vs'] = None
            
            current_state['last_vs'] = current_vs 
            
            # 4. Atualiza os arquivos
            update_monitor_files(data, packets_received_count, total_bytes_received)
            
    except Exception: pass 
    finally: await unregister(websocket)

# =========================================================
# 4. FUNÇÃO MAIN
# =========================================================
def create_initial_files():
    """Cria os arquivos iniciais HTML e JSON com valores vazios."""
    global SERVER_START_TIME, LAST_JSON_UPDATE_TIME
    
    SERVER_START_TIME = datetime.now() 
    # Define o tempo inicial de atualização JSON para garantir que a primeira escrita ocorra imediatamente
    LAST_JSON_UPDATE_TIME = datetime.min
    
    initial_data = {"alt_ind": 0, "vs": 0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
             "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, "engine_vibration_1": 0.0,
             "pilot_id": "N/A", "vatsim_id": "N/A", "ivao_id": "N/A", 
             "alerts": {"overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0,}, 
             "packets_sent": 0, "mb_sent": 0.0}
    
    try:
        # Força a criação inicial de ambos os arquivos
        update_monitor_files(initial_data, 0, 0.0)
        # O JSON será escrito na primeira vez, pois LAST_JSON_UPDATE_TIME é datetime.min
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SUCESSO: Arquivos HTML/JSON iniciais criados.")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ERRO AO CRIAR ARQUIVOS INICIAIS: {e}")

async def main():
    """Função principal para iniciar o servidor."""
    
    create_initial_files()
    
    async with websockets.serve(handle_flight_data, HOST, PORT):
        print(f"*** Servidor WebSocket Skymetrics iniciado. Escutando em ws://{HOST}:{PORT} ***")
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServidor encerrado por Ctrl+C.")