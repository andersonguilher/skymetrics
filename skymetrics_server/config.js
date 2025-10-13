// node_server/config.js

import https from 'https';
import { promisify } from 'util';
import { exec } from 'child_process';

// Promisifica exec para uso com await (usado em log_submitter)
export const execPromise = promisify(exec);

// --- CONFIGURAÇÃO GERAL ---
export const HOST = "0.0.0.0";
export const PORT = 8765;

// *** CAMINHOS E NOMES DE ARQUIVOS DEFINITIVOS ***
export const HTML_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/skymetrics/index.php";
export const JSON_FILE_PATH = "/var/www/kafly_user/data/www/kafly.com.br/skymetrics/whazzup.json";
// **********************************************************

// URLs
export const SUBMIT_LOG_URL = "https://kafly.com.br/dash/utils/submit_flight_log.php";
export const IVAO_DATA_URL = "https://api.ivao.aero/v2/tracker/whazzup";
export const VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json";

// Constantes de Lógica de Voo e Limite
export const ALERT_RATE_LIMIT_MS = 60 * 1000; // 60 segundos
export const GS_TAXI_START_KTS = 10;        // ALTERADO: Usando Ground Speed
export const WORST_CASE_RATE_MBH = 12.3;

// Variáveis de Verificação de Rede
export const NETWORK_CHECK_INTERVAL_SERVER = 120 * 1000;

// Agente HTTPS para ignorar verificação SSL/TLS
export const httpsAgent = new https.Agent({ rejectUnauthorized: false });

// Snapshot inicial (incluindo gs)
export const initialPilotSnapshot = {
    "alt_ind": 0, "vs": 0, "ias": 0, "gs": 0.0, "tas": 0, "agl": 0, "on_ground": 0, "total_fuel": 0, "gear_left_pos": 0, "g_force": 1.0, "engine_count": 0,
    "lat": 0.0, "lng": 0.0, "eng_combustion": 0, "light_beacon_on": 0, "light_landing_on": 0, "light_strobe_on": 0, "plane_bank_degrees": 0.0, "engine_vibration_1": 0.0,
    "pilot_id": "N/A", "pilot_name": "N/A", "vatsim_id": "N/A", "ivao_id": "N/A",
    "alerts": { "overspeed_warning": 0, "stall_warning": 0, "beacon_off_engine_on": 0, "engine_fire": 0, "stall_protection_active": 0, "gpws_warning": 0, "flaps_speed_exceeded": 0, "gear_warning_system_active": 0 },
    "packets_sent": 0, "mb_sent": 0.0
};

// Contadores e Timers Globais (para o Main Server)
export const GLOBAL_STATE = {
    SERVER_START_TIME: null,
    LAST_GLOBAL_NETWORK_CHECK_TIME: 0.0,
    LAST_JSON_UPDATE_TIME: new Date(0),
    packetsReceivedCount: 0,
    totalBytesReceived: 0.0
};