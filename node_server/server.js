// node_server/server.js - Arquivo Principal

import { WebSocketServer } from 'ws';
import { createServer } from 'http';
import * as fs from 'fs/promises';
import { getTimestamp } from './utils.js';
import { HOST, PORT, initialPilotSnapshot, GLOBAL_STATE } from './config.js';
import { updateMonitorFiles } from './monitor_renderer.js';
import { handleNewConnection, unregister, initializeGlobalState, getGlobalState } from './state_manager.js';
import { networkStatusCheckerLoop } from './network_checker.js';

// --- Funções de Inicialização ---

async function createInitialFiles() {
    initializeGlobalState(new Date());
    try {
        await updateMonitorFiles(initialPilotSnapshot, 0, 0.0);
        console.log(`[${getTimestamp()}] [INFO] Arquivos HTML/JSON iniciais criados.`);
    } catch (e) {
        console.error(`[${getTimestamp()}] [ERRO] Ao criar arquivos iniciais: ${e.message}`);
    }
}

async function main() {

    await createInitialFiles();

    // Inicia o loop de verificação de rede em background
    networkStatusCheckerLoop();

    const httpServer = createServer();
    const wss = new WebSocketServer({ server: httpServer });

    wss.on('connection', ws => {
        handleNewConnection(ws);

        // O listener de 'message' e 'close' no ws é para gerenciar o estado,
        // mas a renderização é acionada após o processamento da mensagem
        ws.on('message', async () => {
            const globalState = getGlobalState();
            const lastData = initialPilotSnapshot; // O monitor_renderer buscará o snapshot mais ativo

            await updateMonitorFiles(
                lastData,
                globalState.packetsReceivedCount,
                globalState.totalBytesReceived
            );
        });

        ws.on('close', async () => {
            const globalState = getGlobalState();
            // unregister retorna o último snapshot válido para atualização do monitor
            const data_to_update = await unregister(ws);

            await updateMonitorFiles(
                data_to_update,
                globalState.packetsReceivedCount,
                globalState.totalBytesReceived
            );
        });
    });

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