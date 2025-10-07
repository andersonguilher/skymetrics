// server.js - Roda na sua VPS (Replicação da Lógica Python)
import { WebSocketServer } from 'ws';
import { createServer } from 'http';
import * as fs from 'fs/promises';
import * as path from 'path';
import axios from 'axios';
import { fileURLToPath } from 'url';
import https from 'https'; // Import necessário para Axios com SSL

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
const SUBMIT_LOG_URL = "https://kafly.com.br/dash/utils/submit_flight_log.php";

// Constantes de Lógica de Voo e Limite
const ALERT_RATE_LIMIT_MS = 60 * 1000; // 60 segundos
const IAS_TAXI_START_KTS = 10;        // IAS >= 10 kts para registrar o INÍCIO DO VOOs

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
// *** INJEÇÃO DE AGENTE HTTPS PARA IGNORAR VERIFICAÇÃO SSL/TLS ***
// Desativa a verificação de SSL/TLS para o Axios (necessário em muitos ambientes VPS)
const agent = new https.Agent({ rejectUnauthorized: false });
// ---------------------------------------------------------


// ---------------------------------------------------------
// 2. FUNÇÕES AUXILIARES
// ---------------------------------------------------------

const getTimestamp = () => new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function register(ws) {
    USERS.add(ws);
    ws.pilot_id = "ANON";
    ws.pilot_name = "ANÔNIMO";
    ws.vatsim_id = "N/A";
    ws.ivao_id = "N/A";
    console.log(`[${getTimestamp()}] NOVO CLIENTE CONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}

async function unregister(ws) {
    if (!USERS.has(ws)) return;

    const pilot_name = ws.pilot_name || "ANÔNIMO";
    const clientState = CLIENT_FLIGHT_LOGS[pilot_name];

    if (pilot_name !== "ANÔNIMO") {

        // --- NOVO: Lógica de Logoff ou Descarte ---
        if (clientState && !clientState.flight_ended) {

            if (clientState.initial_fuel_logged) {

                await logEvent(pilot_name, "CONEXAO_PERDIDA", "Conexão encerrada abruptamente (cliente ou rede). Tentando enviar log acumulado.", ALL_PILOT_SNAPSHOTS[pilot_name] || {});

                await postFullFlightLog(pilot_name);

            } else {
                console.log(`[${getTimestamp()}] [LOG DISCARD] Descartando logs para ${pilot_name}: Voo não iniciado (Aeronave Cold & Dark).`);
                if (clientState.event_log) {
                    clientState.event_log = [];
                }
            }
            clientState.flight_ended = true;
        }
        // --- FIM DA Lógica de Logoff ou Descarte ---

        if (ALL_PILOT_SNAPSHOTS[pilot_name]) {
            delete ALL_PILOT_SNAPSHOTS[pilot_name];
        }
        if (PILOT_CONNECTIONS[pilot_name]) {
            delete PILOT_CONNECTIONS[pilot_name];
        }
    }

    USERS.delete(ws);

    let data_to_update = { alt_ind: 0, vs: 0, ias: 0, eng_combustion: 0, vatsim_id: "N/A", ivao_id: "N/A", pilot_id: "N/A", pilot_name: "N/A", packets_sent: 0, mb_sent: 0.0 };
    if (Object.keys(ALL_PILOT_SNAPSHOTS).length > 0) {
        data_to_update = Object.values(ALL_PILOT_SNAPSHOTS)[0];
    }

    if (!data_to_update.pilot_name && data_to_update.pilot_id !== "N/A" && PILOT_CONNECTIONS[data_to_update.pilot_id]) {
        data_to_update.pilot_name = PILOT_CONNECTIONS[data_to_update.pilot_id].pilot_name;
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
 * @param {object} logEntry - O objeto de evento formatado para o PHP.
 */
async function sendEventToPHP(logEntry) {
    try {
        const response = await axios.post(SUBMIT_LOG_URL, logEntry, {
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            timeout: 10000,
            httpsAgent: agent // Usa o agente que ignora o erro SSL
        });

        console.log(`[${getTimestamp()}] [SUBMIT LOG] Evento ${logEntry.event} enviado. Resposta: ${response.data.message}`);
        return 'SUCCESS';
    } catch (e) {
        if (e.response && e.response.status === 400) {
            const phpMessage = e.response.data && e.response.data.message ? e.response.data.message : 'Nenhuma mensagem de erro no corpo do 400.';
            console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO CRÍTICO ao enviar evento ${logEntry.event || 'undefined'}: Request failed with status code 400. Motivo PHP: ${phpMessage}`);
            return 'CRITICAL_ERROR';
        }
        console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO CRÍTICO ao enviar evento ${logEntry.event || 'undefined'}: ${e.message}`);
        return 'CRITICAL_ERROR';
    }
}

/**
 * Armazena o evento localmente (sem enviar ao PHP).
 * @param {string} pilot_name 
 * @param {string} event_name 
 * @param {string} description 
 * @param {object} snapshot - O snapshot de dados atual
 */
async function logEvent(pilot_name, event_name, description, snapshot) {
    // 1. Log to console
    console.log(`[${getTimestamp()}] [EVENTO] Piloto ${pilot_name}: ${event_name} -> ${description} (Armazenado localmente)`);

    if (!CLIENT_FLIGHT_LOGS[pilot_name]) {
        console.error(`[${getTimestamp()}] [FLIGHT LOG] Estado de voo não inicializado para ${pilot_name}. Não é possível logar.`);
        return;
    }

    // Determine the actual userId for the database (IVAO/VATSIM ID)
    const connData = PILOT_CONNECTIONS[pilot_name];
    let actualUserId = snapshot.pilot_id || "N/A";
    if (connData) {
        if (connData.ivao_id !== "N/A") {
            actualUserId = connData.ivao_id;
        } else if (connData.vatsim_id !== "N/A") {
            actualUserId = connData.vatsim_id;
        }
    }

    // 2. Prepare log entry (Formato limpo para o PHP)
    const flightPlan = CLIENT_FLIGHT_LOGS[pilot_name];

    // --- Valores Seguros ---
    // Garante que o combustível seja sempre um número para logs.
    const safeTotalFuel = snapshot.total_fuel || 0.0;

    const logEntry = {
        // Campos obrigatórios para o PHP (busca)
        userId: actualUserId,
        departureId: flightPlan.flightPlan_departureId,
        arrivalId: flightPlan.flightPlan_arrivalId,

        // CHAVE: Campos Limpos para o Log JSON
        data_hora: new Date().toISOString(), // Mapeado como data_hora no PHP
        evento: event_name,                 // Mapeado como evento no PHP
        lat: snapshot.lat || 0.0,
        lng: snapshot.lng || 0.0,
        descricao: description, // Mantém a descrição por padrão
    };

    // 3. Adiciona campos específicos para o log JSON e trata undefined/limpeza
    if (event_name === 'VS_NO_TOQUE') {
        const vsMatch = description.match(/(-?\d+)/);
        const vsValue = vsMatch ? parseFloat(vsMatch[1]) : 0.0;
        logEntry.landing_vs = vsValue;

    } else if (event_name === 'COMBUSTIVEL_INICIAL') {
        logEntry.total_fuel = safeTotalFuel; // Usa o total_fuel para o log JSON
        logEntry.descricao = `Motor ligado. Combustível: ${formatNumber(safeTotalFuel, 0)} gal`;

    } else if (event_name === 'COMBUSTIVEL_FINAL') {
        logEntry.total_fuel = safeTotalFuel; // Usa o total_fuel para o log JSON
        logEntry.descricao = `Combustível final registrado: ${formatNumber(safeTotalFuel, 0)} gal`;

    }
    // Para todos os outros eventos, mantemos os campos básicos (time, event, lat, lng) e a description.

    // 4. Store locally
    flightPlan.event_log.push(logEntry);
}


/**
 * Envia todos os eventos acumulados para o endpoint PHP sequencialmente, com retentativas.
 * @param {string} pilot_name 
 */
async function postFullFlightLog(pilot_name) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 5000; // 5 segundos
    const flightState = CLIENT_FLIGHT_LOGS[pilot_name];

    if (!flightState || flightState.event_log.length === 0) {
        console.warn(`[${getTimestamp()}] [SUBMIT LOG] Nenhum evento acumulado para o piloto ${pilot_name} enviar.`);
        return;
    }

    const logCopy = [...flightState.event_log]; // Use uma cópia para as retentativas
    let success = false;

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        console.log(`[${getTimestamp()}] [SUBMIT LOG] Iniciando tentativa ${attempt}/${MAX_RETRIES} de envio de ${logCopy.length} eventos em lote para o piloto ${pilot_name}...`);

        let allEventsSucceeded = true;

        for (const logEntry of logCopy) {
            const result = await sendEventToPHP(logEntry);

            if (result === 'CRITICAL_ERROR') {
                allEventsSucceeded = false;
                break;
            }
            // Se for sucesso ('SUCCESS'), continua para o próximo evento no loop interno.
        }

        if (allEventsSucceeded) {
            success = true;
            console.log(`[${getTimestamp()}] [SUBMIT LOG] Envio em lote concluído com SUCESSO na tentativa ${attempt}.`);
            // Limpa o buffer de log local após o sucesso final
            CLIENT_FLIGHT_LOGS[pilot_name].event_log = [];
            break;
        }

        if (attempt < MAX_RETRIES) {
            console.warn(`[${getTimestamp()}] [SUBMIT LOG] Aguardando ${RETRY_DELAY_MS / 1000}s antes da próxima retentativa...`);
            await delay(RETRY_DELAY_MS);
        } else {
            // Se for FALHA e estiver na última tentativa, não limpamos o log.
            console.error(`[${getTimestamp()}] [SUBMIT LOG] FALHA CRÍTICA: O envio falhou após ${MAX_RETRIES} tentativas. O log foi mantido na memória.`);
        }
    }
}


// --- Lógica de Verificação de Status Online na IVAO/VATSIM ---

// MODIFICADO: Inclui log de diagnóstico
async function isPilotOnlineIVAO(ivao_id) {
    if (!ivao_id || ivao_id.trim() === 'N/A' || ivao_id.trim() === '' || ivao_id.trim() === '0') return null;
    const ivao_id_int = parseInt(ivao_id.trim());
    if (isNaN(ivao_id_int)) return null;

    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000 });
        const data = response.data;
        for (const client of data.clients.pilots) {
            if (client.userId === ivao_id_int && client.flightPlan) {
                // Log de sucesso
                console.log(`[${getTimestamp()}] [IVAO CHECK] SUCESSO: Piloto ${ivao_id} encontrado online.`);
                return client.flightPlan;
            }
        }
        return null;
    } catch (e) {
        // Log de erro crítico (Firewall, Network, Timeout)
        console.error(`[${getTimestamp()}] [IVAO CHECK] ERRO CRÍTICO ao consultar API IVAO para ID ${ivao_id}. Erro: ${e.message}`);
        return null;
    }
}

// MODIFICADO: Inclui log de diagnóstico
async function isPilotOnlineVATSIM(vatsim_id) {
    if (!vatsim_id || vatsim_id.trim() === 'N/A' || vatsim_id.trim() === '' || vatsim_id.trim() === '0') return false;
    const vatsim_id_int = parseInt(vatsim_id.trim());
    if (isNaN(vatsim_id_int)) return false;

    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000 });
        const data = response.data;
        for (const pilot of data.pilots) {
            if (pilot.cid === vatsim_id_int) {
                // Log de sucesso
                console.log(`[${getTimestamp()}] [VATSIM CHECK] SUCESSO: Piloto ${vatsim_id} encontrado online.`);
                return true;
            }
        }
        return false;
    } catch (e) {
        // Log de erro crítico (Firewall, Network, Timeout)
        console.error(`[${getTimestamp()}] [VATSIM CHECK] ERRO CRÍTICO ao consultar API VATSIM para ID ${vatsim_id}. Erro: ${e.message}`);
        return false;
    }
}


// A função getPilotFlightPlan não foi alterada.
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


// A função checkNetworkStatus não foi alterada.
async function checkNetworkStatus(vatsim_id, ivao_id) {
    const isVatsimOnline = await isPilotOnlineVATSIM(vatsim_id);
    const isIvaoOnline = !!(await isPilotOnlineIVAO(ivao_id));
    return isVatsimOnline || isIvaoOnline;
}


// TAREFA DE BACKGROUND PARA VERIFICAR O STATUS DA REDE

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

        const pilotNames = Object.keys(PILOT_CONNECTIONS);
        console.log(`[${getTimestamp()}] [SERVER CHECK] Iniciando verificação de rede para ${pilotNames.length} piloto(s) (120s).`);
        LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;

        const pilotsToRemove = [];

        for (const pilotName of pilotNames) {
            const connData = PILOT_CONNECTIONS[pilotName];
            if (!connData) continue;

            const ws = connData.websocket;
            const vatsimId = connData.vatsim_id;
            const ivaoId = connData.ivao_id;

            if (pilotName === "ANÔNIMO" || (vatsimId === "N/A" && ivaoId === "N/A") || !ALL_PILOT_SNAPSHOTS[pilotName]) {
                continue;
            }

            try {
                // LOG DE DIAGNÓSTICO: Qual ID está sendo verificado
                console.log(`[${getTimestamp()}] [PERIODIC CHECK] Verificando Piloto: ${pilotName} (V: ${vatsimId} / I: ${ivaoId})`);

                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);
                const isTransmitting = connData.tx_sent;

                if (ws.readyState !== ws.OPEN) {
                    pilotsToRemove.push(pilotName);
                    continue;
                }

                // --- LÓGICA DE PAUSA INTELIGENTE ---
                const pilotSnapshot = ALL_PILOT_SNAPSHOTS[pilotName];
                const currentIas = pilotSnapshot.ias || 0;
                const currentOnGround = pilotSnapshot.on_ground || 1;

                // NOVO: Verifica se o voo atingiu o ponto de taxi inicial.
                const flightState = CLIENT_FLIGHT_LOGS[pilotName];
                const isFlightInitiated = flightState && flightState.initial_fuel_logged;


                const isStuckOnGround = currentOnGround === 1 && currentIas < 5 && isOnline;

                // A PAUSA INTELIGENTE só se aplica se o voo tiver sido iniciado (após IAS >= 10 kts)
                if (isStuckOnGround && isTransmitting && isFlightInitiated) {
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
                        logEvent(pilotName, "PAUSA_INTELIGENTE", "Pouso/Solo detectado (5min). Transmissão pausada para economia de dados.", pilotSnapshot); // Log localmente
                        continue;
                    }
                }
                else if (connData.last_stop_time && (currentIas > 5 || currentOnGround === 0)) {
                    connData.last_stop_time = null;
                }

                // --- LÓGICA DE REDE PADRÃO ---
                if (isOnline) {
                    if (!isTransmitting) {
                        // Se o piloto não está transmitindo, mas está online e começou a se mover/voar, enviamos START_TX
                        if (currentIas > 5 || currentOnGround === 0) {
                            const command = JSON.stringify({ command: "START_TX" });
                            await ws.send(command);
                            connData.tx_sent = true;
                            console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotName} ONLINE. Comando START_TX enviado.`);
                        }
                    }
                } else {
                    if (isTransmitting) {
                        const command = JSON.stringify({ command: "STOP_TX" });
                        await ws.send(command);
                        connData.tx_sent = false;
                        connData.last_stop_time = new Date();
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotName} OFFLINE na rede. Comando STOP_TX enviado (Conexão mantida).`);
                    }
                }

            } catch (e) {
                console.log(`[${getTimestamp()}] [SERVER CHECK] Erro processando/enviando comando para ${pilotName}: ${e.message}`);
                pilotsToRemove.push(pilotName);
            }
        }

        for (const pilotName of pilotsToRemove) {
            if (PILOT_CONNECTIONS[pilotName]) {
                delete PILOT_CONNECTIONS[pilotName];
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

    const pilotNames = Object.keys(PILOT_CONNECTIONS);

    if (pilotNames.length === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #A9A9A9;">Nenhum cliente conectado no momento.</td></tr>';
    }

    for (const pilot_name of pilotNames) {
        const connData = PILOT_CONNECTIONS[pilot_name];
        const data = ALL_PILOT_SNAPSHOTS[pilot_name];
        const conn_status = connData ? connData.tx_sent : false;

        const alt = data ? formatNumber(data.alt_ind || 0, 0) : "N/A";
        const vs = data ? formatNumber(data.vs || 0, 0) : "N/A";
        const ias = data ? formatNumber(data.ias || 0, 0) : "N/A";

        const vatsim = connData.vatsim_id || 'N/A';
        const ivao = connData.ivao_id || 'N/A';

        let status_text;
        let status_class;

        let network_display_final = 'N/A';

        // 1. Determinação do Status de Exibição
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

        // 2. Determinação do Conteúdo da Coluna VATSIM / IVAO
        if (ivao !== 'N/A' && (conn_status || status_text === "OFFLINE/COLD")) {
            network_display_final = 'Ivao';
        } else if (vatsim !== 'N/A' && (conn_status || status_text === "OFFLINE/COLD")) {
            network_display_final = 'Vatsim';
        }

        rows_html += `
                <tr class="pilot-row ${status_class}">
                    <td class="pilot-id">${pilot_name}</td> <td>${network_display_final}</td> <td>${status_text}</td>
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
        "pilot_name": data.pilot_name || "N/A",
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
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background-color: #f4f4f4;
            color: #333;
        }

        .container {
            width: 90%;
            max-width: 1100px;
            margin: 20px auto;
            padding: 20px;
            background-color: #fff;
            box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
        }

        h1 {
            color: #34495e;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }

        h2 {
            color: #2c3e50;
            margin-top: 20px;
        }

        /* --- Status Box (Server Status) --- */
        .status-box {
            padding: 10px 15px;
            margin-bottom: 20px;
            font-weight: bold;
            color: white;
            border-radius: 4px;
            text-align: center;
        }

        .status-connected {
            background-color: #2ecc71; /* Green */
        }

        /* --- Data Tables --- */
        .data-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }

        .data-table thead th {
            background-color: #3498db;
            color: white;
            padding: 12px 15px;
            text-align: left;
            border: 1px solid #2980b9;
        }

        .data-table tbody td {
            padding: 10px 15px;
            border: 1px solid #ecf0f1;
            vertical-align: middle;
        }

        .data-table tbody tr:nth-child(even) {
            background-color: #f9f9f9;
        }

        /* Pilot Status Colors */
        .pilot-row.status-airborne { background-color: #d4edda; color: #155724; }
        .pilot-row.status-taxiing { background-color: #fff3cd; color: #856404; }
        .pilot-row.status-ready { background-color: #d1ecf1; color: #0c5460; }
        .pilot-row.status-paused { background-color: #f8d7da; color: #721c24; }
        .pilot-row.status-cold { background-color: #e9ecef; color: #6c757d; }
        .pilot-row.status-pending { background-color: #e2e3e5; color: #383d41; }
        
        .pilot-id {
            font-weight: bold;
            color: #34495e;
        }

        /* Stats Table Specific Styling */
        .stats-row .stats-label {
            font-weight: bold;
            width: 60%;
        }

        .stats-row .stats-value {
            text-align: right;
            font-weight: bold;
            color: #2c3e50;
        }

        /* Map Styling */
        #map {
            height: 400px;
            width: 100%;
            margin-top: 10px;
            border: 1px solid #ccc;
        }

        /* Hide leaflet default attribution (optional) */
        .leaflet-control-attribution {
            display: none;
        }
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
                    <th>Nome do Piloto</th>
                    <th>Rede</th>
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
                                .bindPopup('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts')
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
                            marker.getPopup().setContent('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts');
                            
                            if (!map.getBounds().contains(newLatLng)) {
                                map.setView(newLatLng, map.getZoom()); 
                            }

                        } else {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>IAS: ' + data.ias + ' kts')
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

            const pilotName = String(data.pilot_name || "ANÔNIMO"); // NOVO: Captura o nome
            pilotId = pilotName; // CHAVE: Usa o nome como ID/chave primária para os maps

            // --- LÓGICA DE CONEXÃO E CHECK INICIAL ---
            if (pilotId !== "ANÔNIMO" && !PILOT_CONNECTIONS[pilotId]) {
                const vatsimId = String(data.vatsim_id || "N/A");
                const ivaoId = String(data.ivao_id || "N/A");

                // LOG DE DIAGNÓSTICO: Qual ID está sendo verificado na conexão
                console.log(`[${getTimestamp()}] [INITIAL CHECK] Verificando Piloto: ${pilotName} (V: ${vatsimId} / I: ${ivaoId})`);

                // NOVO: Obtém o plano de voo da rede antes de inicializar o log
                const flightPlanDetails = await getPilotFlightPlan(vatsimId, ivaoId);

                // -----------------------------------------------------------
                // 🟢 CORREÇÃO CRÍTICA: GARANTIR CAIXA ALTA (UPPERCASE) E TRIM
                // -----------------------------------------------------------
                const depId = flightPlanDetails.departureId ? String(flightPlanDetails.departureId).trim().toUpperCase() : "N/A";
                const arrId = flightPlanDetails.arrivalId ? String(flightPlanDetails.arrivalId).trim().toUpperCase() : "N/A";
                // -----------------------------------------------------------

                // O banco de dados (PHP) espera o ID de rede para buscar o registro de voo.
                const logUserId = flightPlanDetails.networkUserId || data.pilot_id || "N/A"; // Usa ID de rede ou fallback do cliente


                if (!CLIENT_FLIGHT_LOGS[pilotId]) { // Keyed by Name
                    CLIENT_FLIGHT_LOGS[pilotId] = {
                        is_airborne: false,
                        has_landed: true,
                        initial_fuel_logged: false,
                        landing_vs: null,
                        last_vs: 0.0,
                        flight_ended: false,
                        event_log: [],
                        last_alert_timestamps: {}, // NOVO: Armazena o timestamp dos últimos alertas
                        // CHAVE: Usar os IDs normalizados (corrigidos)
                        flightPlan_departureId: depId,
                        flightPlan_arrivalId: arrId,
                    };

                    console.log(`[${getTimestamp()}] [FLIGHT LOG] Piloto ${pilotId} (Log ID: ${logUserId}) iniciado com DEP: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_departureId} / ARR: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_arrivalId}`);
                    // O evento INICIO_SESSAO é o primeiro a ser logado
                    logEvent(pilotId, "INICIO_SESSAO", `Sessão de telemetria iniciada. DEP: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_departureId}, ARR: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_arrivalId}. (Usando ID de Rede ${logUserId} para log)`, data);
                }


                ws.pilot_id = pilotId; // Armazena o nome como a chave de lookup
                ws.pilot_name = pilotName; // Armazena o nome de exibição
                ws.vatsim_id = vatsimId;
                ws.ivao_id = ivaoId;

                PILOT_CONNECTIONS[pilotId] = { // Keyed by Name
                    websocket: ws,
                    pilot_name: pilotName, // Armazena o nome
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
            if (pilotId in PILOT_CONNECTIONS) { // Keyed by Name
                data.pilot_name = pilotName;
                data.pilot_id = PILOT_CONNECTIONS[pilotId].vatsim_id || PILOT_CONNECTIONS[pilotId].ivao_id || "N/A"; // Adiciona o ID de rede ao snapshot (para o mapa)
                ALL_PILOT_SNAPSHOTS[pilotId] = data; // Keyed by Name
            }

            // CHAVE: Só processa a lógica de eventos e a escrita dos arquivos se estivermos transmitindo
            if (!PILOT_CONNECTIONS[pilotId] || !PILOT_CONNECTIONS[pilotId].tx_sent) { // Keyed by Name
                // Atualiza a página de monitoramento mesmo sem transmitir dados de voo
                await updateMonitorFiles(data, packetsReceivedCount, totalBytesReceived);
                return;
            }

            // --- 2. INICIALIZAÇÃO E ATUALIZAÇÃO DE ESTADO ---
            const currentState = CLIENT_FLIGHT_LOGS[pilotId]; // Keyed by Name

            // --- LÓGICA DE RATE LIMITING PARA ALERTAS ---
            const currentTime = Date.now();
            const ALERT_RATE_LIMIT_MS = 60 * 1000; // 60 segundos
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

            // A. DETECÇÃO DO INÍCIO DO VOO (TAXI START)
            if (currentState.has_landed && !currentState.is_airborne && !currentState.initial_fuel_logged && engCombustion === 1 && currentOnGround === 1 && currentIas >= IAS_TAXI_START_KTS) {
                await logEvent(pilotId, "INICIO_VOO", `Início de taxi detectado. IAS >= ${IAS_TAXI_START_KTS} kts no solo.`, data);
                await logEvent(pilotId, "COMBUSTIVEL_INICIAL", `Motor ligado. Combustível: ${formatNumber(data.total_fuel || 0, 0)} gal`, data);
                currentState.initial_fuel_logged = true; // Flag para indicar que o voo começou e o fuel foi logado.
                currentState.has_landed = false; // Não está mais no estado de "pousado/resetado"
            }

            // B. DECOLAGEM (Agora depende de initial_fuel_logged = true)
            if (!currentState.is_airborne && currentState.initial_fuel_logged && currentAgl > 50 && currentIas > 40) {
                currentState.is_airborne = true;
                currentState.has_landed = false;
                currentState.flight_ended = false;
                await logEvent(pilotId, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.", data);
            }

            // C. POUSO (Toque e Parada)
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
            if (currentState.initial_fuel_logged && currentState.has_landed && !currentState.flight_ended && engCombustion === 0) {
                currentState.flight_ended = true;

                // 1. Loga o evento COMBUSTIVEL_FINAL (Novo evento)
                await logEvent(pilotId, "COMBUSTIVEL_FINAL", `Motor desligado. Combustível final: ${formatNumber(data.total_fuel || 0, 0)} gal`, data);

                // 2. Loga o evento final
                await logEvent(pilotId, "VOO_FINALIZADO", "Fim da sessão de voo. Log de voo será enviado.", data);

                // 3. Envia TODO o log acumulado
                await postFullFlightLog(pilotId);

                // 4. Reinicializa o estado (como estava antes)
                currentState.is_airborne = false;
                currentState.has_landed = true;
                currentState.initial_fuel_logged = false;
                currentState.landing_vs = null;
                currentState.last_alert_timestamps = {};
            }


            // H. POUSO RESET (Touch-and-Go)
            if (currentState.initial_fuel_logged && currentState.has_landed && currentOnGround === 1 && currentIas >= IAS_TAXI_START_KTS) {

                // CHAVE: Antes de resetar, finalize e envie o log do segmento anterior
                if (currentState.event_log.length > 0) {
                    await logEvent(pilotId, "SEGMENTO_CONCLUIDO", "Segmento de voo anterior concluído (Touch-and-Go ou re-takeoff). Enviando logs acumulados.", data);
                    await postFullFlightLog(pilotId); // Envia o log e limpa o buffer
                }

                // Reinicia o estado para começar um novo voo
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
        // Usa pilot_id (que é o nome) para o unregister
        await unregister(ws);
        if (ws.pilot_id !== "ANÔNIMO" && PILOT_CONNECTIONS[ws.pilot_id]) {
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
        "pilot_id": "N/A", "pilot_name": "N/A", "vatsim_id": "N/A", "ivao_id": "N/A",
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