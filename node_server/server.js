// server.js - Roda na sua VPS (Replicação da Lógica Python)
import { WebSocketServer } from 'ws';
import { createServer } from 'http';
import * as fs from 'fs/promises'; // Usaremos promises para operações de arquivo
import * as path from 'path';
import axios from 'axios'; // Importa o axios para fazer requisições HTTP
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

// URL CORRETA: https://kafly.com.br/dash/utils/submit_flight_log.php
const SUBMIT_LOG_URL = "https://kafly.com.br/dash/utils/submit_flight_log.php"; //

// Contadores globais
let packetsReceivedCount = 0;
let totalBytesReceived = 0.0;

// Variáveis de Estado
let SERVER_START_TIME = null;
const USERS = new Set();
const WORST_CASE_RATE_MBH = 12.3;

const CLIENT_FLIGHT_LOGS = {};
const ALL_PILOT_SNAPSHOTS = {};
let LAST_JSON_UPDATE_TIME = new Date(0);

// Variáveis para verificação de rede (Controle do Servidor)
const IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup";
const VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json";
const NETWORK_CHECK_INTERVAL_SERVER = 120 * 1000;
let LAST_GLOBAL_NETWORK_CHECK_TIME = 0.0;
const PILOT_CONNECTIONS = {};

// ---------------------------------------------------------
// 2. FUNÇÕES AUXILIARES
// ---------------------------------------------------------

const getTimestamp = () => new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

// NOVO: Função auxiliar de delay para as retentativas
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function register(ws) {
    USERS.add(ws);
    ws.pilot_id = "ANON";
    ws.vatsim_id = "N/A";
    ws.ivao_id = "N/A";
    console.log(`[${getTimestamp()}] NOVO CLIENTE CONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}

async function unregister(ws) {
    if (!USERS.has(ws)) return;

    const pilot_id = ws.pilot_id || "ANON";

    if (pilot_id !== "ANON") {
        if (ALL_PILOT_SNAPSHOTS[pilot_id]) {
            delete ALL_PILOT_SNAPSHOTS[pilot_id];
        }
        if (PILOT_CONNECTIONS[pilot_id]) {
            delete PILOT_CONNECTIONS[pilot_id];
        }

        if (CLIENT_FLIGHT_LOGS[pilot_id] && !CLIENT_FLIGHT_LOGS[pilot_id].flight_ended) {
            await logEvent(pilot_id, "CONEXAO_PERDIDA", "Conexão encerrada abruptamente (cliente ou rede). Tentando enviar log acumulado.", ALL_PILOT_SNAPSHOTS[pilot_id] || {});

            await postFullFlightLog(pilot_id);

            CLIENT_FLIGHT_LOGS[pilot_id].flight_ended = true;
        }
    }

    USERS.delete(ws);

    let data_to_update = { alt_ind: 0, vs: 0, ias: 0, eng_combustion: 0, vatsim_id: "N/A", ivao_id: "N/A", pilot_id: "N/A", packets_sent: 0, mb_sent: 0.0 };
    if (Object.keys(ALL_PILOT_SNAPSHOTS).length > 0) {
        data_to_update = Object.values(ALL_PILOT_SNAPSHOTS)[0];
    }

    await updateMonitorFiles(data_to_update, packetsReceivedCount, totalBytesReceived);
    console.log(`[${getTimestamp()}] CLIENTE DESCONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}


/**
 * Formata um número para string com separador de milhares 
 * @param {number} value
 * @param {number} decimals
 * @returns {string}
 */
function formatNumber(value, decimals) {
    if (typeof value !== 'number') return "N/A";

    const options = { minimumFractionDigits: decimals, maximumFractionDigits: decimals };
    return value.toLocaleString('pt-BR', options);
}

/**
 * Envia um único evento formatado para o endpoint PHP.
 * Retorna 'SUCCESS', 'NOT_FOUND_LOGIC' (404), ou 'CRITICAL_ERROR'.
 * @param {object} logEntry - O objeto de evento formatado para o PHP.
 */
async function sendEventToPHP(logEntry) {
    try {
        const response = await axios.post(SUBMIT_LOG_URL, logEntry, {
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            timeout: 10000
        });

        console.log(`[${getTimestamp()}] [SUBMIT LOG] Evento ${logEntry.evento} enviado. Resposta: ${response.data.message}`);
        return 'SUCCESS';
    } catch (e) {
        if (e.response && e.response.status === 404) {
            const phpMessage = e.response.data && e.response.data.message ? e.response.data.message : 'Nenhuma mensagem de erro no corpo do 404.';
            console.warn(`[${getTimestamp()}] [SUBMIT LOG] AVISO 404 LÓGICO para ${logEntry.evento}. Motivo PHP: ${phpMessage}`);
            return 'NOT_FOUND_LOGIC';
        }
        console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO CRÍTICO ao enviar evento ${logEntry.evento}: ${e.message}`);
        return 'CRITICAL_ERROR';
    }
}

/**
 * Armazena o evento localmente (sem enviar ao PHP).
 * @param {string} pilot_id 
 * @param {string} event_name 
 * @param {string} description 
 * @param {object} snapshot - O snapshot de dados atual
 */
async function logEvent(pilot_id, event_name, description, snapshot) {
    // 1. Log to console
    console.log(`[${getTimestamp()}] [EVENTO] Piloto ${pilot_id}: ${event_name} -> ${description} (Armazenado localmente)`);

    if (!CLIENT_FLIGHT_LOGS[pilot_id]) {
        console.error(`[${getTimestamp()}] [FLIGHT LOG] Estado de voo não inicializado para ${pilot_id}. Não é possível logar.`);
        return;
    }

    // Determine the actual userId for the database (IVAO/VATSIM ID)
    const connData = PILOT_CONNECTIONS[pilot_id];
    let actualUserId = pilot_id;
    if (connData) {
        // Prioritize IVAO ID if present, as that's what the DB is using.
        if (connData.ivao_id !== "N/A") {
            actualUserId = connData.ivao_id;
        } else if (connData.vatsim_id !== "N/A") {
            actualUserId = connData.vatsim_id;
        }
    }

    // 2. Prepare log entry (Format ready for PHP post)
    const flightPlan = CLIENT_FLIGHT_LOGS[pilot_id];

    const logEntry = {
        // CHAVE: Usando o ID de rede (IVAO/VATSIM) para a busca no banco de dados.
        userId: actualUserId,
        departureId: flightPlan.flightPlan_departureId,
        arrivalId: flightPlan.flightPlan_arrivalId,
        evento: event_name,
        descricao: description,
        data_hora: new Date().toISOString(),
        lat: snapshot.lat || 0.0,
        lng: snapshot.lng || 0.0,
    };

    // 3. Store locally
    flightPlan.event_log.push(logEntry);
}

/**
 * Envia todos os eventos acumulados para o endpoint PHP sequencialmente, com retentativas.
 * @param {string} pilot_id 
 */
async function postFullFlightLog(pilot_id) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 5000; // 5 segundos
    const flightState = CLIENT_FLIGHT_LOGS[pilot_id];

    if (!flightState || flightState.event_log.length === 0) {
        console.warn(`[${getTimestamp()}] [SUBMIT LOG] Nenhum evento acumulado para o piloto ${pilot_id} enviar.`);
        return;
    }

    const logCopy = [...flightState.event_log]; // Use uma cópia para as retentativas
    let success = false;

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        console.log(`[${getTimestamp()}] [SUBMIT LOG] Iniciando tentativa ${attempt}/${MAX_RETRIES} de envio de ${logCopy.length} eventos em lote para o piloto ${pilot_id}...`);

        let allEventsSucceeded = true;

        for (const logEntry of logCopy) {
            const result = await sendEventToPHP(logEntry);

            if (result === 'NOT_FOUND_LOGIC' && attempt < MAX_RETRIES) {
                // Se o primeiro evento (ou qualquer outro) retornar 404,
                // interrompemos e tentamos novamente, presumindo que o registro do voo ainda não foi criado.
                allEventsSucceeded = false;
                console.warn(`[${getTimestamp()}] [SUBMIT LOG] Interrompendo tentativa ${attempt}. Recorde de voo não encontrado (404 Lógico).`);
                break;
            } else if (result === 'CRITICAL_ERROR') {
                allEventsSucceeded = false;
                break; // Falha crítica, não faz sentido continuar
            }
            // Se for sucesso, continua para o próximo evento no loop interno.
        }

        if (allEventsSucceeded) {
            success = true;
            console.log(`[${getTimestamp()}] [SUBMIT LOG] Envio em lote concluído com SUCESSO na tentativa ${attempt}.`);
            // Limpa o buffer de log local após o sucesso final
            CLIENT_FLIGHT_LOGS[pilot_id].event_log = [];
            break;
        }

        if (attempt < MAX_RETRIES) {
            console.warn(`[${getTimestamp()}] [SUBMIT LOG] Aguardando ${RETRY_DELAY_MS / 1000}s antes da próxima retentativa...`);
            await delay(RETRY_DELAY_MS);
        } else {
            console.error(`[${getTimestamp()}] [SUBMIT LOG] FALHA CRÍTICA: O envio falhou após ${MAX_RETRIES} tentativas. O log foi mantido na memória.`);
        }
    }
}


// --- Lógica de Verificação de Status Online na IVAO/VATSIM ---

// MODIFICADO: Retorna o objeto FlightPlan se encontrado, caso contrário, null.
async function isPilotOnlineIVAO(ivao_id) {
    if (!ivao_id || ivao_id.trim() === 'N/A' || ivao_id.trim() === '' || ivao_id.trim() === '0') return null;
    const ivao_id_int = parseInt(ivao_id.trim());
    if (isNaN(ivao_id_int)) return null;

    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000 });
        const data = response.data;
        for (const client of data.clients.pilots) {
            if (client.userId === ivao_id_int && client.flightPlan) {
                return client.flightPlan;
            }
        }
        return null;
    } catch (e) {
        return null;
    }
}

// NOVO: Função para obter os IDs do plano de voo
async function getPilotFlightPlan(vatsim_id, ivao_id) {
    let departureId = "N/A";
    let arrivalId = "N/A";
    let networkUserId = "N/A"; // ID de rede real

    // 1. Tenta obter o plano de voo do IVAO
    const ivaoFlightPlan = await isPilotOnlineIVAO(ivao_id);
    if (ivaoFlightPlan) {
        departureId = ivaoFlightPlan.departureId;
        arrivalId = ivaoFlightPlan.arrivalId;
        networkUserId = ivao_id;
    }

    // 2. Adicionar lógica para VATSIM aqui (Se necessário)

    return { departureId, arrivalId, networkUserId };
}


// A função check_network_status permanece inalterada, pois ela apenas verifica a presença.
async function isPilotOnlineVATSIM(vatsim_id) {
    if (!vatsim_id || vatsim_id.trim() === 'N/A' || vatsim_id.trim() === '' || vatsim_id.trim() === '0') return false;
    const vatsim_id_int = parseInt(vatsim_id.trim());
    if (isNaN(vatsim_id_int)) return false;

    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000 });
        const data = response.data;
        for (const pilot of data.pilots) {
            if (pilot.cid === vatsim_id_int) { return true; }
        }
        return false;
    } catch (e) { return false; }
}

async function checkNetworkStatus(vatsim_id, ivao_id) {
    const isVatsimOnline = await isPilotOnlineVATSIM(vatsim_id);
    // Reutiliza a função modificada, mas apenas para a verificação de status (null é false)
    const isIvaoOnline = !!(await isPilotOnlineIVAO(ivao_id));
    return isVatsimOnline || isIvaoOnline;
}


// TAREFA DE BACKGROUND PARA VERIFICAR O STATUS DA REDE
// ... (networkStatusCheckerLoop and all subsequent auxiliary functions remain the same) ...

async function networkStatusCheckerLoop() {

    const loop = async () => {
        const currentTime = Date.now();

        if (Object.keys(PILOT_CONNECTIONS).length === 0) {
            LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;
            setTimeout(loop, 1000);
            return;
        }

        if (currentTime - LAST_GLOBAL_NETWORK_CHECK_TIME < NETWORK_CHECK_INTERVAL_SERVER) {
            setTimeout(loop, 1000);
            return;
        }

        console.log(`[${getTimestamp()}] [SERVER CHECK] Iniciando verificação de rede para ${Object.keys(PILOT_CONNECTIONS).length} piloto(s) (120s).`);
        LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;

        const pilotsToRemove = [];
        const pilotIds = Object.keys(PILOT_CONNECTIONS);

        for (const pilotId of pilotIds) {
            const connData = PILOT_CONNECTIONS[pilotId];
            if (!connData) continue;

            const ws = connData.websocket;
            const vatsimId = connData.vatsim_id;
            const ivaoId = connData.ivao_id;

            if (pilotId === "ANON" || (vatsimId === "N/A" && ivaoId === "N/A") || !ALL_PILOT_SNAPSHOTS[pilotId]) {
                continue;
            }

            try {
                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);
                const isTransmitting = connData.tx_sent;

                if (ws.readyState !== ws.OPEN) {
                    pilotsToRemove.push(pilotId);
                    continue;
                }

                // --- LÓGICA DE PAUSA INTELIGENTE ---
                const pilotSnapshot = ALL_PILOT_SNAPSHOTS[pilotId];
                const currentIas = pilotSnapshot.ias || 0;
                const currentOnGround = pilotSnapshot.on_ground || 1;

                const isStuckOnGround = currentOnGround === 1 && currentIas < 5 && isOnline;

                if (isStuckOnGround && isTransmitting) {
                    const lastStopTime = connData.last_stop_time;
                    if (!lastStopTime) {
                        connData.last_stop_time = new Date();
                        continue;
                    }

                    const timeStuckMs = new Date().getTime() - lastStopTime.getTime();
                    if (timeStuckMs >= 5 * 60 * 1000) {
                        const command = JSON.stringify({ command: "STOP_TX" });
                        await ws.send(command);
                        connData.tx_sent = false;
                        connData.last_stop_time = new Date();
                        logEvent(pilotId, "PAUSA_INTELIGENTE", "Pouso/Solo detectado (5min). Transmissão pausada para economia de dados.", pilotSnapshot); // Log localmente
                        continue;
                    }
                }
                else if (connData.last_stop_time && (currentIas > 5 || currentOnGround === 0)) {
                    connData.last_stop_time = null;
                }

                // --- LÓGICA DE REDE PADRÃO ---
                if (isOnline) {
                    if (!isTransmitting) {
                        if (currentIas > 5 || currentOnGround === 0) {
                            const command = JSON.stringify({ command: "START_TX" });
                            await ws.send(command);
                            connData.tx_sent = true;
                            console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} ONLINE. Comando START_TX enviado.`);
                        }
                    }
                } else {
                    if (isTransmitting) {
                        const command = JSON.stringify({ command: "STOP_TX" });
                        await ws.send(command);
                        connData.tx_sent = false;
                        connData.last_stop_time = new Date();
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} OFFLINE em IVAO/VATSIM. Comando STOP_TX enviado (Conexão mantida).`);
                    }
                }

            } catch (e) {
                console.log(`[${getTimestamp()}] [SERVER CHECK] Erro processando/enviando comando para ${pilotId}: ${e.message}`);
                pilotsToRemove.push(pilotId);
            }
        }

        for (const pilotId of pilotsToRemove) {
            if (PILOT_CONNECTIONS[pilotId]) {
                delete PILOT_CONNECTIONS[pilotId];
            }
        }

        setTimeout(loop, 1000);
    };

    setTimeout(loop, 1000);
}


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

function generatePilotSummaryRows() {
    let rows_html = "";

    const pilotIds = Object.keys(PILOT_CONNECTIONS);

    if (pilotIds.length === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #A9A9A9;">Nenhum cliente conectado no momento.</td></tr>';
    }

    for (const pilot_id of pilotIds) {
        const connData = PILOT_CONNECTIONS[pilot_id];
        const data = ALL_PILOT_SNAPSHOTS[pilot_id];
        const conn_status = connData ? connData.tx_sent : false;

        const alt = data ? formatNumber(data.alt_ind || 0, 0) : "N/A";
        const vs = data ? formatNumber(data.vs || 0, 0) : "N/A";
        const ias = data ? formatNumber(data.ias || 0, 0) : "N/A";

        const vatsim = connData.vatsim_id || 'N/A';
        const ivao = connData.ivao_id || 'N/A';

        let status_text;
        let status_class;

        if (!data) {
            status_text = "CONECTADO (Sem Dados)";
            status_class = "status-pending";
        }
        else if (!conn_status) {
            const is_stuck_on_ground = connData.last_stop_time;

            if (is_stuck_on_ground && (data.eng_combustion || 0) === 1 && (data.on_ground || 1) === 1) {
                status_text = "PAUSADO (Solo Inteligente)";
            } else {
                status_text = "PAUSADO (Offline Rede)";
            }
            status_class = "status-paused";
        } else {
            const is_airborne = (data.on_ground || 1) === 0 || (data.agl || 0) > 50;

            const is_taxiing = (data.on_ground || 1) === 1 && (data.ias || 0) > 5 && (data.eng_combustion || 0) === 1;
            const is_cold = (data.eng_combustion || 0) === 0;

            if (is_airborne) { status_text = "EM VOO"; status_class = "status-airborne"; }
            else if (is_taxiing) { status_text = "TAXIANDO"; status_class = "status-taxiing"; }
            else if (!is_cold) { status_text = "EM SOLO (Engine On)"; status_class = "status-ready"; }
            else { status_text = "OFFLINE/COLD"; status_class = "status-cold"; }
        }


        rows_html += `
                <tr class="pilot-row ${status_class}">
                    <td class="pilot-id">${pilot_id}</td>
                    <td>V: ${vatsim} / I: ${ivao}</td>
                    <td>${status_text}</td>
                    <td>${alt} ft</td>
                    <td>${vs} fpm</td>
                    <td>${ias} kts</td>
                </tr>`;
    }

    return rows_html;
}


async function generateRealtimeDataJson(data, received_count, total_bytes_received) {
    const now = new Date();
    const timeSinceLastUpdate = now.getTime() - LAST_JSON_UPDATE_TIME.getTime();

    if (timeSinceLastUpdate < 60000) {
        return;
    }

    LAST_JSON_UPDATE_TIME = now;
    console.log(`[${getTimestamp()}] [JSON_WRITE] Atualizando whazzup.json para Lat/Lng.`);

    const timeElapsed = now.getTime() - SERVER_START_TIME.getTime();
    const timeElapsedHours = timeElapsed / (1000 * 3600);

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
        console.error(`[${getTimestamp()}] ERRO AO ESCREVER ARQUIVO JSON: ${e.message}`);
    }
}


async function updateMonitorFiles(data, received_count, total_bytes_received) {

    await generateRealtimeDataJson(data, received_count, total_bytes_received);

    const now = new Date();
    const timeElapsed = now.getTime() - SERVER_START_TIME.getTime();
    const timeElapsedHours = timeElapsed / (1000 * 3600);

    let averageRateMbh = 0.0;
    const totalMbReceived = total_bytes_received / (1024 * 1024);

    if (timeElapsedHours > 0 && total_bytes_received > 0) {
        averageRateMbh = totalMbReceived / timeElapsedHours;
    }

    const rateStatusText = formatNumber(averageRateMbh, 4) + " MB/hora";
    const estimatedTableRows = generateEstimatedDataTable(averageRateMbh);
    const pilotSummaryRows = generatePilotSummaryRows();

    const sentCount = formatNumber(data.packets_sent || 0, 0);
    const sentMb = formatNumber(data.mb_sent || 0.0, 4);
    const receivedMb = formatNumber(totalMbReceived, 4);
    const activePilotsCount = Object.keys(PILOT_CONNECTIONS).length;

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
        /* ... Estilos de Monitoramento (Inalterados) ... */
    </style>
</head>
<body>
    <div class="container">
        <h1>Monitor de Voos Ativos Skymetrics (Node.js)</h1>
        
        <div id="status" class="status-box status-connected">ESTADO DO SERVIDOR: ${now.toLocaleTimeString('pt-BR')}</div>

        <h2>Resumo de Clientes Conectados (${activePilotsCount} Clientes)</h2>
        <table class="data-table">
            <thead>
                <tr>
                    <th>ID Piloto</th>
                    <th>VATSIM / IVAO</th>
                    <th>Status</th>
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
            // ... Lógica de Mapa (Inalterada) ...
            var map;
            var marker = null; 
            
            const JSON_URL = 'whazzup.json';

            function isValidData(data) {
                return data && 
                       typeof data.lat === 'number' && data.lat !== 0.0 && 
                       typeof data.lng === 'number' && data.lng !== 0.0;
            }

            function initMap() { 
                if (!document.getElementById('map')) return; 

                if (map) { map.remove(); }

                var mapCenter = [-23.5505, -46.6333]; 
                map = L.map('map').setView(mapCenter, 10);
                
                var osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' });
                var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Tiles &copy; Esri' });
                
                osm.addTo(map);

                var baseLayers = { "Estrada (OSM)": osm, "Satélite (Esri)": satellite };
                L.control.layers(baseLayers).addTo(map);
                
                map.invalidateSize();
            }

            async function fetchInitialData() {
                initMap(); 
                
                try {
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime()); 
                    const data = await response.json();
                    
                    if (isValidData(data)) { 
                        var newLatLng = L.latLng(data.lat, data.lng);

                        if (!marker) {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup('<b>Piloto: ' + data.pilot_id + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts')
                                .openPopup();
                            
                            map.setView(newLatLng, 10); 
                        }
                    } else {
                        console.warn("JSON lido com sucesso, mas coordenadas Lat/Lng são inválidas (0.0) na inicialização.");
                    }
                    
                    setInterval(updateMarkerPosition, 2000); 

                } catch (error) {
                    console.error("ERRO GRAVE no FETCH/JSON inicial. Verifique as permissões de 'whazzup.json'.", error.message);
                    
                    setInterval(updateMarkerPosition, 2000); 
                }
            }


            async function updateMarkerPosition() {
                try {
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime());
                    const data = await response.json();

                    if (isValidData(data)) { 
                        var newLatLng = L.latLng(data.lat, data.lng);

                        if (marker) {
                            marker.setLatLng(newLatLng);
                            marker.getPopup().setContent('<b>Piloto: ' + data.pilot_id + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts');
                            
                            if (!map.getBounds().contains(newLatLng)) {
                                map.setView(newLatLng, map.getZoom()); 
                            }

                        } else {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup('<b>Piloto: ' + data.pilot_id + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts')
                                .openPopup();
                            
                            map.setView(newLatLng, 10); 
                        }
                    } else {
                        console.warn("JSON lido com sucesso no loop, mas coordenadas Lat/Lng são inválidas (0.0).");
                    }
                    
                    document.getElementById('pacotes-recebidos').textContent = data.packets_received_count;

                } catch (error) {
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
            Dados do mapa atualizados em tempo real via whazzup.json. O servidor atualiza o whazzup.json a cada 60 segundos.
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

async function handleFlightData(ws) {
    register(ws);

    let pilotId = ws.pilot_id;

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

                // NOVO: Obtém o plano de voo da rede antes de inicializar o log
                const flightPlanDetails = await getPilotFlightPlan(vatsimId, ivaoId);

                // O banco de dados (PHP) espera o ID de rede para buscar o registro de voo.
                const logUserId = flightPlanDetails.networkUserId || pilotId;


                if (!CLIENT_FLIGHT_LOGS[pilotId]) {
                    CLIENT_FLIGHT_LOGS[pilotId] = {
                        is_airborne: false,
                        has_landed: true,
                        initial_fuel_logged: false,
                        landing_vs: null,
                        last_vs: 0.0,
                        flight_ended: false,
                        event_log: [],
                        last_alert_timestamps: {}, // NOVO: Armazena o timestamp dos últimos alertas
                        // CHAVE: Usar os IDs obtidos da rede (ou N/A como fallback)
                        flightPlan_departureId: flightPlanDetails.departureId,
                        flightPlan_arrivalId: flightPlanDetails.arrivalId,
                    };

                    console.log(`[${getTimestamp()}] [FLIGHT LOG] Piloto ${pilotId} (Log ID: ${logUserId}) iniciado com DEP: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_departureId} / ARR: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_arrivalId}`);
                    // O evento INICIO_SESSAO é o primeiro a ser logado
                    logEvent(pilotId, "INICIO_SESSAO", `Sessão de telemetria iniciada. DEP: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_departureId}, ARR: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_arrivalId}. (Usando ID de Rede ${logUserId} para log)`, data);
                }


                ws.pilot_id = pilotId;
                ws.vatsim_id = vatsimId;
                ws.ivao_id = ivaoId;

                PILOT_CONNECTIONS[pilotId] = {
                    websocket: ws,
                    vatsim_id: vatsimId,
                    ivao_id: ivaoId,
                    tx_sent: false,
                    last_stop_time: null,
                };

                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);

                if (isOnline) {
                    const command = JSON.stringify({ command: "START_TX" });
                    ws.send(command);
                    PILOT_CONNECTIONS[pilotId].tx_sent = true;
                    console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} ONLINE e detectado. Comando START_TX enviado (Inicia Transmissão).`);
                } else {
                    const command = JSON.stringify({ command: "STOP_TX" });
                    ws.send(command);
                    PILOT_CONNECTIONS[pilotId].tx_sent = false;
                    console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotId} OFFLINE na rede. Comando STOP_TX enviado (Conexão mantida).`);
                }
            }

            // --- Armazena o snapshot mesmo se a transmissão estiver pausada ---
            if (pilotId in PILOT_CONNECTIONS) {
                ALL_PILOT_SNAPSHOTS[pilotId] = data;
            }

            // CHAVE: Só processa a lógica de eventos e a escrita dos arquivos se estivermos transmitindo
            if (!PILOT_CONNECTIONS[pilotId] || !PILOT_CONNECTIONS[pilotId].tx_sent) {
                // Atualiza a página de monitoramento mesmo sem transmitir dados de voo
                await updateMonitorFiles(data, packetsReceivedCount, totalBytesReceived);
                return;
            }

            // --- REMOVIDO: LOG DE DADOS BRUTOS (CONFORME SOLICITADO) ---

            // --- 2. INICIALIZAÇÃO E ATUALIZAÇÃO DE ESTADO ---
            const currentState = CLIENT_FLIGHT_LOGS[pilotId];

            // --- LÓGICA DE RATE LIMITING PARA ALERTAS ---
            const currentTime = Date.now();
            const ALERT_RATE_LIMIT_MS = 15000; // 15 segundos
            const lastAlerts = currentState.last_alert_timestamps;

            function shouldLogAlert(alertName) {
                if (!lastAlerts[alertName] || currentTime - lastAlerts[alertName] >= ALERT_RATE_LIMIT_MS) {
                    lastAlerts[alertName] = currentTime;
                    return true;
                }
                return false;
            }

            // --- DETECÇÃO DE EVENTOS DE VOO ---
            const currentAgl = data.agl || 0;
            const currentIas = data.ias || 0;
            const currentVs = data.vs || 0;
            const currentOnGround = data.on_ground || 0;
            const currentBank = data.plane_bank_degrees || 0;
            const engCombustion = data.eng_combustion || 0;

            // A. DECOLAGEM
            if (!currentState.is_airborne && currentAgl > 50 && currentIas > 40) {
                currentState.is_airborne = true;
                currentState.has_landed = false;
                currentState.flight_ended = false; // Reinicia o estado de finalizado
                await logEvent(pilotId, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.", data);
            }

            // B. POUSO (Toque e Parada)
            if (currentState.is_airborne && currentOnGround === 1 && currentAgl < 100 && !currentState.has_landed) {
                if (currentState.landing_vs === null) currentState.landing_vs = currentState.last_vs;
                if (currentIas < 10) {
                    currentState.has_landed = true;
                    currentState.is_airborne = false;
                    const vsNoToque = currentState.landing_vs || currentVs;
                    // Loga a VS no toque
                    await logEvent(pilotId, "VS_NO_TOQUE", `Velocidade vertical no toque detectada: ${vsNoToque.toFixed(0)} fpm.`, data);
                    // Loga a conclusão
                    await logEvent(pilotId, "POUSO_FINALIZADO", `Pouso concluído. VS no toque final: ${vsNoToque.toFixed(0)} fpm`, data);
                }
            }

            // C. COMBUSTÍVEL INICIAL (Início de sessão no simulador)
            if (engCombustion === 1 && !currentState.initial_fuel_logged) {
                await logEvent(pilotId, "COMBUSTIVEL_INICIAL", `Motor ligado. Combustível: ${formatNumber(data.total_fuel || 0, 0)} gal`, data);
                currentState.initial_fuel_logged = true;
            }

            // D. ALERTA: BANK ANGLE (> 30°) (RATE LIMITED)
            if (Math.abs(currentBank) > 30) {
                if (shouldLogAlert("ALERTA:BANK_ANGLE_HIGH")) {
                    await logEvent(pilotId, "ALERTA:BANK_ANGLE_HIGH", `Ângulo de inclinação excessivo: ${Math.abs(currentBank).toFixed(1)} graus.`, data);
                }
            }

            // E. ALERTA: STALL WARNING (RATE LIMITED)
            if ((data.alerts?.stall_warning || 0) === 1) {
                if (shouldLogAlert("ALERTA:STALL_WARNING")) {
                    await logEvent(pilotId, "ALERTA:STALL_WARNING", "Alerta de estol (stall warning) ativo.", data);
                }
            }

            // F. OUTROS ALERTA (RATE LIMITED)
            if ((data.alerts?.beacon_off_engine_on || 0) === 1) {
                if (shouldLogAlert("ALERTA:BEACON_OFF_ENGINE_ON")) {
                    await logEvent(pilotId, "ALERTA:BEACON_OFF_ENGINE_ON", "Beacon Lights desligadas com o motor em funcionamento.", data);
                }
            }
            if ((data.alerts?.engine_fire || 0) === 1) {
                if (shouldLogAlert("ALERTA:ENG_FIRE")) {
                    await logEvent(pilotId, "ALERTA:ENG_FIRE", "Incêndio detectado no Motor.", data);
                }
            }

            // G. VOO FINALIZADO (Motor Desligado após Pouso)
            if (currentState.has_landed && currentState.initial_fuel_logged && !currentState.flight_ended && engCombustion === 0) {
                currentState.flight_ended = true;

                // 1. Loga o evento final localmente
                await logEvent(pilotId, "VOO_FINALIZADO", "Motor desligado após pouso. Fim da sessão de voo.", data);

                // 2. Envia TODO o log acumulado
                await postFullFlightLog(pilotId);
            }

            // H. POUSO RESET (Ex: Volta a acelerar para outra decolagem)
            if (currentState.has_landed && currentOnGround === 1 && currentIas > 50) {

                // CHAVE: Antes de resetar, finalize e envie o log do segmento anterior
                if (currentState.event_log.length > 0) {
                    await logEvent(pilotId, "SEGMENTO_CONCLUIDO", "Segmento de voo anterior concluído (Touch-and-Go ou re-takeoff). Enviando logs acumulados.", data);
                    await postFullFlightLog(pilotId); // Envia o log e limpa o buffer
                }

                currentState.is_airborne = false;
                currentState.has_landed = false;
                currentState.initial_fuel_logged = false;
                currentState.landing_vs = null;
                currentState.flight_ended = false;
                await logEvent(pilotId, "RESET_VOO", "Voando novamente ou táxi rápido após pouso. Reiniciando estado de voo.", data);
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

    ws.on('close', async () => {
        await unregister(ws);
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

async function createInitialFiles() {
    SERVER_START_TIME = new Date();
    LAST_JSON_UPDATE_TIME = new Date(0);

    const initial_data = {
        "alt_ind": 0, "vs": 0, "ias": 0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
        "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, "engine_vibration_1": 0.0,
        "pilot_id": "N/A", "vatsim_id": "N/A", "ivao_id": "N/A",
        "alerts": { "overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0 },
        "packets_sent": 0, "mb_sent": 0.0
    };

    try {
        await updateMonitorFiles(initial_data, 0, 0.0);

        console.log(`[${getTimestamp()}] [INFO] Arquivos HTML/JSON iniciais criados.`);
    } catch (e) {
        console.error(`[${getTimestamp()}] [ERRO] Ao criar arquivos iniciais: ${e.message}`);
    }
}

async function main() {

    await createInitialFiles();

    networkStatusCheckerLoop();

    const httpServer = createServer();
    const wss = new WebSocketServer({ server: httpServer });

    wss.on('connection', handleFlightData);

    httpServer.listen(PORT, HOST, () => {
        console.log(`[${getTimestamp()}] *** Servidor WebSocket Skymetrics iniciado. Escutando em ws://${HOST}:${PORT} ***`);
    });

    process.on('SIGINT', () => {
        console.log(`[${getTimestamp()}] Servidor encerrado por Ctrl+C.`);
        wss.close(() => {
            httpServer.close(() => {
                process.exit(0);
            });
        });
    });
}

main().catch(error => {
    console.error(`[${getTimestamp()}] Erro fatal na inicialização do servidor: ${error.message}`);
    process.exit(1);
});