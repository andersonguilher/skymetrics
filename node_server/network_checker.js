// node_server/network_checker.js

import axios from 'axios';
import { IVAO_DATA_URL, VATSIM_DATA_URL, httpsAgent, NETWORK_CHECK_INTERVAL_SERVER } from './config.js';
import { getTimestamp } from './utils.js';
import { getGlobalState, getAllPilotSnapshots, getPilotConnections, startTx, stopTx, removePilotConnection } from './state_manager.js';


// --- Lógica de Consulta às APIs ---

async function isPilotOnlineIVAO(ivao_id) {
    // ... (função inalterada) ...
    if (!ivao_id || ivao_id.trim() === 'N/A' || ivao_id.trim() === '' || ivao_id.trim() === '0') return null;
    const ivao_id_int = parseInt(ivao_id.trim());
    if (isNaN(ivao_id_int)) return null;

    try {
        const response = await axios.get(IVAO_DATA_URL, { timeout: 5000, httpsAgent: httpsAgent });
        const data = response.data;
        for (const client of data.clients.pilots) {
            if (client.userId === ivao_id_int && client.flightPlan) {
                console.log(`[${getTimestamp()}] [IVAO CHECK] SUCESSO: Piloto ${ivao_id} encontrado online.`);
                return client.flightPlan;
            }
        }
        return null;
    } catch (e) {
        console.error(`[${getTimestamp()}] [IVAO CHECK] ERRO CRÍTICO ao consultar API IVAO para ID ${ivao_id}. Erro: ${e.message}`);
        return null;
    }
}

async function isPilotOnlineVATSIM(vatsim_id) {
    // ... (função inalterada) ...
    if (!vatsim_id || vatsim_id.trim() === 'N/A' || vatsim_id.trim() === '' || vatsim_id.trim() === '0') return false;
    const vatsim_id_int = parseInt(vatsim_id.trim());
    if (isNaN(vatsim_id_int)) return false;

    try {
        const response = await axios.get(VATSIM_DATA_URL, { timeout: 5000, httpsAgent: httpsAgent });
        const data = response.data;
        for (const pilot of data.pilots) {
            if (pilot.cid === vatsim_id_int) {
                console.log(`[${getTimestamp()}] [VATSIM CHECK] SUCESSO: Piloto ${vatsim_id} encontrado online.`);
                return true;
            }
        }
        return false;
    } catch (e) {
        console.error(`[${getTimestamp()}] [VATSIM CHECK] ERRO CRÍTICO ao consultar API VATSIM para ID ${vatsim_id}. Erro: ${e.message}`);
        return false;
    }
}

export async function getPilotFlightPlan(vatsim_id, ivao_id) {
    // ... (função inalterada) ...
    let departureId = "N/A";
    let arrivalId = "N/A";
    let networkUserId = "N/A";

    const ivaoFlightPlan = await isPilotOnlineIVAO(ivao_id);
    if (ivaoFlightPlan) {
        departureId = ivaoFlightPlan.departureId;
        arrivalId = ivaoFlightPlan.arrivalId;
        networkUserId = ivao_id;
    }

    // Adicionar lógica para VATSIM aqui (se necessário)

    return { departureId, arrivalId, networkUserId };
}

export async function checkNetworkStatus(vatsim_id, ivao_id) {
    // ... (função inalterada) ...
    const isVatsimOnline = await isPilotOnlineVATSIM(vatsim_id);
    const isIvaoOnline = !!(await isPilotOnlineIVAO(ivao_id));
    return isVatsimOnline || isIvaoOnline;
}


// --- Loop de Verificação Periódica ---
export async function networkStatusCheckerLoop() {

    const loop = async () => {
        const currentTime = Date.now();
        const pilotConnections = getPilotConnections();
        const allPilotSnapshots = getAllPilotSnapshots();
        // REMOVIDO: const clientFlightLogs = getClientFlightLogs(); // Estado transferido para o cliente
        const globalState = getGlobalState();

        if (Object.keys(pilotConnections).length === 0) {
            globalState.LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;
            setTimeout(loop, 1000);
            return;
        }

        if (currentTime - globalState.LAST_GLOBAL_NETWORK_CHECK_TIME < NETWORK_CHECK_INTERVAL_SERVER) {
            setTimeout(loop, 1000);
            return;
        }

        const pilotNames = Object.keys(pilotConnections);
        console.log(`[${getTimestamp()}] [SERVER CHECK] Iniciando verificação de rede para ${pilotNames.length} piloto(s).`);
        globalState.LAST_GLOBAL_NETWORK_CHECK_TIME = currentTime;

        const pilotsToRemove = [];

        for (const pilotName of pilotNames) {
            const connData = pilotConnections[pilotName];
            const pilotSnapshot = allPilotSnapshots[pilotName];

            if (!connData || !pilotSnapshot || pilotName === "ANÔNIMO" || (connData.vatsim_id === "N/A" && connData.ivao_id === "N/A")) {
                continue;
            }

            const ws = connData.websocket;
            const vatsimId = connData.vatsim_id;
            const ivaoId = connData.ivao_id;
            const isTransmitting = connData.tx_sent;

            if (ws.readyState !== ws.OPEN) {
                pilotsToRemove.push(pilotName);
                continue;
            }

            try {
                console.log(`[${getTimestamp()}] [PERIODIC CHECK] Verificando Piloto: ${pilotName} (V: ${vatsimId} / I: ${ivaoId})`);

                const isOnline = await checkNetworkStatus(vatsimId, ivaoId);

                const currentGs = pilotSnapshot.gs || pilotSnapshot.ias || 0; // ALTERADO: Prioriza GS
                const currentOnGround = pilotSnapshot.on_ground || 1;
                // REMOVIDO: const flightState = clientFlightLogs[pilotName];
                // REMOVIDO: const isFlightInitiated = flightState && flightState.initial_fuel_logged;


                // --- LÓGICA DE PAUSA INTELIGENTE (Solo/Parado) ---
                const isStuckOnGround = currentOnGround === 1 && currentGs < 5 && isOnline; // ALTERADO: De currentIas para currentGs

                // REMOVIDA A CHECAGEM isFlightInitiated
                if (isStuckOnGround && isTransmitting) {
                    const lastStopTime = connData.last_stop_time;
                    if (!lastStopTime) {
                        connData.last_stop_time = new Date();
                        continue;
                    }

                    const timeStuckMs = new Date().getTime() - lastStopTime.getTime();
                    if (timeStuckMs >= 5 * 60 * 1000) {
                        stopTx(pilotName, ws);
                        // REMOVIDO: logEvent call
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotName} PAUSADO por 5min parado no solo/online.`);
                        continue;
                    }
                }
                else if (connData.last_stop_time && (currentGs > 5 || currentOnGround === 0)) { // ALTERADO: De currentIas para currentGs
                    connData.last_stop_time = null;
                }

                // --- LÓGICA DE REDE PADRÃO (Online/Offline) ---
                if (isOnline) {
                    if (!isTransmitting) {
                        if (currentGs > 5 || currentOnGround === 0) { // ALTERADO: De currentIas para currentGs
                            startTx(pilotName, ws);
                        }
                    }
                } else {
                    if (isTransmitting) {
                        stopTx(pilotName, ws);
                        console.log(`[${getTimestamp()}] [SERVER CHECK] Piloto ${pilotName} OFFLINE na rede. Comando STOP_TX enviado (Conexão mantida).`);
                    }
                }

            } catch (e) {
                console.log(`[${getTimestamp()}] [SERVER CHECK] Erro processando/enviando comando para ${pilotName}: ${e.message}`);
                pilotsToRemove.push(pilotName);
            }
        }

        for (const pilotName of pilotsToRemove) {
            await removePilotConnection(pilotName);
        }

        setTimeout(loop, 1000);
    };

    setTimeout(loop, 1000);
}