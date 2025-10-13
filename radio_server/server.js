// server.js (CORRIGIDO com Lógica de Distância Implementada e Rota de Estatísticas)

const express = require('express');
const http = require('http');
const { Server } = require('socket.io');

const app = express();
const server = http.createServer(app);

const io = new Server(server, {
    cors: {
        origin: "*",
        methods: ["GET", "POST"]
    }
});

const PORT = 3000;
const HOST = '0.0.0.0';
const DEFAULT_FREQUENCY = '121.5';

// --- ESTADO GLOBAL E CONSTANTES DE ALCANCE (Sincronizadas com o cliente Python) ---
const PILOT_POSITIONS = {}; // { pilot_id: { lat: number, lng: number, socket_id: string, currentFrequency: string } }
const MAX_RANGE_KM = 4000.0;
const MIN_RANGE_KM = 5.0;
const EARTH_RADIUS_KM = 6371; // Raio da Terra em km

/**
 * Calcula a distância Haversine entre duas coordenadas.
 * @param {number} lat1 
 * @param {number} lon1 
 * @param {number} lat2 
 * @param {number} lon2 
 * @returns {number} Distância em km.
 */
function haversineDistance(lat1, lon1, lat2, lon2) {
    const toRad = (value) => (value * Math.PI) / 180;

    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const lat1Rad = toRad(lat1);
    const lat2Rad = toRad(lat2);

    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.sin(dLon / 2) * Math.sin(dLon / 2) * Math.cos(lat1Rad) * Math.cos(lat2Rad);

    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

    return EARTH_RADIUS_KM * c;
}

/**
 * Calcula o fator de degradação com base na distância.
 * @param {number} distanceKm 
 * @returns {number} Fator entre 0.0 (perfeito) e 1.0 (degradação máxima).
 */
function calculateDegradationFactor(distanceKm) {
    if (distanceKm <= MIN_RANGE_KM) return 0.0;
    if (distanceKm >= MAX_RANGE_KM) return 1.0;

    // Interpolação linear
    return (distanceKm - MIN_RANGE_KM) / (MAX_RANGE_KM - MIN_RANGE_KM);
}

// --- ROTA DE ESTATÍSTICAS (http://HOST:PORT/skymetrics) ---
// Rota ajustada para ser o caminho base da aplicação web.
app.get('/skymetrics', (req, res) => {
    // io.engine.clientsCount conta todas as conexões (incluindo as não totalmente inicializadas)
    const totalConnections = io.engine.clientsCount;
    // Conta apenas os clientes que enviaram o 'update_position' com o pilot_id
    const activePilots = Object.keys(PILOT_POSITIONS).length;

    let tableRows = '';

    if (activePilots === 0) {
        tableRows = '<tr><td colspan="4" style="text-align: center; color: #777;">Nenhum piloto ativo enviando posição.</td></tr>';
    } else {
        for (const pilotId in PILOT_POSITIONS) {
            const data = PILOT_POSITIONS[pilotId];
            tableRows += `
                <tr>
                    <td>${pilotId}</td>
                    <td>${data.currentFrequency || 'N/A'}</td>
                    <td>${data.lat.toFixed(4)}, ${data.lng.toFixed(4)}</td>
                    <td>${data.socket_id}</td>
                </tr>
            `;
        }
    }

    const html = `
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Estatísticas do Servidor de Rádio</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f9; }
                .container { max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
                h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
                .status-box { background-color: #3498db; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
                .stats-table { width: 100%; border-collapse: collapse; margin-top: 15px; }
                .stats-table th, .stats-table td { border: 1px solid #ddd; padding: 10px; text-align: left; }
                .stats-table th { background-color: #ecf0f1; color: #2c3e50; }
                .pilot-id { font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Estatísticas do Servidor de Rádio (SkyMetrics)</h1>
                <p>Última atualização: ${new Date().toLocaleTimeString('pt-BR')}</p>
                <div class="status-box">
                    <p>Clientes Socket.IO Conectados: <strong>${totalConnections}</strong></p>
                    <p>Pilotos Ativos Enviando Posição: <strong>${activePilots}</strong></p>
                    <p>Frequência Padrão: <strong>${DEFAULT_FREQUENCY}</strong></p>
                </div>

                <h2>Pilotos Ativos e Posições (Ouvindo/Enviando)</h2>
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>ID do Piloto</th>
                            <th>Frequência Sintonizada</th>
                            <th>Coordenadas (Lat, Lng)</th>
                            <th>ID do Socket</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${tableRows}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
    `;
    res.send(html);
});
// --- FIM DA ROTA DE ESTATÍSTICAS ---


// --- Lógica do Rádio ---

io.on('connection', (socket) => {
    console.log(`[CONEXÃO] Novo cliente conectado: ${socket.id}`);

    // Garante que o estado PILOT_POSITIONS seja atualizado com a frequência e socket_id
    socket.join(DEFAULT_FREQUENCY);
    console.log(`Cliente ${socket.id} entrou na frequência ${DEFAULT_FREQUENCY}`);

    let currentFrequency = DEFAULT_FREQUENCY;
    let pilotId = null;

    // 1. Recebe solicitação de MUDANÇA DE FREQUÊNCIA
    socket.on('change_frequency', (newFrequency) => {
        if (newFrequency && typeof newFrequency === 'string') {

            socket.leave(currentFrequency);
            console.log(`Cliente ${socket.id} saiu da frequência ${currentFrequency}`);

            currentFrequency = newFrequency.trim();
            socket.join(currentFrequency);
            console.log(`Cliente ${socket.id} entrou na nova frequência: ${currentFrequency}`);

            // ATUALIZA A FREQUÊNCIA NO ESTADO GLOBAL
            if (pilotId && PILOT_POSITIONS[pilotId]) {
                PILOT_POSITIONS[pilotId].currentFrequency = currentFrequency;
            }

            socket.emit('frequency_changed', currentFrequency);
        }
    });

    // 1.5. Recebe e armazena a POSIÇÃO e o ID do piloto
    socket.on('update_position', (data) => {
        const { lat, lng, pilot_id } = data;

        if (pilot_id && typeof lat === 'number' && typeof lng === 'number') {
            pilotId = pilot_id;
            PILOT_POSITIONS[pilot_id] = {
                lat,
                lng,
                socket_id: socket.id,
                currentFrequency: currentFrequency // Guarda a frequência atual
            };
            console.log(`[POSIÇÃO] Piloto ${pilot_id} atualizado. Coords: ${lat.toFixed(4)}, ${lng.toFixed(4)}`);
        }
    });


    // 2. Recebe ÁUDIO do PTT e retransmite para a sala (frequência)
    socket.on('audio_chunk', (data) => {
        if (!pilotId) {
            // Áudio recebido, mas piloto ainda não enviou a posição/ID.
            return;
        }

        const senderPosition = PILOT_POSITIONS[pilotId];
        if (!senderPosition) return;

        // Percorre todos os clientes na sala (exceto o remetente)
        io.sockets.in(currentFrequency).fetchSockets().then(sockets => {
            sockets.forEach(receiverSocket => {

                // Não envia de volta para o remetente
                if (receiverSocket.id === socket.id) return;

                // 1. Encontrar o ID e a Posição do Receptor
                let receiverPilotId = null;
                let receiverPosition = null;

                for (const id in PILOT_POSITIONS) {
                    if (PILOT_POSITIONS[id].socket_id === receiverSocket.id) {
                        receiverPilotId = id;
                        receiverPosition = PILOT_POSITIONS[id];
                        break;
                    }
                }

                if (!receiverPosition) {
                    const payload = { audio: data, factor: 0.0 };
                    receiverSocket.emit('broadcast_audio', payload);
                    return;
                }

                // 2. CALCULAR DISTÂNCIA
                const distanceKm = haversineDistance(
                    senderPosition.lat, senderPosition.lng,
                    receiverPosition.lat, receiverPosition.lng
                );

                // 3. CALCULAR FATOR DE DEGRADAÇÃO
                const factor = calculateDegradationFactor(distanceKm);

                // 4. ENVIAR COM FATOR CORRETO
                const payload = {
                    audio: data,
                    factor: factor
                };

                console.log(`[TX AUDIO] De ${pilotId} para ${receiverPilotId} (Dist: ${distanceKm.toFixed(1)} km, Fator: ${factor.toFixed(4)})`);

                receiverSocket.emit('broadcast_audio', payload);
            });
        });
    });

    // 3. Lidar com a desconexão
    socket.on('disconnect', () => {
        console.log(`[DESCONEXÃO] Cliente desconectado: ${socket.id}`);
        socket.leave(currentFrequency);

        // Remove a posição do piloto ao desconectar
        for (const id in PILOT_POSITIONS) {
            if (PILOT_POSITIONS[id].socket_id === socket.id) {
                delete PILOT_POSITIONS[id];
                console.log(`[POSIÇÃO] Piloto ${id} removido.`);
                break;
            }
        }
    });
});

// Inicia o servidor Node.js
server.listen(PORT, HOST, () => {
    console.log(`Servidor Socket.IO de Rádio rodando em http://${HOST}:${PORT}`);
    // URL que reflete a estrutura do diretório
    console.log(`Estatísticas disponíveis em: http://${HOST}:${PORT}/skymetrics`);
});