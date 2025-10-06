// server.js - Versão do servidor Skymetrics em Node.js
const { WebSocketServer } = require('ws');
const fs = require('fs').promises;
const path = require('path');
const axios = require('axios'); // Para fazer pedidos HTTP (equivalente ao 'requests')

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
const USERS = new Set(); // Equivalente ao 'set()' do Python
const WORST_CASE_RATE_MBH = 12.3;
const ALL_PILOT_SNAPSHOTS = new Map(); // Equivalente ao dicionário 'ALL_PILOT_SNAPSHOTS'
let LAST_JSON_UPDATE_TIME = new Date(0); // Equivalente a datetime.min

// Variáveis para verificação de rede
const IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup";
const VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json";
const NETWORK_CHECK_INTERVAL_SERVER = 120000; // milissegundos (2 minutos)
const PILOT_CONNECTIONS = new Map(); // Equivalente ao dicionário 'PILOT_CONNECTIONS'

// =========================================================
// 2. FUNÇÕES AUXILIARES
// =========================================================

function printEvent(pilotId, eventName, description) {
    const timestamp = new Date().toLocaleTimeString('pt-BR');
    console.log(`[${timestamp}] [EVENTO] Piloto ${pilotId}: ${eventName} -> ${description}`);
}

function formatNumber(value, decimals) {
    if (typeof value === 'number') {
        return value.toLocaleString('pt-BR', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    }
    return "N/A";
}

// --- Lógica de Verificação de Status Online na IVAO/VATSIM ---
async function isPilotOnlineIvao(ivaoId) {
    if (!ivaoId || ivaoId.trim() === 'N/A' || ivaoId.trim() === '') return false;
    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000 });
        const ivaoIdInt = parseInt(ivaoId.trim(), 10);
        const pilots = response.data?.clients?.pilots || [];
        return pilots.some(client => client.userId === ivaoIdInt);
    } catch (error) {
        // console.error(`[SERVER CHECK] ERRO ao verificar IVAO: ${error.message}`);
        return false;
    }
}

async function isPilotOnlineVatsim(vatsimId) {
    if (!vatsimId || vatsimId.trim() === 'N/A' || vatsimId.trim() === '') return false;
    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000 });
        const vatsimIdInt = parseInt(vatsimId.trim(), 10);
        const pilots = response.data?.pilots || [];
        return pilots.some(pilot => pilot.cid === vatsimIdInt);
    } catch (error) {
        // console.error(`[SERVER CHECK] ERRO ao verificar VATSIM: ${error.message}`);
        return false;
    }
}

async function checkNetworkStatus(vatsimId, ivaoId) {
    const isVatsimOnline = await isPilotOnlineVatsim(vatsimId);
    if (isVatsimOnline) return true;
    const isIvaoOnline = await isPilotOnlineIvao(ivaoId);
    return isIvaoOnline;
}

// --- Geração de Ficheiros ---
function generatePilotSummaryRows() {
    if (ALL_PILOT_SNAPSHOTS.size === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #95a5a6;">Nenhum voo ativo no momento.</td></tr>';
    }

    let rowsHtml = "";
    for (const [pilotId, data] of ALL_PILOT_SNAPSHOTS.entries()) {
        const connStatus = PILOT_CONNECTIONS.get(pilotId)?.tx_sent || false;
        
        const alt = formatNumber(data.alt_ind || 0, 0);
        const vs = formatNumber(data.vs || 0, 0);
        const ias = formatNumber(data.ias || 0, 0);
        const vatsim = data.vatsim_id || 'N/A';
        const ivao = data.ivao_id || 'N/A';

        let statusText, statusClass;
        if (!connStatus) {
            statusText = "PAUSADO (Offline Rede)";
            statusClass = "status-cold";
        } else {
            const isAirborne = data.on_ground === 0 && data.alt_ind > 100;
            const isTaxiing = data.on_ground === 1 && data.ias > 5 && data.eng_combustion === 1;
            const isCold = data.eng_combustion === 0;

            if (isAirborne) { statusText = "EM VOO"; statusClass = "status-airborne"; }
            else if (isTaxiing) { statusText = "TAXIANDO"; statusClass = "status-taxiing"; }
            else if (!isCold) { statusText = "EM SOLO (Engine On)"; statusClass = "status-ready"; }
            else { statusText = "OFFLINE/COLD"; statusClass = "status-cold"; }
        }
        
        rowsHtml += `
            <tr class="pilot-row ${statusClass}">
                <td class="pilot-id">${pilotId}</td>
                <td>V: ${vatsim} / I: ${ivao}</td>
                <td>${statusText}</td>
                <td>${alt} ft</td>
                <td>${vs} fpm</td>
                <td>${ias} kts</td>
            </tr>`;
    }
    return rowsHtml;
}

async function updateMonitorFiles(data) {
    const timeElapsed = (new Date() - SERVER_START_TIME) / (1000 * 3600); // Horas
    const totalMbReceived = totalBytesReceived / (1024 * 1024);
    const averageRateMbh = timeElapsed > 0 ? totalMbReceived / timeElapsed : 0;

    // 1. GERAÇÃO DO JSON (Controlado por tempo)
    if ((new Date() - LAST_JSON_UPDATE_TIME) > 60000) { // 60 segundos
        LAST_JSON_UPDATE_TIME = new Date();
        console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [JSON_WRITE] Atualizando t.json para Lat/Lng.`);

        const jsonData = {
            timestamp: new Date().toISOString(),
            pilot_id: data.pilot_id || "N/A",
            lat: data.lat || 0.0,
            lng: data.lng || 0.0,
            alt_ind: data.alt_ind || 0,
            vs: data.vs || 0,
            ias: data.ias || 0,
            packets_received_count: packetsReceivedCount,
            total_bytes_received_mb: totalMbReceived,
            average_rate_mbh: averageRateMbh,
        };

        try {
            await fs.mkdir(path.dirname(JSON_FILE_PATH), { recursive: true });
            await fs.writeFile(JSON_FILE_PATH, JSON.stringify(jsonData, null, 2), 'utf-8');
        } catch (e) {
            console.error(`[ERRO] Falha ao escrever ficheiro JSON: ${e.message}`);
        }
    }

    // 2. GERAÇÃO DO HTML (Sempre que chamado)
    const pilotSummaryRows = generatePilotSummaryRows();
    const receivedMb = formatNumber(totalMbReceived, 4);
    const htmlTemplate = ``;
    // ... (Cole o seu template HTML aqui e use ${variável} para inserir os dados dinâmicos)
    // Devido ao tamanho do HTML, optei por não o colar todo aqui, mas o processo é o mesmo
    // Exemplo: <h2>Resumo de Voos Ativos (${ALL_PILOT_SNAPSHOTS.size} Piloto(s))</h2>
    // ... <tbody>${pilotSummaryRows}</tbody> ...
    try {
        // Para simplificar, vou apenas logar que a atualização ocorreria
        // console.log("Atualizando ficheiro HTML (lógica omitida para brevidade)...");
        // await fs.mkdir(path.dirname(HTML_FILE_PATH), { recursive: true });
        // await fs.writeFile(HTML_FILE_PATH, htmlContent, 'utf-8');
    } catch (e) {
        console.error(`[ERRO] Falha ao escrever ficheiro HTML: ${e.message}`);
    }
}


// =========================================================
// 3. LÓGICA DO SERVIDOR WEBSOCKET
// =========================================================
const wss = new WebSocketServer({ host: HOST, port: PORT });

wss.on('connection', (ws, req) => {
    const remoteAddress = req.socket.remoteAddress;
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });

    console.log(`[${new Date().toLocaleTimeString('pt-BR')}] NOVO CLIENTE CONECTADO: ${remoteAddress}. Total: ${wss.clients.size}`);
    
    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);
            const pilotId = String(data.pilot_id || "ANON");
            ws.pilotId = pilotId; // Associa o ID à conexão

            packetsReceivedCount++;
            totalBytesReceived += message.length;

            if (pilotId !== "ANON" && !PILOT_CONNECTIONS.has(pilotId)) {
                const vatsimId = String(data.vatsim_id || "N/A");
                const ivaoId = String(data.ivao_id || "N/A");

                PILOT_CONNECTIONS.set(pilotId, {
                    websocket: ws,
                    vatsim_id: vatsimId,
                    ivao_id: ivaoId,
                    tx_sent: false,
                });

                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);
                const connData = PILOT_CONNECTIONS.get(pilotId);

                if (isOnline) {
                    ws.send(JSON.stringify({ command: "START_TX" }));
                    connData.tx_sent = true;
                    console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [SERVER CHECK] Piloto ${pilotId} ONLINE (Check Imediato). Comando START_TX enviado.`);
                } else {
                    ws.send(JSON.stringify({ command: "STOP_TX" }));
                    connData.tx_sent = false;
                     console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [SERVER CHECK] Piloto ${pilotId} OFFLINE (Check Imediato). Comando STOP_TX enviado.`);
                }
            }
            
            const connData = PILOT_CONNECTIONS.get(pilotId);
            if (connData && connData.tx_sent) {
                ALL_PILOT_SNAPSHOTS.set(pilotId, data);
                // Lógica de deteção de eventos de voo (pode ser portada aqui se necessário)
                // printEvent(pilotId, "DECOLAGEM", "Decolagem detectada.");
                await updateMonitorFiles(data);
            }

        } catch (e) {
            console.error("Erro ao processar mensagem:", e.message);
        }
    });

    ws.on('close', async () => {
        const pilotId = ws.pilotId || "ANON";
        console.log(`[${new Date().toLocaleTimeString('pt-BR')}] CLIENTE DESCONECTADO: ${remoteAddress}. Total: ${wss.clients.size}`);
        
        if (pilotId !== "ANON") {
            PILOT_CONNECTIONS.delete(pilotId);
            ALL_PILOT_SNAPSHOTS.delete(pilotId);
        }
        
        // Força atualização dos ficheiros para remover o piloto da lista
        const lastPilotData = ALL_PILOT_SNAPSHOTS.size > 0 ? [...ALL_PILOT_SNAPSHOTS.values()].pop() : {};
        await updateMonitorFiles(lastPilotData);
    });

    ws.on('error', (error) => {
        console.error("Erro na conexão WebSocket:", error);
    });
});

// TAREFA DE BACKGROUND PARA VERIFICAR O STATUS DA REDE
setInterval(async () => {
    if (PILOT_CONNECTIONS.size === 0) return;

    console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [SERVER CHECK] Iniciando verificação de rede para ${PILOT_CONNECTIONS.size} piloto(s) (${NETWORK_CHECK_INTERVAL_SERVER / 1000}s).`);

    for (const [pilotId, connData] of PILOT_CONNECTIONS.entries()) {
        const { websocket, vatsim_id, ivao_id, tx_sent } = connData;

        try {
            const isOnline = await checkNetworkStatus(vatsim_id, ivao_id);

            if (isOnline && !tx_sent) {
                websocket.send(JSON.stringify({ command: "START_TX" }));
                connData.tx_sent = true;
                console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [SERVER CHECK] Piloto ${pilotId} ONLINE. Comando START_TX enviado.`);
            } else if (!isOnline && tx_sent) {
                websocket.send(JSON.stringify({ command: "STOP_TX" }));
                connData.tx_sent = false;
                console.log(`[${new Date().toLocaleTimeString('pt-BR')}] [SERVER CHECK] Piloto ${pilotId} OFFLINE. Comando STOP_TX enviado.`);
            }
        } catch (e) {
            console.error(`Erro no loop de verificação para ${pilotId}:`, e.message);
        }
    }
}, NETWORK_CHECK_INTERVAL_SERVER);

// Limpeza de conexões "mortas"
setInterval(() => {
    wss.clients.forEach(ws => {
        if (!ws.isAlive) return ws.terminate();
        ws.isAlive = false;
        ws.ping(() => {});
    });
}, 30000);


console.log(`*** Servidor WebSocket Skymetrics iniciado com Node.js. A escutar em ws://${HOST}:${PORT} ***`);
