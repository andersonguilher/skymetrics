// node_server/monitor_renderer.js

import * as fs from 'fs/promises';
import * as path from 'path';
import { HTML_FILE_PATH, JSON_FILE_PATH, WORST_CASE_RATE_MBH } from './config.js';
import { getTimestamp, formatNumber } from './utils.js';
import { getGlobalState, getPilotConnections, getAllPilotSnapshots } from './state_manager.js';


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

    const pilotConnections = getPilotConnections();
    const allPilotSnapshots = getAllPilotSnapshots();
    const pilotNames = Object.keys(pilotConnections);

    if (pilotNames.length === 0) {
        return '<tr><td colspan="6" style="text-align:center; color: #A9A9A9;">Nenhum cliente conectado no momento.</td></tr>';
    }

    for (const pilot_name of pilotNames) {
        const connData = pilotConnections[pilot_name];
        const data = allPilotSnapshots[pilot_name];
        const conn_status = connData ? connData.tx_sent : false;

        const alt = data ? formatNumber(data.alt_ind || 0, 0) : "N/A";
        const vs = data ? formatNumber(data.vs || 0, 0) : "N/A";
        const gs = data ? formatNumber(data.gs || 0, 0) : "N/A"; // ALTERADO: Usando GS para a tabela

        const vatsim = connData.vatsim_id || 'N/A';
        const ivao = connData.ivao_id || 'N/A';

        let status_text;
        let status_class;

        let network_display_final = 'N/A';

        // 1. Determinação do Status de Exibição
        const is_airborne = (data.on_ground || 1) === 0 || (data.agl || 0) > 50;
        const is_taxiing = (data.on_ground || 1) === 1 && (data.gs || 0) > 5 && (data.eng_combustion || 0) === 1; // ALTERADO: Usando 'gs'
        const is_cold = (data.eng_combustion || 0) === 0;

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
                    <td>${gs} kts</td> </tr>`;
    }

    return rows_html;
}


async function generateRealtimeDataJson(data, received_count, total_bytes_received) {
    const globalState = getGlobalState();
    const now = new Date();
    const timeSinceLastUpdate = now.getTime() - globalState.LAST_JSON_UPDATE_TIME.getTime();

    if (timeSinceLastUpdate < 60000) {
        return;
    }

    globalState.LAST_JSON_UPDATE_TIME = now;
    console.log(`[${getTimestamp()}] [JSON_WRITE] Atualizando whazzup.json.`);

    const timeElapsed = now.getTime() - globalState.SERVER_START_TIME.getTime();
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
        "gs": data.gs || 0, // NOVO: Adicionado Ground Speed ao JSON
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


export async function updateMonitorFiles(data, received_count, total_bytes_received) {
    const globalState = getGlobalState();
    await generateRealtimeDataJson(data, received_count, total_bytes_received);

    const now = new Date();
    const timeElapsed = now.getTime() - globalState.SERVER_START_TIME.getTime();
    const timeElapsedHours = timeElapsed / (1000 * 3600);

    let averageRateMbh = 0.0;
    const totalMbReceived = total_bytes_received / (1024 * 1024);

    if (timeElapsedHours > 0 && total_bytes_received > 0) {
        averageRateMbh = totalMbReceived / timeElapsedHours;
    }

    const pilotConnections = getPilotConnections();
    const rateStatusText = formatNumber(averageRateMbh, 4) + " MB/hora";
    const estimatedTableRows = generateEstimatedDataTable(averageRateMbh);
    const pilotSummaryRows = generatePilotSummaryRows();

    const sentCount = formatNumber(data.packets_sent || 0, 0);
    const sentMb = formatNumber(data.mb_sent || 0.0, 4);
    const receivedMb = formatNumber(totalMbReceived, 4);
    const activePilotsCount = Object.keys(pilotConnections).length;

    const displaySpeed = data.gs || data.ias || 0; // Prioriza GS para exibição no mapa

    // O conteúdo HTML é muito extenso. Será reproduzido com a alteração do cabeçalho da tabela (IAS -> GS) e na lógica do script.
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
                    <th>GS</th>
                </tr>
            </thead>
            <tbody>
                ${pilotSummaryRows}
            </tbody>
        </table>

        <h2 style="margin-top: 30px;">Localização (Último Piloto Ativo)</h2>
        <div id="map"></div>
        
        <script>
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
                    const displaySpeed = data.gs || data.ias;

                    if (isValidData(data)) { 
                        var newLatLng = L.latLng(data.lat, data.lng);

                        if (!marker) {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>GS: ' + displaySpeed + ' kts')
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
                    const displaySpeed = data.gs || data.ias;

                    if (isValidData(data)) { 
                        var newLatLng = L.latLng(data.lat, data.lng);

                        if (marker) {
                            marker.setLatLng(newLatLng);
                            marker.getPopup().setContent('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>GS: ' + displaySpeed + ' kts');
                            
                            if (!map.getBounds().contains(newLatLng)) {
                                map.setView(newLatLng, map.getZoom()); 
                            }

                        } else {
                            marker = L.marker(newLatLng).addTo(map)
                                .bindPopup('<b>Piloto: ' + data.pilot_name + '</b><br>Alt: ' + data.alt_ind + ' ft<br>GS: ' + displaySpeed + ' kts')
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
                <tr class="stats-row"><td class="stats-label">Pacotes Recebidos (Servidor)</td><td class="stats-value">${received_count}</td></tr>
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