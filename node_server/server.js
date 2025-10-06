// server.js - Roda na sua VPS (Replicação da Lógica Python)
import { WebSocketServer } from 'ws';
import { createServer } from 'http';
import * as fs from 'fs/promises'; // Usaremos promises para operações de arquivo
import * as path from 'path';
import axios from 'axios';
import { fileURLToPath } from 'url';

// ---------------------------------------------------------
// 1. CONFIGURAÇÃO GERAL
// ---------------------------------------------------------
const HOST = "0.0.0.0";
const PORT = 8765;
// *** CAMINHOS E NOMES DE ARQUIVOS DEFINITIVOS ***
const HTML_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/skymetrics/index.php";
const JSON_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/skymetrics/whazzup.json";
// **********************************************************

// Contadores globais
let packetsReceivedCount = 0;
let totalBytesReceived = 0.0;

// Variáveis de Estado
let SERVER_START_TIME = null;
const USERS = new Set(); // Conjunto de conexões WebSocket
const WORST_CASE_RATE_MBH = 12.3;
const CLIENT_FLIGHT_STATES = {}; // {pilot_id: {is_airborne: bool, ...}}
const ALL_PILOT_SNAPSHOTS = {}; // {pilot_id: {data_completa}}
let LAST_JSON_UPDATE_TIME = new Date(0); // CHAVE: Tempo da última atualização do JSON (equivalente a datetime.min)

// Variáveis para verificação de rede (Controle do Servidor)
const IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup";
const VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json";
const NETWORK_CHECK_INTERVAL_SERVER = 120 * 1000; // milissegundos (2 minutos)
let LAST_GLOBAL_NETWORK_CHECK_TIME = 0.0;
// Centralizado: {pilot_id: {'websocket': ws, 'vatsim_id': id, 'ivao_id': id, 'tx_sent': bool}}
const PILOT_CONNECTIONS = {};


// ---------------------------------------------------------
// 2. FUNÇÕES AUXILIARES
// ---------------------------------------------------------

/**
 * Retorna o timestamp formatado (HH:MM:SS).
 * @returns {string}
 */
const getTimestamp = () => new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

/**
 * Adiciona um novo cliente ao conjunto de usuários ativos (sem ID ainda).
 * @param {import('ws')} ws 
 */
function register(ws) {
    USERS.add(ws);
    // Atribui propriedades customizadas ao objeto WebSocket (como no Python)
    ws.pilot_id = "ANON";
    ws.vatsim_id = "N/A";
    ws.ivao_id = "N/A";
    console.log(`[${getTimestamp()}] NOVO CLIENTE CONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}

/**
 * Remove um cliente do conjunto de usuários ativos.
 * @param {import('ws')} ws 
 */
async function unregister(ws) {
    if (!USERS.has(ws)) return;

    // Tenta obter o ID do objeto websocket
    const pilot_id = ws.pilot_id || "ANON";

    // Remove o piloto dos snapshots e conexões controladas
    if (pilot_id !== "ANON") {
        if (ALL_PILOT_SNAPSHOTS[pilot_id]) {
            delete ALL_PILOT_SNAPSHOTS[pilot_id];
        }
        if (PILOT_CONNECTIONS[pilot_id]) {
            delete PILOT_CONNECTIONS[pilot_id];
        }
    }

    USERS.delete(ws);

    // CHAVE: Forçar a atualização dos arquivos de monitoramento após a desconexão
    let data_to_update = { alt_ind: 0, vs: 0, ias: 0, eng_combustion: 0, vatsim_id: "N/A", ivao_id: "N/A", pilot_id: "N/A", packets_sent: 0, mb_sent: 0.0 };
    if (Object.keys(ALL_PILOT_SNAPSHOTS).length > 0) {
        // Pega um snapshot válido para forçar a atualização da lista de pilotos
        data_to_update = Object.values(ALL_PILOT_SNAPSHOTS)[0];
    }

    await updateMonitorFiles(data_to_update, packetsReceivedCount, totalBytesReceived);
    console.log(`[${getTimestamp()}] CLIENTE DESCONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}


/**
 * Exibe o evento na tela do console com timestamp.
 * @param {string} pilot_id 
 * @param {string} event_name 
 * @param {string} description 
 */
function printEvent(pilot_id, event_name, description) {
    console.log(`[${getTimestamp()}] [EVENTO] Piloto ${pilot_id}: ${event_name} -> ${description}`);
}

/**
 * Formata um número para string com separador de milhares (ponto como milhar, vírgula como decimal - Padrão Brasileiro)
 * @param {number} value
 * @param {number} decimals
 * @returns {string}
 */
function formatNumber(value, decimals) {
    if (typeof value !== 'number') return "N/A";

    const options = { minimumFractionDigits: decimals, maximumFractionDigits: decimals };
    // Usamos o locale 'pt-BR' para garantir o formato correto (1.000,00)
    return value.toLocaleString('pt-BR', options);
}

// --- Lógica de Verificação de Status Online na IVAO/VATSIM ---

/**
 * Verifica se o piloto (pelo IVAO ID) está ativo na rede IVAO.
 * @param {string} ivao_id
 * @returns {Promise<boolean>}
 */
async function isPilotOnlineIVAO(ivao_id) {
    if (!ivao_id || ivao_id.trim() === 'N/A' || ivao_id.trim() === '' || ivao_id.trim() === '0') return false;
    const ivao_id_int = parseInt(ivao_id.trim());
    if (isNaN(ivao_id_int)) return false;

    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000 });
        const data = response.data;

        for (const client of data.clients.pilots) {
            if (client.userId === ivao_id_int) {
                return true;
            }
        }
        return false;
    } catch (e) {
        // console.log(`[${getTimestamp()}] [SERVER CHECK] ERRO ao verificar IVAO: ${e.message}`);
        return false;
    }
}

/**
 * Verifica se o piloto (pelo VATSIM ID) está ativo na rede VATSIM.
 * @param {string} vatsim_id
 * @returns {Promise<boolean>}
 */
async function isPilotOnlineVATSIM(vatsim_id) {
    if (!vatsim_id || vatsim_id.trim() === 'N/A' || vatsim_id.trim() === '' || vatsim_id.trim() === '0') return false;
    const vatsim_id_int = parseInt(vatsim_id.trim());
    if (isNaN(vatsim_id_int)) return false;

    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000 });
        const data = response.data;

        for (const pilot of data.pilots) {
            if (pilot.cid === vatsim_id_int) {
                return true;
            }
        }
        return false;
    } catch (e) {
        // console.log(`[${getTimestamp()}] [SERVER CHECK] ERRO ao verificar VATSIM: ${e.message}`);
        return false;
    }
}

/**
 * Verifica se o piloto está ativo em pelo menos uma das redes (IVAO ou VATSIM).
 * @param {string} vatsim_id
 * @param {string} ivao_id
 * @returns {Promise<boolean>}
 */
async function checkNetworkStatus(vatsim_id, ivao_id) {
    const isVatsimOnline = await isPilotOnlineVATSIM(vatsim_id);
    const isIvaoOnline = await isPilotOnlineIVAO(ivao_id);

    return isVatsimOnline || isIvaoOnline;
}
// --- FIM Lógica de Verificação de Status Online ---


// TAREFA DE BACKGROUND PARA VERIFICAR O STATUS DA REDE
async function networkStatusCheckerLoop() {

    const loop = async () => {
        const currentTime = Date.now(); // Tempo em milissegundos

        // 1. GATE PRINCIPAL: Verifica se há pilotos conectados
        if (Object.keys(PILOT_CONNECTIONS).length === 0) {
            LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime; // Reset/Update para evitar estouro
            setTimeout(loop, 1000); // Tenta novamente em 1 segundo
            return;
        }

        // 2. GATE DE TEMPO: Verifica se 120 segundos se passaram desde o último check global
        if (currentTime - LAST_GLOBAL_NETWORK_CHECK_TIME < NETWORK_CHECK_INTERVAL_SERVER) {
            setTimeout(loop, 1000); // Tenta novamente em 1 segundo
            return;
        }

        console.log(`[${getTimestamp()}] [SERVER CHECK] Iniciando verificação de rede para ${Object.keys(PILOT_CONNECTIONS).length} piloto(s) (120s).`);
        LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;

        const pilotsToRemove = [];
        // Cria uma cópia das chaves para iterar, permitindo modificação do objeto
        const pilotIds = Object.keys(PILOT_CONNECTIONS);

        for (const pilotId of pilotIds) {
            const connData = PILOT_CONNECTIONS[pilotId];
            if (!connData) continue; // Caso tenha sido removido por outra rotina

            const ws = connData.websocket;
            const vatsimId = connData.vatsim_id;
            const ivaoId = connData.ivao_id;

            // Pula pilotos sem IDs válidos
            if (pilotId === "ANON" || (vatsimId === "N/A" && ivaoId === "N/A")) {
                continue;
            }

            try {
                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);
                const isTransmitting = connData.tx_sent;

                if (ws.readyState !== ws.OPEN) {
                    pilotsToRemove.push(pilotId); // Conexão fechada
                    continue;
                }

                if (isOnline) {
                    // PILOTO ONLINE: Se não estava transmitindo, envia START_TX
                    if (!isTransmitting) {
                        const command = JSON.stringify({ command: "START_TX" });
                        await ws.send(command); // Usamos await por consistência
                        connData.tx_sent = true;
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} ONLINE. Comando START_TX enviado.`);
                    }
                } else {
                    // PILOTO OFFLINE: Se estava transmitindo, envia STOP_TX e atualiza a flag.
                    if (isTransmitting) {
                        const command = JSON.stringify({ command: "STOP_TX" });
                        await ws.send(command);
                        connData.tx_sent = false;
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} OFFLINE em IVAO/VATSIM. Comando STOP_TX enviado (Conexão mantida).`);
                    }
                }

            } catch (e) {
                // Se o send falhar (e.g., conexão fechada no meio do send)
                console.log(`[${getTimestamp()}] [SERVER CHECK] Erro processando/enviando comando para ${pilotId}: ${e.message}`);
                pilotsToRemove.push(pilotId);
            }
        }

        // Limpa os pilotos desconectados/fechados
        for (const pilotId of pilotsToRemove) {
            if (PILOT_CONNECTIONS[pilotId]) {
                delete PILOT_CONNECTIONS[pilotId];
            }
        }

        setTimeout(loop, 1000); // Tenta novamente em 1 segundo
    };

    // Inicia o loop
    setTimeout(loop, 1000);
}


/**
 * Gera a tabela HTML com a estimativa de consumo.
 * @param {number} average_rate_mbh
 * @returns {string}
 */
function generateEstimatedDataTable(average_rate_mbh) {
    const hours = [2, 4, 6, 8];
    let rows_html = "";
    const rate_to_use = average_rate_mbh > 0 ? average_rate_mbh : WORST_CASE_RATE_MBH;

    for (const h of hours) {
        const estimated_mb = h * rate_to_use;
        const formatted_mb = formatNumber(estimated_mb, 2);
        rows_html += `<tr class="stats-row"><td>${h} Horas</td><td class="stats-value">${formatted_mb} MB</td></tr>`;
    }
    return rows_html;
}

/**
 * Gera as linhas HTML para a tabela resumo de todos os pilotos ativos.
 * @returns {string}
 */
function generatePilotSummaryRows() {
    let rows_html = "";

    const pilotSnapshots = ALL_PILOT_SNAPSHOTS;
    const pilotIds = Object.keys(pilotSnapshots);

    if (pilotIds.length === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #95a5a6;">Nenhum voo ativo no momento.</td></tr>';
    }

    for (const pilot_id of pilotIds) {
        const data = pilotSnapshots[pilot_id];
        // Verifica o status de transmissão para fins de exibição no HTML
        const conn_status = PILOT_CONNECTIONS[pilot_id] ? PILOT_CONNECTIONS[pilot_id].tx_sent : false;

        // Dados essenciais para o resumo
        const alt = formatNumber(data.alt_ind || 0, 0);
        const vs = formatNumber(data.vs || 0, 0);
        const ias = formatNumber(data.ias || 0, 0);
        const vatsim = data.vatsim_id || 'N/A';

        // Lógica de Status Visual
        let status_text;
        let status_class;

        if (!conn_status) {
            status_text = "PAUSADO (Offline Rede)";
            status_class = "status-cold";
        } else {
            // VARIÁVEIS DE VERIFICAÇÃO
            const current_vs = data.vs || 0;
            const is_moving_vertically = Math.abs(current_vs) > 50; // Movimento vertical > 50 fpm
            const is_airborne = (data.on_ground || 1) === 0 && (data.agl || 0) > 100;
            const is_taxiing = (data.on_ground || 1) === 1 && (data.ias || 0) > 5 && (data.eng_combustion || 0) === 1;
            const is_cold = (data.eng_combustion || 0) === 0;

            // NOVO: Prioriza Movimento Vertical ou On_Ground correto
            if (is_airborne || (is_moving_vertically && (data.ias || 0) > 40)) {
                status_text = "EM VOO";
                status_class = "status-airborne";
            }
            else if (is_taxiing) {
                status_text = "TAXIANDO";
                status_class = "status-taxiing";
            }
            else if (!is_cold) {
                status_text = "EM SOLO (Engine On)";
                status_class = "status-ready";
            }
            else {
                status_text = "OFFLINE/COLD";
                status_class = "status-cold";
            }
        }

        rows_html += `
                <tr class="pilot-row ${status_class}">
                    <td class="pilot-id">${pilot_id}</td>
                    <td>V: ${vatsim} / I: ${data.ivao_id || 'N/A'}</td>
                    <td>${status_text}</td>
                    <td>${alt} ft</td>
                    <td>${vs} fpm</td>
                    <td>${ias} kts</td>
                </tr>`;
    }

    return rows_html;
}


/**
 * Gera o arquivo JSON com os dados em tempo real para o frontend.
 * @param {object} data - O snapshot de dados do último piloto ativo.
 * @param {number} received_count - Contagem global de pacotes recebidos.
 * @param {number} total_bytes_received - Total de bytes recebidos.
 * @returns {Promise<void>}
 */
async function generateRealtimeDataJson(data, received_count, total_bytes_received) {
    const now = new Date();
    const timeSinceLastUpdate = now.getTime() - LAST_JSON_UPDATE_TIME.getTime();

    // CORREÇÃO: Verifica se já se passaram 5 segundos (5000 ms) desde a última atualização
    if (timeSinceLastUpdate < 5000) {
        return; // Não atualiza o arquivo JSON
    }

    // Atualiza o timestamp da última escrita
    LAST_JSON_UPDATE_TIME = now;
    console.log(`[${getTimestamp()}] [JSON_WRITE] Atualizando whazzup.json para Lat/Lng.`);

    const timeElapsed = now.getTime() - SERVER_START_TIME.getTime();
    const timeElapsedHours = timeElapsed / (1000 * 3600); // Milissegundos para horas

    let averageRateMbh = 0.0;
    const totalMbReceived = total_bytes_received / (1024 * 1024);

    if (timeElapsedHours > 0 && total_bytes_received > 0) {
        averageRateMbh = totalMbReceived / timeElapsedHours;
    }

    const json_data = {
        "timestamp": now.toISOString(),
        "pilot_id": data.pilot_id || "N/A",
        "lat": data.lat || 0.0,
        "lng": data.lng || 0.0,
        "alt_ind": data.alt_ind || 0,
        "vs": data.vs || 0,
        "ias": data.ias || 0,
        "g_force": data.g_force || 1.0,
        "total_fuel": data.total_fuel || 0,
        "eng_combustion": data.eng_combustion || 0,
        "packets_received_count": received_count,
        "total_bytes_received_mb": totalMbReceived,
        "average_rate_mbh": averageRateMbh,
    };

    try {
        await fs.mkdir(path.dirname(JSON_FILE_PATH), { recursive: true });
        await fs.writeFile(JSON_FILE_PATH, JSON.stringify(json_data));
    } catch (e) {
        // ESSA MENSAGEM É CRUCIAL PARA DEBUGAR O PROBLEMA DE ESCRITA
        console.error(`[${getTimestamp()}] ERRO AO ESCREVER ARQUIVO JSON: ${e.message}`);
    }
}


/**
 * Gera o HTML principal (estático) e o JSON (tempo real, se o tempo permitir).
 * @param {object} data - O snapshot de dados do último piloto ativo.
 * @param {number} received_count - Contagem global de pacotes recebidos.
 * @param {number} total_bytes_received - Total de bytes recebidos.
 * @returns {Promise<void>}
 */
async function updateMonitorFiles(data, received_count, total_bytes_received) {

    // 1. GERAÇÃO DO JSON (Controlada pelo tempo dentro da função)
    await generateRealtimeDataJson(data, received_count, total_bytes_received);

    // 2. GERAÇÃO DO HTML (Sempre atualiza, mas a tabela de resumo de pilotos depende do ALL_PILOT_SNAPSHOTS)

    const now = new Date();
    const timeElapsed = now.getTime() - SERVER_START_TIME.getTime();
    const timeElapsedHours = timeElapsed / (1000 * 3600); // Milissegundos para horas

    let averageRateMbh = 0.0;
    const totalMbReceived = total_bytes_received / (1024 * 1024);

    if (timeElapsedHours > 0 && total_bytes_received > 0) {
        averageRateMbh = totalMbReceived / timeElapsedHours;
    }

    const rateStatusText = formatNumber(averageRateMbh, 4) + " MB/hora";
    const estimatedTableRows = generateEstimatedDataTable(averageRateMbh);
    const pilotSummaryRows = generatePilotSummaryRows();

    // Pega os dados do último piloto ativo para exibição de estatísticas individuais
    const sentCount = formatNumber(data.packets_sent || 0, 0);
    const sentMb = formatNumber(data.mb_sent || 0.0, 4);
    const receivedMb = formatNumber(totalMbReceived, 4);
    const activePilotsCount = Object.keys(ALL_PILOT_SNAPSHOTS).length;

    const html_content = `<?php
// Arquivo gerado em ${now.toISOString()} pelo Servidor Node.js
// O mapa agora usa AJAX para ler whazzup.json para dados em tempo real
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
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1c1c1c; color: #e0e0e0; margin: 0; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; background-color: #242424; padding: 30px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5); }
        
        /* Títulos */
        h1 { text-align: center; color: #00bcd4; border-bottom: 2px solid #00bcd4; padding-bottom: 10px; margin-bottom: 25px; font-weight: 300; letter-spacing: 1px; }
        h2 { color: #ff9800; font-size: 1.2em; border-bottom: 1px solid #ff980040; padding-bottom: 5px; margin-top: 30px; }

        /* Tabelas */
        .data-table { width: 100%; border-collapse: collapse; margin-bottom: 30px; border-radius: 8px; overflow: hidden; }
        .data-table th, .data-table td { padding: 14px; text-align: left; border-bottom: 1px solid #333; }
        .data-table th { background-color: #383838; color: #ffffff; font-weight: 600; text-transform: uppercase; }
        #map { height: 400px; width: 100%; border-radius: 8px; margin-top: 20px; }
        /* Cores Dinâmicas */
        .pilot-row.status-airborne { background-color: #43a04730; color: #81c784; } 
        .pilot-row.status-taxiing { background-color: #ffb30030; color: #ffb300; } 
        .pilot-row.status-ready { background-color: #1e88e530; color: #64b5f6; } 
        .pilot-row.status-cold { background-color: #333333; color: #999; } 
    </style>
</head>
<body>
    <div class="container">
        <h1>Monitor de Voos Ativos Skymetrics (Node.js)</h1>
        
        <div id="status" class="status-box status-connected">ESTADO DO SERVIDOR: ${now.toLocaleTimeString('pt-BR')}</div>

        <h2>Resumo de Voos Ativos (${activePilotsCount} Piloto(s))</h2>
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
                ${pilotSummaryRows}
            </tbody>
        </table>

        <h2 style="margin-top: 30px;">Localização (Último Piloto Ativo)</h2>
        <div id="map"></div>
        
        <script>
            var map;
            var marker = null; // CHAVE: Inicializado como null para controle de criação
            
            const JSON_URL = 'whazzup.json';

            // NOVO: Função para verificar se os dados lidos são válidos para o mapa
            function isValidData(data) {
                // Verifica se lat e lng existem, são números e NÃO são os valores iniciais (0.0)
                return data && 
                       typeof data.lat === 'number' && data.lat !== 0.0 && 
                       typeof data.lng === 'number' && data.lng !== 0.0;
            }

            // NOVO: Apenas cria o mapa, centralizado no fallback de SP
            function initMap() { 
                if (!document.getElementById('map')) return; 

                if (map) { map.remove(); }

                // Centra o mapa no ponto de fallback (São Paulo)
                var mapCenter = [-23.5505, -46.6333]; 
                map = L.map('map').setView(mapCenter, 10);
                
                // --- DEFINIÇÃO DE CAMADAS BASE ---
                var osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' });
                var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Tiles &copy; Esri' });
                
                osm.addTo(map);

                var baseLayers = { "Estrada (OSM)": osm, "Satélite (Esri)": satellite };
                L.control.layers(baseLayers).addTo(map);

                // O marcador NÃO é criado aqui
            }


            // NOVO: Tenta carregar dados iniciais e inicia o loop
            async function fetchInitialData() {
                // O mapa deve ser inicializado, mesmo sem dados.
                initMap(); 
                
                try {
                    // Usa um timestamp para garantir que o primeiro dado lido seja o mais novo
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime()); 
                    const data = await response.json();
                    
                    if (isValidData(data)) { // CHAVE: Se os dados são válidos
                        var newLatLng = L.latLng(data.lat, data.lng);

                        // CRIAÇÃO INICIAL DO MARCADOR (Apenas se não existir)
                        if (!marker) {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup(\`<b>Piloto: \${data.pilot_id}</b><br>Alt: \${data.alt_ind} ft<br>IAS: \${data.ias} kts\`)
                                .openPopup();
                            
                            // Centraliza o mapa no local correto
                            map.setView(newLatLng, 10); 
                        }
                    } else {
                        console.warn("JSON lido com sucesso, mas coordenadas Lat/Lng são inválidas (0.0) na inicialização.");
                    }
                    
                    // Inicia o loop de atualização contínua
                    setInterval(updateMarkerPosition, 2000); 

                } catch (error) {
                    console.error("ERRO GRAVE no FETCH/JSON inicial. Verifique as permissões de 'whazzup.json'.", error.message);
                    
                    // Inicia o loop para tentar criar o marcador posteriormente
                    setInterval(updateMarkerPosition, 2000); 
                }
            }


            // NOVO: FUNÇÃO CHAVE que cria ou move o marcador
            async function updateMarkerPosition() {
                try {
                    // Adiciona um timestamp para evitar cache do navegador
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime());
                    const data = await response.json();

                    if (isValidData(data)) { // CHAVE: Se os dados são válidos
                        var newLatLng = L.latLng(data.lat, data.lng);

                        if (marker) {
                            // Se o marcador existe, apenas move
                            marker.setLatLng(newLatLng);
                            // Atualiza o popup
                            marker.getPopup().setContent(\`<b>Piloto: \${data.pilot_id}</b><br>Alt: \${data.alt_ind} ft<br>IAS: \${data.ias} kts\`);
                            
                            // Re-centraliza se o marcador sair da tela
                            if (!map.getBounds().contains(newLatLng)) {
                                map.setView(newLatLng, map.getZoom()); 
                            }

                        } else {
                            // Se o marcador NÃO existe, cria ele agora (catch up)
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup(\`<b>Piloto: \${data.pilot_id}</b><br>Alt: \${data.alt_ind} ft<br>IAS: \${data.ias} kts\`)
                                .openPopup();
                            
                            // Centraliza o mapa no local correto
                            map.setView(newLatLng, 10); 
                        }
                    } else {
                        // Se o fetch foi bem-sucedido, mas o JSON tem lat/lng 0.0
                        console.warn("JSON lido com sucesso no loop, mas coordenadas Lat/Lng são inválidas (0.0).");
                    }
                    
                    document.getElementById('pacotes-recebidos').textContent = data.packets_received_count;

                } catch (error) {
                    // Este erro geralmente é 404/403 ou JSON malformado.
                    console.error("ERRO GRAVE no FETCH/JSON do loop. Verifique as permissões de 'whazzup.json'.", error.message);
                }
            }

            window.onload = fetchInitialData;
        </script>
        
        <h2 style="margin-top: 30px;">Estatísticas de Tráfego Global</h2>
        <table class="data-table" style="max-width: 500px;">
            <tbody>
                <tr class="stats-row"><td class="stats-label">Pacotes Enviados (Cliente)</td><td class="stats-value">${sentCount}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Enviados (MB)</td><td class="stats-value">${sentMb} MB</td></tr>
                <tr class="stats-row"><td class="stats-label">Pacotes Recebidos (Servidor)</td><td class="stats-value" id="pacotes-recebidos">${received_count}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Recebidos (MB)</td><td class="stats-value">${receivedMb} MB</td></tr>
            </tbody>
        </table>

        <h2 style="margin-top: 30px;">Projeção de Consumo (Baseado na Taxa Atual: ${rateStatusText})</h2>
        <table class="data-table" style="max-width: 400px;">
            <thead>
                <tr><th>Projeção</th><th>Consumo Estimado</th></tr>
            </thead>
            <tbody>${estimatedTableRows}</tbody>
        </table>
        
        <p style="text-align: center; font-size: 0.8em; margin-top: 20px; color: #95a5a6;">
            Dados do mapa atualizados em tempo real via whazzup.json. O servidor atualiza o whazzup.json a cada 5 segundos.
        </p>
    </div>
</body>
</html>
`;

    try {
        await fs.mkdir(path.dirname(HTML_FILE_PATH), { recursive: true });
        await fs.writeFile(HTML_FILE_PATH, html_content);
    } catch (e) {
        console.error(`[${getTimestamp()}] ERRO AO ESCREVER ARQUIVO HTML: ${e.message}`);
    }
}


// ---------------------------------------------------------
// 3. HANDLER PRINCIPAL (Lógica de Estado do Voo)
// ---------------------------------------------------------

/**
 * Lida com a conexão e as mensagens do WebSocket.
 * @param {import('ws')} ws 
 */
async function handleFlightData(ws) {
    register(ws);

    let pilotId = ws.pilot_id;

    // Adiciona o listener para mensagens
    ws.on('message', async (message) => {
        try {
            const messageString = message.toString();
            // --- 1. ATUALIZAÇÃO DOS CONTADORES DO SERVIDOR ---
            const messageSize = Buffer.byteLength(messageString, 'utf8');
            totalBytesReceived += messageSize;
            packetsReceivedCount += 1;

            const data = JSON.parse(messageString);
            pilotId = String(data.pilot_id || "ANON"); // Atualiza o pilotId na closure

            // --- LÓGICA DE CONEXÃO E CHECK INICIAL ---
            if (pilotId !== "ANON" && !PILOT_CONNECTIONS[pilotId]) {
                const vatsimId = String(data.vatsim_id || "N/A");
                const ivaoId = String(data.ivao_id || "N/A");

                // Armazena IDs no objeto WebSocket (para uso no unregister)
                ws.pilot_id = pilotId;
                ws.vatsim_id = vatsimId;
                ws.ivao_id = ivaoId;

                // Armazena na lista controlada para o loop de background
                PILOT_CONNECTIONS[pilotId] = {
                    websocket: ws,
                    vatsim_id: vatsimId,
                    ivao_id: ivaoId,
                    tx_sent: false // Flag para garantir um único envio de START_TX
                };

                // Realiza o CHECK IMEDIATO
                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);

                if (isOnline) {
                    const command = JSON.stringify({ command: "START_TX" });
                    ws.send(command);
                    PILOT_CONNECTIONS[pilotId].tx_sent = true;
                    console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} ONLINE (Check Imediato). Comando START_TX enviado (ÚNICO).`);
                } else {
                    const command = JSON.stringify({ command: "STOP_TX" });
                    ws.send(command);
                    PILOT_CONNECTIONS[pilotId].tx_sent = false;
                    console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} OFFLINE (Check Imediato). Comando STOP_TX enviado (Conexão mantida).`);
                }
            }

            // CHAVE: Só processa a lógica de eventos e a escrita dos arquivos se estivermos transmitindo
            if (!PILOT_CONNECTIONS[pilotId] || !PILOT_CONNECTIONS[pilotId].tx_sent) {
                return;
            }


            // LOG CONCISO DE DEBUG
            const altitude = data.alt_ind || 0;
            const ias = data.ias || 0;
            const vs = data.vs || 0;
            const bank = data.plane_bank_degrees || 0;
            const overspeed = data.alerts?.overspeed_warning || 0;
            const stall = data.alerts?.stall_warning || 0;
            console.log(`[${getTimestamp()}] [DADOS BRUTOS] Piloto: ${pilotId} | Alt: ${altitude.toFixed(0)} ft | VS: ${vs.toFixed(0)} fpm | IAS: ${ias.toFixed(1)} kts | Bank: ${bank.toFixed(1)} deg | ALERTS: OSPD=${overspeed}, STALL=${stall}`);


            // --- 2. INICIALIZAÇÃO E ATUALIZAÇÃO DE ESTADO ---
            if (!CLIENT_FLIGHT_STATES[pilotId]) {
                CLIENT_FLIGHT_STATES[pilotId] = {
                    is_airborne: false, has_landed: true, initial_fuel_logged: false, landing_vs: null, last_vs: 0.0
                };
            }

            // CHAVE: Armazena o snapshot completo e atualizado para este piloto
            ALL_PILOT_SNAPSHOTS[pilotId] = data;

            const currentState = CLIENT_FLIGHT_STATES[pilotId];

            // --- DETECÇÃO DE EVENTOS DE VOO ---
            const currentAgl = data.agl || 0;
            const currentIas = data.ias || 0;
            const currentVs = data.vs || 0;
            const currentOnGround = data.on_ground || 0;
            const currentBank = data.plane_bank_degrees || 0;

            // A. DECOLAGEM
            if (!currentState.is_airborne && currentAgl > 50 && currentIas > 40) {
                currentState.is_airborne = true;
                currentState.has_landed = false;
                printEvent(pilotId, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.");
            }

            // B. POUSO (Toque e Parada)
            if (currentState.is_airborne && currentOnGround === 1 && currentAgl < 100 && !currentState.has_landed) {
                if (currentState.landing_vs === null) currentState.landing_vs = currentState.last_vs;
                if (currentIas < 10) {
                    currentState.has_landed = true;
                    currentState.is_airborne = false;
                    const vsNoToque = currentState.landing_vs || currentVs;
                    printEvent(pilotId, "POUSO_FINALIZADO", `Pouso concluído. VS no toque: ${vsNoToque.toFixed(0)} fpm`);
                }
            }

            // C. COMBUSTÍVEL INICIAL
            if ((data.eng_combustion || 0) === 1 && !currentState.initial_fuel_logged) {
                printEvent(pilotId, "COMBUSTIVEL_INICIAL", `Motor ligado. Combustível: ${formatNumber(data.total_fuel || 0, 0)} gal`);
                currentState.initial_fuel_logged = true;
            }

            // D. ALERTA: BANK ANGLE (> 30°)
            if (Math.abs(currentBank) > 30) {
                printEvent(pilotId, "ALERTA:BANK_ANGLE_HIGH", `Ângulo de inclinação excessivo: ${Math.abs(currentBank).toFixed(1)} graus.`);
            }

            // E. ALERTA: STALL WARNING 
            if ((data.alerts?.stall_warning || 0) === 1) {
                printEvent(pilotId, "ALERTA:STALL_WARNING", "Alerta de estol (stall warning) ativo.");
            }

            // F. OUTROS ALERTAS
            if ((data.alerts?.beacon_off_engine_on || 0) === 1) {
                printEvent(pilotId, "ALERTA:BEACON_OFF_ENGINE_ON", "Beacon Lights desligadas com o motor em funcionamento.");
            }
            if ((data.alerts?.engine_fire || 0) === 1) {
                printEvent(pilotId, "ALERTA:ENG_FIRE", "Incêndio detectado no Motor.");
            }

            // G. POUSO RESET (Ex: Após pousar, o piloto volta a acelerar para outra decolagem ou táxi rápido)
            if (currentState.has_landed && currentOnGround === 1 && currentIas > 50) {
                currentState.is_airborne = false;
                currentState.has_landed = false;
                currentState.initial_fuel_logged = false;
                currentState.landing_vs = null;
            }

            currentState.last_vs = currentVs;

            // 4. Atualiza os arquivos
            await updateMonitorFiles(data, packetsReceivedCount, totalBytesReceived);

        } catch (e) {
            if (e.message === 'WebSocket closed') {
                // Conexão fechada. O 'close' listener lidará com o unregister.
            } else if (e instanceof SyntaxError) {
                console.error(`[${getTimestamp()}] [ERROR HANDLER] Erro de parse JSON de ${pilotId}: ${e.message}`);
            } else {
                console.error(`[${getTimestamp()}] [ERROR HANDLER] Erro no fluxo de dados para ${pilotId}: ${e.message}`);
            }
        }
    });

    // Adiciona o listener para fechamento (equivalente ao finally do Python)
    ws.on('close', async () => {
        await unregister(ws);
        // Remove a referência do PILOT_CONNECTIONS se ainda estiver lá
        if (ws.pilot_id !== "ANON" && PILOT_CONNECTIONS[ws.pilot_id]) {
            delete PILOT_CONNECTIONS[ws.pilot_id];
        }
    });

    ws.on('error', (error) => {
        console.error(`[${getTimestamp()}] [WS ERROR] Erro na conexão para ${pilotId || ws._socket.remoteAddress}: ${error.message}`);
    });
}

// ---------------------------------------------------------
// 4. FUNÇÃO MAIN
// ---------------------------------------------------------

/**
 * Cria os arquivos iniciais HTML e JSON com valores vazios.
 * @returns {Promise<void>}
 */
async function createInitialFiles() {
    SERVER_START_TIME = new Date();
    // Define o tempo inicial de atualização JSON para garantir que a primeira escrita ocorra imediatamente
    LAST_JSON_UPDATE_TIME = new Date(0);

    const initial_data = {
        "alt_ind": 0, "vs": 0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
        "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, "engine_vibration_1": 0.0,
        "pilot_id": "N/A", "vatsim_id": "N/A", "ivao_id": "N/A",
        "alerts": { "overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0 },
        "packets_sent": 0, "mb_sent": 0.0
    };

    try {
        // Força a criação inicial de ambos os arquivos
        await updateMonitorFiles(initial_data, 0, 0.0);

        console.log(`[${getTimestamp()}] SUCESSO: Arquivos HTML/JSON iniciais criados.`);
    } catch (e) {
        // Se a criação inicial falhar, o erro deve ser capturado aqui
        console.error(`[${getTimestamp()}] ERRO AO CRIAR ARQUIVOS INICIAIS: ${e.message}`);
    }
}

/**
 * Função principal para iniciar o servidor.
 */
async function main() {

    await createInitialFiles();

    // Inicia a tarefa de verificação de rede em background
    networkStatusCheckerLoop();

    // Cria o servidor HTTP para hospedar o WebSocket
    const httpServer = createServer();
    const wss = new WebSocketServer({ server: httpServer });

    wss.on('connection', handleFlightData);

    httpServer.listen(PORT, HOST, () => {
        console.log(`*** Servidor WebSocket Skymetrics iniciado. Escutando em ws://${HOST}:${PORT} ***`);
    });

    // Lida com o desligamento limpo (Ctrl+C)
    process.on('SIGINT', () => {
        console.log("\nServidor encerrado por Ctrl+C.");
        wss.close(() => {
            httpServer.close(() => {
                process.exit(0);
            });
        });
    });
}

// Executa a função principal
main().catch(error => {
    console.error(`Erro fatal na inicialização do servidor: ${error.message}`);
    process.exit(1);
});