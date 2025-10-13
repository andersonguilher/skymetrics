// node_server/utils.js

import { execPromise } from './config.js';

export const getTimestamp = () => new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

export const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Formata um número para string com separador de milhares.
 * @param {number} value
 * @param {number} decimals
 * @returns {string}
 */
export function formatNumber(value, decimals) {
    if (typeof value !== 'number') return "N/A";

    const options = { minimumFractionDigits: decimals, maximumFractionDigits: decimals };
    return value.toLocaleString('pt-BR', options);
}

/**
 * Executa um comando shell (curl) para enviar dados.
 * @param {string} command - O comando curl completo.
 * @returns {Promise<object>} - { stdout, stderr }
 */
export async function executeCurlCommand(command) {
    return execPromise(command);
}