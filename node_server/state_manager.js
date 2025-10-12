// node_server/state_manager.js

import { getTimestamp, formatNumber } from './utils.js';
import { postFullFlightLog } from './log_submitter.js';
import { checkNetworkStatus, getPilotFlightPlan } from './network_checker.js';
import { GS_TAXI_START_KTS, ALERT_RATE_LIMIT_MS, initialPilotSnapshot, GLOBAL_STATE } from './config.js';


// --- Variáveis de Estado Globais (Encapsuladas) ---
const USERS = new Set();
const CLIENT_FLIGHT_LOGS = {};
const ALL_PILOT_SNAPSHOTS = {};
const PILOT_CONNECTIONS = {};


// --- GETTERS E SETTERS ---
export const getGlobalState = () => GLOBAL_STATE;
export const getClientFlightLogs = () => CLIENT_FLIGHT_LOGS;
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
        if (CLIENT_FLIGHT_LOGS[pilotName]) delete CLIENT_FLIGHT_LOGS[pilotName];
    }
}


/**
 * Armazena o evento localmente (logEvent).
 */
export async function logEvent(pilot_name, event_name, description, snapshot) {
    console.log(`[${getTimestamp()}] [EVENTO] Piloto ${pilot_name}: ${event_name} -> ${description} (Armazenado localmente)`);

    const flightPlan = CLIENT_FLIGHT_LOGS[pilot_name];
    const actualUserId = flightPlan.logUserId || 'N/A';
    const safeTotalFuel = snapshot.total_fuel || 0.0;
    const latString = String(snapshot.lat || 0.0);
    const lngString = String(snapshot.lng || 0.0);

    const logEntry = {
        userId: actualUserId,
        departureId: flightPlan.flightPlan_departureId,
        arrivalId: flightPlan.flightPlan_arrivalId,
        data_hora: new Date().toISOString(),
        evento: event_name,
        lat: latString,
        lng: lngString,
        descricao: description,
    };

    if (event_name === 'VS_NO_TOQUE') {
        const vsValue = snapshot.landing_vs || 0.0;
        logEntry.landing_vs = vsValue;
    } else if (['COMBUSTIVEL_INICIAL', 'COMBUSTIVEL_FINAL'].includes(event_name)) { // CORREÇÃO APLICADA: Uso de .includes()
        logEntry.total_fuel = safeTotalFuel;
    }

    flightPlan.event_log.push(logEntry);
}


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
    const clientState = CLIENT_FLIGHT_LOGS[pilot_name];

    if (pilot_name !== "ANÔNIMO") {

        if (clientState && !clientState.flight_ended) {
            if (clientState.initial_fuel_logged) {
                await logEvent(pilot_name, "CONEXAO_PERDIDA", "Conexão encerrada abruptamente.", ALL_PILOT_SNAPSHOTS[pilot_name] || {});
                await postFullFlightLog(pilot_name, clientState);
            } else {
                if (clientState.event_log) clientState.event_log = [];
            }
            clientState.flight_ended = true;
        }

        if (ALL_PILOT_SNAPSHOTS[pilot_name]) delete ALL_PILOT_SNAPSHOTS[pilot_name];
        if (PILOT_CONNECTIONS[pilot_name]) delete PILOT_CONNECTIONS[pilot_name];
        if (CLIENT_FLIGHT_LOGS[pilot_name]) delete CLIENT_FLIGHT_LOGS[pilot_name];
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


// --- Lógica Principal de Eventos ---

function shouldLogAlert(alertName, lastAlerts) {
    const currentTime = Date.now();
    if (!lastAlerts[alertName] || currentTime - lastAlerts[alertName] >= ALERT_RATE_LIMIT_MS) {
        lastAlerts[alertName] = currentTime;
        return true;
    }
    return false;
}

export async function processFlightEvents(ws, data) {

    const pilotId = ws.pilot_name;
    const currentState = CLIENT_FLIGHT_LOGS[pilotId];

    // --- DETECÇÃO DE EVENTOS DE VOO ---
    const currentAgl = data.agl || 0;
    const currentGs = data.gs || data.ias || 0;
    const currentVs = data.vs || 0;
    const currentOnGround = data.on_ground || 0;
    const currentBank = data.plane_bank_degrees || 0;
    const engCombustion = data.eng_combustion || 0;
    const alerts = data.alerts || {};

    // A. INÍCIO DO VOO (TAXI START)
    if (currentState.has_landed && !currentState.is_airborne && !currentState.initial_fuel_logged && engCombustion === 1 && currentOnGround === 1 && currentGs >= GS_TAXI_START_KTS) {
        await logEvent(pilotId, "INICIO_VOO", `Início de taxi detectado. GS >= ${GS_TAXI_START_KTS} kts no solo.`, data);
        await logEvent(pilotId, "COMBUSTIVEL_INICIAL", `Motor ligado. Combustível: ${formatNumber(data.total_fuel || 0, 0)} gal`, data);
        currentState.initial_fuel_logged = true;
        currentState.has_landed = false;
        currentState.flight_ended = false;
    }

    // B. DECOLAGEM
    if (!currentState.is_airborne && currentState.initial_fuel_logged && currentAgl > 50 && currentGs > 40) {
        currentState.is_airborne = true;
        currentState.has_landed = false;
        currentState.flight_ended = false;
        await logEvent(pilotId, "DECOLAGEM", "Decolagem detectada. Aeronave no ar.", data);
    }

    // C. POUSO (Toque e Parada)
    if (currentState.is_airborne && currentOnGround === 1 && currentAgl < 100 && !currentState.has_landed) {
        if (currentState.landing_vs === null) currentState.landing_vs = currentState.last_vs;
        if (currentGs < 10) {
            currentState.has_landed = true;
            currentState.is_airborne = false;
            const vsNoToque = currentState.landing_vs || currentVs;
            data.landing_vs = vsNoToque;
            await logEvent(pilotId, "VS_NO_TOQUE", `Velocidade vertical no toque detectada: ${vsNoToque.toFixed(0)} fpm.`, data);
            await logEvent(pilotId, "POUSO_FINALIZADO", `Pouso concluído. VS no toque final: ${vsNoToque.toFixed(0)} fpm`, data);
        }
    }

    // D. ALERTA: BANK ANGLE (> 30°)
    if (Math.abs(currentBank) > 30) {
        if (shouldLogAlert("ALERTA:BANK_ANGLE_HIGH", currentState.last_alert_timestamps)) {
            await logEvent(pilotId, "ALERTA:BANK_ANGLE_HIGH", `Ângulo de inclinação excessivo: ${Math.abs(currentBank).toFixed(1)} graus.`, data);
        }
    }

    // E. ALERTA: STALL WARNING 
    if ((alerts.stall_warning || 0) === 1) {
        if (shouldLogAlert("ALERTA:STALL_WARNING", currentState.last_alert_timestamps)) {
            await logEvent(pilotId, "ALERTA:STALL_WARNING", "Alerta de estol (stall warning) ativo.", data);
        }
    }

    // F. OUTROS ALERTA (RATE LIMITED)
    if ((alerts.beacon_off_engine_on || 0) === 1) {
        if (shouldLogAlert("ALERTA:BEACON_OFF_ENGINE_ON", currentState.last_alert_timestamps)) {
            await logEvent(pilotId, "ALERTA:BEACON_OFF_ENGINE_ON", "Beacon Lights desligadas com o motor em funcionamento.", data);
        }
    }
    if ((alerts.engine_fire || 0) === 1) {
        if (shouldLogAlert("ALERTA:ENG_FIRE", currentState.last_alert_timestamps)) {
            await logEvent(pilotId, "ALERTA:ENG_FIRE", "Incêndio detectado no Motor.", data);
        }
    }

    // G. VOO FINALIZADO (Motor Desligado após Pouso)
    if (currentState.initial_fuel_logged && currentState.has_landed && !currentState.flight_ended && engCombustion === 0) {
        currentState.flight_ended = true;
        await logEvent(pilotId, "COMBUSTIVEL_FINAL", `Motor desligado. Combustível final: ${formatNumber(data.total_fuel || 0, 0)} gal`, data);
        await logEvent(pilotId, "VOO_FINALIZADO", "Fim da sessão de voo. Log de voo será enviado.", data);

        await postFullFlightLog(pilotId, currentState);

        currentState.is_airborne = false;
        currentState.has_landed = true;
        currentState.initial_fuel_logged = false;
        currentState.landing_vs = null;
        currentState.last_alert_timestamps = {};
    }

    // H. POUSO RESET (Touch-and-Go)
    if (currentState.initial_fuel_logged && currentState.has_landed && currentOnGround === 1 && currentGs >= GS_TAXI_START_KTS) {
        if (currentState.event_log.length > 0) {
            await logEvent(pilotId, "SEGMENTO_CONCLUIDO", "Segmento de voo anterior concluído (Touch-and-Go ou re-takeoff). Enviando logs acumulados.", data);
            await postFullFlightLog(pilotId, currentState);
        }

        currentState.is_airborne = false;
        currentState.has_landed = false;
        currentState.initial_fuel_logged = false;
        currentState.landing_vs = null;
        currentState.flight_ended = false;
        await logEvent(pilotId, "RESET_VOO", "Voando novamente ou táxi rápido após pouso. Reiniciando estado de voo.", data);
    }

    currentState.last_vs = currentVs;

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

                let depId = String(data.departureId || "N/A").trim().toUpperCase();
                let arrId = String(data.arrivalId || "N/A").trim().toUpperCase();
                let logUserId = vatsimId !== "N/A" ? vatsimId : ivaoId;

                let networkCheckRequired = !(depId !== "N/A" && arrId !== "N/A");

                if (networkCheckRequired) {
                    const flightPlanDetails = await getPilotFlightPlan(vatsimId, ivaoId);

                    depId = flightPlanDetails.departureId ? String(flightPlanDetails.departureId).trim().toUpperCase() : depId;
                    arrId = flightPlanDetails.arrivalId ? String(flightPlanDetails.arrivalId).trim().toUpperCase() : arrId;
                    logUserId = flightPlanDetails.networkUserId || logUserId;
                }

                if (!CLIENT_FLIGHT_LOGS[pilotId]) {
                    CLIENT_FLIGHT_LOGS[pilotId] = {
                        is_airborne: false, has_landed: true, initial_fuel_logged: false, landing_vs: null, last_vs: 0.0,
                        flight_ended: true, event_log: [], last_alert_timestamps: {},
                        flightPlan_departureId: depId, flightPlan_arrivalId: arrId, logUserId: logUserId,
                    };
                    await logEvent(pilotId, "INICIO_SESSAO", `Sessão de telemetria iniciada. DEP: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_departureId}, ARR: ${CLIENT_FLIGHT_LOGS[pilotId].flightPlan_arrivalId}. (Usando ID de Rede ${logUserId} para log)`, data);
                }

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

                if (pilotConnections[pilotId].tx_sent) {
                    await processFlightEvents(ws, data);
                }
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