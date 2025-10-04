# va_auth.py
import requests
import json
import configparser
import os

# Constantes de Arquivo
CONFIG_FILE = 'config.ini'

# =================================================================
# NOVO: URLs das redes de simulação (Fixas)
# =================================================================
IVAO_WHAZZUP_URL = "https://api.ivao.aero/v2/tracker/whazzup"
VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"

# =================================================================
# FIX: Função _load_config movida para o topo para evitar NameError
# =================================================================
def _load_config():
    """Carrega as configurações de URL do config.ini."""
    config = configparser.ConfigParser()
    # Tenta ler o arquivo, se não existir, usa um dict vazio
    config.read(CONFIG_FILE) 
    
    # Mapeamento dinâmico das URLs
    url_base = {}
    if 'URLS' in config:
        # Pega as URLs base e remove o sufixo _BASE_URL para criar a chave da VA
        for key, value in config.items('URLS'):
            if key.endswith('_base_url'):
                va_key = key.replace('_base_url', '').upper()
                url_base[va_key] = value

    return url_base, config.get('URLS', 'login_endpoint', fallback='/dash/utils/login_check.php'), config.get('URLS', 'pilots_endpoint', fallback='/dash/utils/get_validated_pilots.php')

# Carrega as configurações globais
URL_BASE, LOGIN_ENDPOINT, PILOTS_ENDPOINT = _load_config()

if not URL_BASE:
    print("Aviso: Configurações de URL não encontradas. Verifique o config.ini.")


def check_login(va_key: str, email: str, password: str) -> bool:
    """
    Tenta autenticar o piloto na VA especificada usando o endpoint login_check.php.
    """
    try:
        if va_key not in URL_BASE:
            print(f"Erro: Chave de VA '{va_key}' não encontrada nas configurações.")
            return False
            
        url = URL_BASE[va_key] + LOGIN_ENDPOINT
        data = {'username': email, 'password': password}
        
        response = requests.post(url, data=data, timeout=10)
        return response.text.strip().lower() == 'true'
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao tentar login em {va_key}: {e}")
        return False


# =================================================================
# FUNÇÕES DE VERIFICAÇÃO DE STATUS ONLINE
# =================================================================

def _get_pilot_data_from_va(va_key: str, email: str) -> dict | None:
    """
    Busca os dados do piloto (incluindo ivao_id e vatsim_id) na VA.
    Retorna o dicionário do piloto ou None se não for encontrado.
    
    NOTA: A busca agora verifica a chave real '_email_contato'
    """
    try:
        if va_key not in URL_BASE:
            print(f"Erro: Chave de VA '{va_key}' não encontrada nas configurações.")
            return None

        url = URL_BASE[va_key] + PILOTS_ENDPOINT

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        pilots_list = response.json()

        for pilot in pilots_list:
            # CORREÇÃO DEFINITIVA: Usa a chave REAL '_email_contato' do JSON
            if pilot.get('_email_contato', '').lower() == email.lower():
                return pilot
            # Removidas as verificações para 'email_piloto' e 'username'
            # para focar apenas na chave correta, mas você pode mantê-las se quiser.
            
        # Nenhum piloto encontrado com a chave testada
        return None 

    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar lista de pilotos de {va_key}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"Erro: Resposta do endpoint de pilotos de {va_key} não é um JSON válido.")
        return None

def is_pilot_validated(va_key: str, email: str) -> bool:
    """
    Verifica se o piloto consta na lista de pilotos validados da VA.
    (Atualizada para usar _get_pilot_data_from_va)
    """
    return _get_pilot_data_from_va(va_key, email) is not None


def _check_ivao_online(ivao_id: int) -> bool:
    """
    Verifica se o piloto está online na IVAO (pelo userId/CID).
    """
    if not ivao_id:
        return False
    try:
        response = requests.get(IVAO_WHAZZUP_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        ivao_id_str = str(ivao_id)
        
        # A lista de pilotos está em data['clients']['pilots']
        for pilot in data.get('clients', {}).get('pilots', []):
            if str(pilot.get('userId')) == ivao_id_str:
                return True
        return False

    except requests.exceptions.RequestException as e:
        print(f"Aviso: Falha ao consultar IVAO Whazzup: {e}")
        return False
    except Exception:
        return False


def _check_vatsim_online(vatsim_id: int) -> bool:
    """
    Verifica se o piloto está online na VATSIM (pelo cid).
    """
    if not vatsim_id:
        return False
    try:
        response = requests.get(VATSIM_DATA_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        vatsim_id_str = str(vatsim_id)

        # A lista de pilotos está em data['pilots']
        for pilot in data.get('pilots', []):
            if str(pilot.get('cid')) == vatsim_id_str:
                return True
        return False

    except requests.exceptions.RequestException as e:
        print(f"Aviso: Falha ao consultar VATSIM data: {e}")
        return False
    except Exception:
        return False


def is_pilot_online_ivao_vatsim(va_key: str, email: str) -> tuple[bool, str]:
    """
    Função principal. Verifica se o piloto está online na IVAO ou VATSIM.

    Retorna: (is_online: bool, network_name: str)
    - (True, 'IVAO') se estiver online na IVAO.
    - (True, 'VATSIM') se estiver online na VATSIM.
    - (False, 'Offline') se não estiver em nenhuma.
    - (False, 'Piloto não validado na VA') se não for encontrado na VA.
    """
    # 1. Busca os IDs do piloto na VA
    pilot_data = _get_pilot_data_from_va(va_key, email)

    if not pilot_data:
        return False, "Piloto não validado na VA"

    # Assume que a VA fornece os IDs. Conversão para int é importante.
    try:
        # Tenta obter o ID, garantindo que seja um inteiro
        ivao_id = int(pilot_data.get('ivao_id'))
    except (TypeError, ValueError):
        ivao_id = None
        
    try:
        # Tenta obter o ID, garantindo que seja um inteiro
        vatsim_id = int(pilot_data.get('vatsim_id'))
    except (TypeError, ValueError):
        vatsim_id = None
        
    # 2. Checa IVAO primeiro
    if ivao_id and _check_ivao_online(ivao_id):
        return True, "IVAO" 

    # 3. Se não estiver na IVAO, checa VATSIM
    if vatsim_id and _check_vatsim_online(vatsim_id):
        return True, "VATSIM" 

    return False, "Offline"