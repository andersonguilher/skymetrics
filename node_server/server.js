// server.js - Tradução funcional do servidor Python Skymetrics
const { WebSocketServer } = require('ws');
const fs = require('fs').promises;
const axios = require('axios');

// =========================================================
// 1. CONFIGURAÇÃO GERAL
// =========================================================
const HOST = "0.0.0.0";
const PORT = 8765;
const HTML_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t.php";
const JSON_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/dash/utils/t.json";

// Contadores e Variáveis de Estado
let packetsReceivedCount = 0;
let totalBytesReceived = 0.0;
const SERVER_START_TIME = new Date();
const USERS = new Set(); // Armazena os objetos 'ws' de cada conexão
const WORST_CASE_RATE_MBH = 12.3;
const CLIENT_FLIGHT_STATES = new Map();
const ALL_PILOT_SNAPSHOTS = new Map();
let LAST_JSON_UPDATE_TIME = new Date(0); // new Date(0) é o equivalente a datetime.min

// Variáveis para verificação de rede
const IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup";
const VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json";
const NETWORK_CHECK_INTERVAL_SERVER = 120 * 1000; // 120 segundos em milissegundos
const PILOT_CONNECTIONS = new Map();


// =========================================================
// 2. FUNÇÕES AUXILIARES
// =========================================================

const log = (message) => console.log(`[${new Date().toLocaleTimeString('pt-BR')}] ${message}`);
const printEvent = (pilotId, eventName, description) => log(`[EVENTO] Piloto ${pilotId}: ${eventName} -> ${description}`);

function formatNumber(value, decimals) {
    if (typeof value === 'number') {
        return value.toLocaleString('pt-BR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }
    return "N/A";
}

// --- Lógica de Verificação de Status Online na IVAO/VATSIM ---
async function isPilotOnlineIvao(ivaoId) {
    if (!ivaoId || ivaoId.trim() === 'N/A' || ivaoId.trim() === '' || ivaoId.trim() === '0') return false;
    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000 });
        const ivaoIdInt = parseInt(ivaoId.trim(), 10);
        const pilots = response.data?.clients?.pilots || [];
        return pilots.some(client => client.userId === ivaoIdInt);
    } catch (error) {
        return false;
    }
}

async function isPilotOnlineVatsim(vatsimId) {
    if (!vatsimId || vatsimId.trim() === 'N/A' || vatsimId.trim() === '' || vatsimId.trim() === '0') return false;
    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000 });
        const vatsimIdInt = parseInt(vatsimId.trim(), 10);
        const pilots = response.data?.pilots || [];
        return pilots.some(pilot => pilot.cid === vatsimIdInt);
    } catch (error) {
        return false;
    }
}

async function checkNetworkStatus(vatsimId, ivaoId) {
    const isVatsimOnline = await isPilotOnlineVatsim(vatsimId);
    if (isVatsimOnline) return true;
    return await isPilotOnlineIvao(ivaoId);
}
// --- FIM Lógica de Verificação ---

// --- Geração de Conteúdo HTML e JSON ---
function generateEstimatedDataTable(averageRateMbh) {
    const hours = [2, 4, 6, 8];
    let rowsHtml = "";
    const rateToUse = averageRateMbh > 0 ? averageRateMbh : WORST_CASE_RATE_MBH;
    for (const h of hours) {
        const estimatedMb = h * rateToUse;
        rowsHtml += `<tr class="stats-row"><td>${h} Horas</td><td class="stats-value">${formatNumber(estimatedMb, 2)} MB</td></tr>`;
    }
    return rowsHtml;
}

function generatePilotSummaryRows() {
    if (ALL_PILOT_SNAPSHOTS.size === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #95a5a6;">Nenhum voo ativo no momento.</td></tr>';
    }
    let rowsHtml = "";
    for (const [pilotId, data] of ALL_PILOT_SNAPSHOTS.entries()) {
        const conn_status = PILOT_CONNECTIONS.get(pilotId)?.tx_sent || false;
        let status_text, status_class;
        if (!conn_status) {
            status_text = "PAUSADO (Offline Rede)";
            status_class = "status-cold";
        } else {
            const is_airborne = data.on_ground === 0 && data.alt_ind > 100;
            const is_taxiing = data.on_ground === 1 && data.ias > 5 && data.eng_combustion === 1;
            const is_cold = data.eng_combustion === 0;
            if (is_airborne) { status_text = "EM VOO"; status_class = "status-airborne"; }
            else if (is_taxiing) { status_text = "TAXIANDO"; status_class = "status-taxiing"; }
            else if (!is_cold) { status_text = "EM SOLO (Engine On)"; status_class = "status-ready"; }
            else { status_text = "OFFLINE/COLD"; status_class = "status-cold"; }
        }
        rowsHtml += `
            <tr class="pilot-row ${status_class}">
                <td class="pilot-id">${pilotId}</td>
                <td>V: ${data.vatsim_id || 'N/A'} / I: ${data.ivao_id || 'N/A'}</td>
                <td>${status_text}</td>
                <td>${formatNumber(data.alt_ind || 0, 0)} ft</td>
                <td>${formatNumber(data.vs || 0, 0)} fpm</td>
                <td>${formatNumber(data.ias || 0, 0)} kts</td>
            </tr>`;
    }
    return rowsHtml;
}

async function generateRealtimeDataJson(data) {
    if ((new Date() - LAST_JSON_UPDATE_TIME) < 60000) return; // 60 segundos
    LAST_JSON_UPDATE_TIME = new Date();
    log(`[JSON_WRITE] Atualizando t.json para Lat/Lng.`);

    const timeElapsedHours = (new Date() - SERVER_START_TIME) / 3600000;
    const totalMbReceived = totalBytesReceived / (1024 * 1024);
    const averageRateMbh = (timeElapsedHours > 0 && totalMbReceived > 0) ? totalMbReceived / timeElapsedHours : 0.0;

    const jsonData = {
        "timestamp": new Date().toISOString(),
        "pilot_id": data.pilot_id || "N/A",
        "lat": data.lat || 0.0,
        "lng": data.lng || 0.0,
        "alt_ind": data.alt_ind || 0,
        "vs": data.vs || 0,
        "ias": data.ias || 0,
        "packets_received_count": packetsReceivedCount,
    };
    try {
        await fs.writeFile(JSON_FILE_PATH, JSON.stringify(jsonData, null, 2));
    } catch (e) {
        log(`ERRO AO ESCREVER ARQUIVO JSON: ${e.message}`);
    }
}

async function updateMonitorFiles(data) {
    await generateRealtimeDataJson(data);

    const timeElapsedHours = (new Date() - SERVER_START_TIME) / 3600000;
    const totalMbReceived = totalBytesReceived / (1024 * 1024);
    const averageRateMbh = (timeElapsedHours > 0 && totalMbReceived > 0) ? totalMbReceived / timeElapsedHours : 0.0;

    const rateStatusText = formatNumber(averageRateMbh, 4) + " MB/hora";
    const estimatedTableRows = generateEstimatedDataTable(averageRateMbh);
    const pilotSummaryRows = generatePilotSummaryRows();
    const receivedMb = formatNumber(totalMbReceived, 4);
    const sentCount = formatNumber(data.packets_sent || 0, 0);
    const sentMb = formatNumber(data.mb_sent || 0.0, 4);

    // O conteúdo HTML é idêntico ao do script Python, apenas com sintaxe de Template Literal do JS
    const htmlContent = `<?php
// Arquivo gerado em ${new Date().toISOString()} pelo Servidor Node.js
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
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1c1c1c; color: #e0e0e0; margin: 0; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; background-color: #242424; padding: 30px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5); }
        h1 { text-align: center; color: #00bcd4; border-bottom: 2px solid #00bcd4; padding-bottom: 10px; margin-bottom: 25px; font-weight: 300; letter-spacing: 1px; }
        h2 { color: #ff9800; font-size: 1.2em; border-bottom: 1px solid #ff980040; padding-bottom: 5px; margin-top: 30px; }
        .data-table { width: 100%; border-collapse: collapse; margin-bottom: 30px; border-radius: 8px; overflow: hidden; }
        .data-table th, .data-table td { padding: 14px; text-align: left; border-bottom: 1px solid #333; }
        .data-table th { background-color: #383838; color: #ffffff; font-weight: 600; text-transform: uppercase; }
        #map { height: 400px; width: 100%; border-radius: 8px; margin-top: 20px; }
        .pilot-row.status-airborne { background-color: #43a04730; color: #81c784; } 
        .pilot-row.status-taxiing { background-color: #ffb30030; color: #ffb300; } 
        .pilot-row.status-ready { background-color: #1e88e530; color: #64b5f6; } 
        .pilot-row.status-cold { background-color: #333333; color: #999; } 
    </style>
</head>
<body>
    <div class="container">
        <h1>Monitor de Voos Ativos Skymetrics</h1>
        <h2>Resumo de Voos Ativos (${ALL_PILOT_SNAPSHOTS.size} Piloto(s))</h2>
        <table class="data-table">
            <thead><tr><th>ID Piloto</th><th>VATSIM / IVAO</th><th>Status Voo</th><th>Altitude</th><th>VS</th><th>IAS</th></tr></thead>
            <tbody>${pilotSummaryRows}</tbody>
        </table>
        <h2 style="margin-top: 30px;">Localização (Último Piloto Ativo)</h2>
        <div id="map"></div>
        <script>
            var map, marker = null, initialLat = 0.0, initialLng = 0.0;
            const JSON_URL = 't.json';
            async function fetchInitialData() {
                try {
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime());
                    const data = await response.json();
                    initialLat = data.lat; initialLng = data.lng;
                    initMap(); 
                    setInterval(updateMarkerPosition, 2000);
                } catch (error) {
                    console.error("Erro ao carregar dados iniciais do mapa:", error);
                    initMap(); 
                    setInterval(updateMarkerPosition, 2000);
                }
            }
            function initMap() {
                if (!document.getElementById('map')) return;
                if (map) map.remove();
                var mapCenter = [initialLat || -23.5505, initialLng || -46.6333];
                map = L.map('map').setView(mapCenter, 10);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
                if (initialLat !== 0 && initialLng !== 0) {
                    marker = L.marker(mapCenter).addTo(map).bindPopup('Aguardando dados...').openPopup();
                }
            }
            async function updateMarkerPosition() {
                try {
                    const response = await fetch(JSON_URL + '?t=' + new Date().getTime());
                    const data = await response.json();
                    if (data.lat === 0 && data.lng === 0) {
                        if (marker) {
                            map.removeLayer(marker);
                            marker = null;
                        }
                        return;
                    }
                    var newLatLng = L.latLng(data.lat, data.lng);
                    if (marker) {
                        marker.setLatLng(newLatLng);
                        marker.getPopup().setContent(\`<b>Piloto: \${data.pilot_id}</b><br>Alt: \${data.alt_ind} ft<br>IAS: \${data.ias} kts\`);
                    } else {
                        marker = L.marker(newLatLng).addTo(map).bindPopup(\`<b>Piloto: \${data.pilot_id}</b><br>Alt: \${data.alt_ind} ft<br>IAS: \${data.ias} kts\`).openPopup();
                    }
                    document.getElementById('pacotes-recebidos').textContent = data.packets_received_count;
                } catch (error) {
                    console.warn("Aguardando dados em t.json ou erro de leitura.", error.message);
                }
            }
            window.onload = fetchInitialData;
        </script>
        <h2 style="margin-top: 30px;">Estatísticas de Tráfego Global</h2>
        <table class="data-table" style="max-width: 500px;">
            <tbody>
                <tr class="stats-row"><td class="stats-label">Pacotes Enviados (Cliente)</td><td class="stats-value">${sentCount}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Enviados (MB)</td><td class="stats-value">${sentMb} MB</td></tr>
                <tr class="stats-row"><td class="stats-label">Pacotes Recebidos (Servidor)</td><td class="stats-value" id="pacotes-recebidos">${packetsReceivedCount}</td></tr>
                <tr class="stats-row"><td class="stats-label">Dados Recebidos (MB)</td><td class="stats-value">${receivedMb} MB</td></tr>
            </tbody>
        </table>
        <h2 style="margin-top: 30px;">Projeção de Consumo (Baseado na Taxa Atual: ${rateStatusText})</h2>
        <table class="data-table" style="max-width: 400px;">
            <thead><tr><th>Projeção</th><th>Consumo Estimado</th></tr></thead>
            <tbody>${estimatedTableRows}</tbody>
        </table>
        <p style="text-align: center; font-size: 0.8em; margin-top: 20px; color: #95a5a6;">
            Dados do mapa atualizados em tempo real via t.json. O servidor atualiza o t.json a cada 60 segundos.
        </p>
    </div>
</body>
</html>`;
    try {
        await fs.writeFile(HTML_FILE_PATH, htmlContent);
    } catch (e) {
        log(`ERRO AO ESCREVER ARQUIVO HTML: ${e.message}`);
    }
}


// =========================================================
// 3. HANDLER PRINCIPAL E LÓGICA DO SERVIDOR
// =========================================================
const wss = new WebSocketServer({ host: HOST, port: PORT });

wss.on('connection', (ws, req) => {
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });

    // --- a. Registro da Conexão ---
    USERS.add(ws);
    ws.pilotId = "ANON";
    log(`NOVO CLIENTE CONECTADO: ${req.socket.remoteAddress}. Total: ${USERS.size}`);

    // --- b. Handler de Mensagens ---
    ws.on('message', async (message) => {
        try {
            packetsReceivedCount++;
            totalBytesReceived += message.length;
            const data = JSON.parse(message);
            const pilotId = String(data.pilot_id || "ANON");

            // --- Primeira mensagem / Identificação ---
            if (pilotId !== "ANON" && !PILOT_CONNECTIONS.has(pilotId)) {
                const vatsimId = String(data.vatsim_id || "N/A");
                const ivaoId = String(data.ivao_id || "N/A");
                ws.pilotId = pilotId; // Associa o ID ao objeto ws

                PILOT_CONNECTIONS.set(pilotId, {
                    websocket: ws, vatsim_id: vatsimId, ivao_id: ivaoId, tx_sent: false
                });

                // Check imediato de rede
                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);
                const connData = PILOT_CONNECTIONS.get(pilotId);
                if (isOnline) {
                    ws.send(JSON.stringify({ command: "START_TX" }));
                    connData.tx_sent = true;
                    log(`[SERVER CHECK] Piloto ${pilotId} ONLINE (Check Imediato). Comando START_TX enviado.`);
                } else {
                    ws.send(JSON.stringify({ command: "STOP_TX" }));
                    connData.tx_sent = false;
                    log(`[SERVER CHECK] Piloto ${pilotId} OFFLINE (Check Imediato). Comando STOP_TX enviado.`);
                }
            }

            const connData = PILOT_CONNECTIONS.get(pilotId);
            if (!connData || !connData.tx_sent) {
                return; // Ignora pacotes se não estiver autorizado a transmitir
            }

            // --- Processamento de Dados de Voo e Eventos ---
            ALL_PILOT_SNAPSHOTS.set(pilotId, data);

            if (!CLIENT_FLIGHT_STATES.has(pilotId)) {
                CLIENT_FLIGHT_STATES.set(pilotId, {
                    is_airborne: false, has_landed: true, initial_fuel_logged: false, landing_vs: null, last_vs: 0.0
                });
            }
            const state = CLIENT_FLIGHT_STATES.get(pilotId);

            // Detecção de Eventos
            if (!state.is_airborne && data.agl > 50 && data.ias > 40) {
                state.is_airborne = true; state.has_landed = false;
                printEvent(pilotId, "DECOLAGEM", "Decolagem detectada.");
            }
            if (state.is_airborne && data.on_ground === 1 && data.agl < 100 && !state.has_landed) {
                if (state.landing_vs === null) state.landing_vs = state.last_vs;
                if (data.ias < 10) {
                    state.has_landed = true; state.is_airborne = false;
                    printEvent(pilotId, "POUSO_FINALIZADO", `Pouso concluído. VS no toque: ${state.landing_vs?.toFixed(0) || 0} fpm`);
                }
            }
            state.last_vs = data.vs;

            await updateMonitorFiles(data);

        } catch (e) {
            log(`Erro ao processar mensagem: ${e.message}`);
        }
    });

    // --- c. Handler de Desconexão ---
    ws.on('close', () => {
        USERS.delete(ws);
        const pilotId = ws.pilotId; // ID que associamos na primeira mensagem
        if (pilotId && pilotId !== "ANON") {
            PILOT_CONNECTIONS.delete(pilotId);
            ALL_PILOT_SNAPSHOTS.delete(pilotId);
            CLIENT_FLIGHT_STATES.delete(pilotId);
        }
        log(`CLIENTE DESCONECTADO: ${req.socket.remoteAddress}. Total: ${USERS.size}`);

        // Atualiza a página para remover o piloto da lista
        const lastData = ALL_PILOT_SNAPSHOTS.size > 0 ? [...ALL_PILOT_SNAPSHOTS.values()].pop() : {};
        updateMonitorFiles(lastData);
    });

    ws.on('error', (error) => log(`ERRO NO WEBSOCKET: ${error.message}`));
});

// =========================================================
// 4. LOOPS DE BACKGROUND E INICIALIZAÇÃO
// =========================================================

// Loop de verificação periódica de rede
setInterval(async () => {
    if (PILOT_CONNECTIONS.size === 0) return;
    log(`[SERVER CHECK] Iniciando verificação de rede para ${PILOT_CONNECTIONS.size} piloto(s) (${NETWORK_CHECK_INTERVAL_SERVER / 1000}s).`);

    for (const [pilotId, connData] of PILOT_CONNECTIONS.entries()) {
        try {
            const isOnline = await checkNetworkStatus(connData.vatsim_id, connData.ivao_id);
            if (isOnline && !connData.tx_sent) {
                connData.websocket.send(JSON.stringify({ command: "START_TX" }));
                connData.tx_sent = true;
                log(`[SERVER CHECK] Piloto ${pilotId} ONLINE. Comando START_TX enviado.`);
            } else if (!isOnline && connData.tx_sent) {
                connData.websocket.send(JSON.stringify({ command: "STOP_TX" }));
                connData.tx_sent = false;
                log(`[SERVER CHECK] Piloto ${pilotId} OFFLINE. Comando STOP_TX enviado.`);
            }
        } catch (e) {
            log(`Erro no loop de verificação para ${pilotId}: ${e.message}`);
        }
    }
}, NETWORK_CHECK_INTERVAL_SERVER);

// Loop para verificar conexões "mortas" (ping/pong)
setInterval(() => {
    wss.clients.forEach(ws => {
        if (!ws.isAlive) return ws.terminate();
        ws.isAlive = false;
        ws.ping(() => { });
    });
}, 30000);


// Função de inicialização
async function main() {
    const initialData = { pilot_id: "N/A", vatsim_id: "N/A", ivao_id: "N/A", lat: 0, lng: 0, alt_ind: 0, vs: 0, ias: 0 };
    await updateMonitorFiles(initialData);
    log(`SUCESSO: Arquivos HTML/JSON iniciais criados.`);
    log(`*** Servidor WebSocket Skymetrics iniciado. Escutando em ws://${HOST}:${PORT} ***`);
}

main().catch(err => log(`ERRO FATAL NA INICIALIZAÇÃO: ${err.message}`));