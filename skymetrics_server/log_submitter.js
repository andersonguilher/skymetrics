// node_server/log_submitter.js

import { SUBMIT_LOG_URL } from './config.js';
import { getTimestamp, delay, executeCurlCommand } from './utils.js';

/**
 * Envia um único evento formatado para o endpoint PHP, usando CURL.
 * @param {object} logEntry - O objeto de evento formatado para o PHP.
 */
export async function sendEventToPHP(logEntry) {
    const curlData = Object.entries(logEntry)
        .map(([key, value]) => ` --data-urlencode "${key}=${encodeURIComponent(String(value))}"`)
        .join('');

    const command = `curl -X POST -k -s "${SUBMIT_LOG_URL}" -H "Content-Type: application/x-www-form-urlencoded"${curlData}`;

    try {
        const { stdout, stderr } = await executeCurlCommand(command);

        let responseJson = null;
        const rawResponse = stdout.trim();

        try {
            responseJson = JSON.parse(rawResponse);
        } catch (e) {
            console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO CRÍTICO (CURL STDOUT) ao enviar evento ${logEntry.evento || 'undefined'}. Resposta bruta: ${rawResponse.substring(0, 100)}`);
            return 'CRITICAL_ERROR';
        }

        if (stderr) {
            console.warn(`[${getTimestamp()}] [SUBMIT LOG] Aviso/Erro de CURL (STDERR): ${stderr.trim()}`);
        }

        console.log(`[${getTimestamp()}] [SUBMIT LOG] Evento ${logEntry.evento} enviado. Resposta: ${responseJson.message}`);

        if (responseJson.status === 'error' || responseJson.status === 'not_found') {
            console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO LÓGICO do PHP: ${responseJson.message}`);
            return 'CRITICAL_ERROR';
        }

        return 'SUCCESS';

    } catch (e) {
        console.error(`[${getTimestamp()}] [SUBMIT LOG] ERRO CRÍTICO ao executar CURL: ${e.message}`);
        return 'CRITICAL_ERROR';
    }
}

/**
 * Envia todos os eventos acumulados para o endpoint PHP sequencialmente, com retentativas.
 * @param {string} pilot_name 
 * @param {object} flightState - O objeto de estado de voo que contém event_log.
 */
export async function postFullFlightLog(pilot_name, flightState) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 5000;

    if (!flightState || flightState.event_log.length === 0) {
        console.warn(`[${getTimestamp()}] [SUBMIT LOG] Nenhum evento acumulado para o piloto ${pilot_name} enviar.`);
        return;
    }

    const logCopy = [...flightState.event_log];

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        console.log(`[${getTimestamp()}] [SUBMIT LOG] Iniciando tentativa ${attempt}/${MAX_RETRIES} de envio de ${logCopy.length} eventos em lote para o piloto ${pilot_name}...`);

        let allEventsSucceeded = true;

        for (const logEntry of logCopy) {
            const result = await sendEventToPHP(logEntry);

            if (result === 'CRITICAL_ERROR') {
                allEventsSucceeded = false;
                break;
            }
        }

        if (allEventsSucceeded) {
            console.log(`[${getTimestamp()}] [SUBMIT LOG] Envio em lote concluído com SUCESSO na tentativa ${attempt}.`);
            flightState.event_log = []; // Limpa o buffer
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