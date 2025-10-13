// node_server/state_manager.js

import { getTimestamp, formatNumber } from './utils.js';
import { checkNetworkStatus, getPilotFlightPlan } from './network_checker.js';
import { GS_TAXI_START_KTS, initialPilotSnapshot, GLOBAL_STATE } from './config.js';


// --- Variáveis de Estado Globais (Encapsuladas) ---
const USERS = new Set();
// REMOVIDO: const CLIENT_FLIGHT_LOGS = {}; // Lógica transferida para o cliente
const ALL_PILOT_SNAPSHOTS = {};
const PILOT_CONNECTIONS = {};


// --- GETTERS E SETTERS ---
export const getGlobalState = () => GLOBAL_STATE;
// REMOVIDO: export const getClientFlightLogs = () => CLIENT_FLIGHT_LOGS;
export const getAllPilotSnapshots = () => ALL_PILOT_SNAPSHOTS;
export const getPilotConnections = () => PILOT_CONNECTIONS;

export function initializeGlobalState(startTime) {
    GLOBAL_STATE.SERVER_START_TIME = startTime;
}


// --- Funções de Comando ---
export function startTx(pilotName, ws) {
    const command = JSON.stringify({ command: "START_TX" });
    ws.send(command);
    PILOT_CONNECTIONS[pilotName].tx_sent = true;
    console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotName} ONLINE/EM VOO. Comando START_TX enviado.`);
}

export function stopTx(pilotName, ws) {
    const command = JSON.stringify({ command: "STOP_TX" });
    ws.send(command);
    PILOT_CONNECTIONS[pilotName].tx_sent = false;
    PILOT_CONNECTIONS[pilotName].last_stop_time = new Date();
}

/**
 * Remove a conexão do piloto de todos os estados (usado no network_checker).
 * @param {string} pilotName
 */
export async function removePilotConnection(pilotName) {
    const connData = PILOT_CONNECTIONS[pilotName];
    if (connData && connData.websocket) {
        // unregister já trata o log de sessão e remove do estado
        await unregister(connData.websocket);
    } else {
        // Se a conexão não tinha websocket (erro), remove diretamente
        if (ALL_PILOT_SNAPSHOTS[pilotName]) delete ALL_PILOT_SNAPSHOTS[pilotName];
        if (PILOT_CONNECTIONS[pilotName]) delete PILOT_CONNECTIONS[pilotName];
        // REMOVIDO: if (CLIENT_FLIGHT_LOGS[pilotName]) delete CLIENT_FLIGHT_LOGS[pilotName];
    }
}


// REMOVIDO: logEvent, shouldLogAlert, processFlightEvents (Lógica transferida para o cliente)


// --- Funções de Gerenciamento de Conexão ---

export function register(ws) {
    USERS.add(ws);
    ws.pilot_id = "ANON"; // Usado internamente para o websocket
    ws.pilot_name = "ANÔNIMO";
    ws.vatsim_id = "N/A";
    ws.ivao_id = "N/A";
    console.log(`[${getTimestamp()}] NOVO CLIENTE CONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
}

export async function unregister(ws) {
    if (!USERS.has(ws)) return initialPilotSnapshot;

    const pilot_name = ws.pilot_name || "ANÔNIMO";
    // REMOVIDO: const clientState = CLIENT_FLIGHT_LOGS[pilot_name];

    if (pilot_name !== "ANÔNIMO") {

        // REMOVIDO: Lógica para logar CONEXAO_PERDIDA e postFullFlightLog (Transferido para o cliente)

        if (ALL_PILOT_SNAPSHOTS[pilot_name]) delete ALL_PILOT_SNAPSHOTS[pilot_name];
        if (PILOT_CONNECTIONS[pilot_name]) delete PILOT_CONNECTIONS[pilot_name];
        // REMOVIDO: if (CLIENT_FLIGHT_LOGS[pilot_name]) delete CLIENT_FLIGHT_LOGS[pilot_name];
    }

    USERS.delete(ws);

    let data_to_update = initialPilotSnapshot;
    const activePilots = Object.values(ALL_PILOT_SNAPSHOTS);
    if (activePilots.length > 0) {
        data_to_update = activePilots[0];
        data_to_update.pilot_name = data_to_update.pilot_id !== "N/A" && PILOT_CONNECTIONS[data_to_update.pilot_id] ? PILOT_CONNECTIONS[data_to_update.pilot_id].pilot_name : "N/A";
        data_to_update.pilot_id = data_to_update.pilot_id !== "N/A" && PILOT_CONNECTIONS[data_to_update.pilot_id] ? data_to_update.pilot_id : "N/A";
    }

    console.log(`[${getTimestamp()}] CLIENTE DESCONECTADO: ${ws._socket.remoteAddress}. Total: ${USERS.size}`);
    return data_to_update;
}


export async function handleNewConnection(ws) {
    register(ws);

    ws.on('message', async (message) => {
        try {
            const messageString = message.toString();
            const messageSize = Buffer.byteLength(messageString, 'utf8');

            GLOBAL_STATE.totalBytesReceived += messageSize;
            GLOBAL_STATE.packetsReceivedCount += 1;

            const data = JSON.parse(messageString);
            const pilotName = String(data.pilot_name || "ANÔNIMO");
            const pilotId = pilotName;

            const pilotConnections = getPilotConnections();
            const allPilotSnapshots = getAllPilotSnapshots();

            if (pilotId !== "ANÔNIMO" && !pilotConnections[pilotId]) {
                const vatsimId = String(data.vatsim_id || "N/A");
                const ivaoId = String(data.ivao_id || "N/A");

                // REMOVIDO: Lógica de checagem de flight plan e inicialização de log (Transferido para o cliente)

                ws.pilot_id = pilotId;
                ws.pilot_name = pilotName;
                ws.vatsim_id = vatsimId;
                ws.ivao_id = ivaoId;

                pilotConnections[pilotId] = {
                    websocket: ws, pilot_name: pilotName, vatsim_id: vatsimId, ivao_id: ivaoId,
                    tx_sent: false, last_stop_time: null,
                };

                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);

                if (isOnline) {
                    startTx(pilotId, ws);
                } else {
                    stopTx(pilotId, ws);
                }
            }

            if (pilotId in pilotConnections) {
                data.pilot_name = pilotName;
                data.pilot_id = pilotConnections[pilotId].vatsim_id || pilotConnections[pilotId].ivao_id || "N/A";
                allPilotSnapshots[pilotId] = data;

                // REMOVIDO: Chamada para processFlightEvents (Transferido para o cliente)
            }

        } catch (e) {
            if (e instanceof SyntaxError) {
                console.error(`[${getTimestamp()}] [ERROR HANDLER] Erro de parse JSON de ${ws.pilot_name || 'ANÔNIMO'}: ${e.message}`);
            } else {
                console.error(`[${getTimestamp()}] [ERROR HANDLER] Erro no fluxo de dados para ${ws.pilot_name || 'ANÔNIMO'}: ${e.message}`);
            }
        }
    });

    ws.on('close', async () => {
        await unregister(ws);
    });

    ws.on('error', (error) => {
        console.error(`[${getTimestamp()}] [WS ERROR] Erro na conexão para ${ws.pilot_name || ws._socket.remoteAddress}: ${error.message}`);
    });
}