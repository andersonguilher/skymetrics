# Arquivo: client/auth_utils.py

import requests
import configparser
import keyring 
import os # NOVO: Importar 'os'
from typing import Dict, Any, Tuple

CONFIG_FILE = 'client_config.ini'
CLIENT_CONFIG_SECTION = 'CLIENT_CONFIG' 
CLIENT_LOGIN_SECTION = 'LOGIN_CREDENTIALS'

def _get_absolute_config_path(config_file: str) -> str:
    """Retorna o caminho absoluto do arquivo de configuração, baseado no diretório deste script."""
    # os.path.abspath(__file__) é o caminho completo para auth_utils.py
    # os.path.dirname(...) pega o diretório (i.e., '.../client')
    # os.path.join(...) junta o diretório com o nome do arquivo (i.e., '.../client/client_config.ini')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), config_file)


def _get_config_globals(config_file: str) -> Tuple[str, str, str, str, str, configparser.ConfigParser]:
    # CHAVE: Usar o caminho ABSOLUTO
    config_path = _get_absolute_config_path(config_file)
    
    config = configparser.ConfigParser()
    config.read(config_path)

    # Garante que as seções existem antes de usar, caso o arquivo seja novo
    if CLIENT_CONFIG_SECTION not in config: config[CLIENT_CONFIG_SECTION] = {}
    if CLIENT_LOGIN_SECTION not in config: config[CLIENT_LOGIN_SECTION] = {}

    KEYRING_SERVICE_ID = config.get(CLIENT_CONFIG_SECTION, 'keyring_service_id', fallback='KAFY_Pilot_Password')
    KAFY_BASE_URL = config.get(CLIENT_CONFIG_SECTION, 'kafy_base_url', fallback='https://kafly.com.br')
    LOGIN_ENDPOINT = config.get(CLIENT_CONFIG_SECTION, 'login_endpoint', fallback='/dash/utils/login_check.php')
    PILOTS_ENDPOINT = config.get(CLIENT_CONFIG_SECTION, 'pilots_endpoint', fallback='/dash/utils/get_validated_pilots.php')
    
    return KAFY_BASE_URL, LOGIN_ENDPOINT, PILOTS_ENDPOINT, KEYRING_SERVICE_ID, CLIENT_LOGIN_SECTION, config


def check_login(email: str, password: str, config_file: str = CONFIG_FILE) -> bool:
    """Verifica as credenciais do piloto no endpoint de login da VA."""
    KAFY_BASE_URL, LOGIN_ENDPOINT, _, _, _, _ = _get_config_globals(config_file)
    url = KAFY_BASE_URL + LOGIN_ENDPOINT
    data = {'username': email, 'password': password}
    try:
        response = requests.post(url, data=data, timeout=10)
        return response.text.strip().lower() == 'true'
    except requests.exceptions.RequestException: 
        return False

def get_validated_pilot_data(email: str, config_file: str = CONFIG_FILE) -> Dict[str, Any] | None:
    """Busca os dados do piloto na lista de pilotos validados da VA."""
    KAFY_BASE_URL, _, PILOTS_ENDPOINT, _, _, _ = _get_config_globals(config_file)
    url = KAFY_BASE_URL + PILOTS_ENDPOINT
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        pilots_list = response.json()
        for pilot in pilots_list:
            if pilot.get('_email_contato', '').lower() == email.lower(): 
                return pilot
        return None
    except Exception: 
        return None

def load_credentials(config_file: str = CONFIG_FILE) -> Tuple[str, str, bool]:
    """Carrega as credenciais salvas do arquivo de config e do keyring."""
    email, password, remember_me = "", "", False
    try:
        KAFY_BASE_URL, _, _, KEYRING_SERVICE_ID, CLIENT_LOGIN_SECTION, config = _get_config_globals(config_file)
        if CLIENT_LOGIN_SECTION in config:
            email = config.get(CLIENT_LOGIN_SECTION, 'pilot_email', fallback="")
            remember_me = config.getboolean(CLIENT_LOGIN_SECTION, 'remember_me', fallback=False)
        if email and remember_me:
            password = keyring.get_password(KEYRING_SERVICE_ID, email)
    except Exception: 
        pass
    return email, password, remember_me

def save_credentials(email: str, password: str, config_file: str = CONFIG_FILE):
    """Salva as credenciais para login futuro."""
    try:
        KAFY_BASE_URL, _, _, KEYRING_SERVICE_ID, CLIENT_LOGIN_SECTION, config = _get_config_globals(config_file)
        config_path = _get_absolute_config_path(config_file) # Obtém o caminho absoluto novamente
        
        if CLIENT_LOGIN_SECTION not in config: 
            config[CLIENT_LOGIN_SECTION] = {}
        config[CLIENT_LOGIN_SECTION]['remember_me'] = 'True'
        config[CLIENT_LOGIN_SECTION]['pilot_email'] = email
        
        # CHAVE: Salva no caminho ABSOLUTO correto
        with open(config_path, 'w') as configfile: 
            config.write(configfile)
            
        keyring_username = email
        keyring.set_password(KEYRING_SERVICE_ID, keyring_username, password)
    except Exception as e: 
        print(f"Erro ao salvar credenciais: {e}")

def delete_credentials(email: str, clear_email: bool = True, config_file: str = CONFIG_FILE):
    """Remove as credenciais e desativa o autologin."""
    try:
        KAFY_BASE_URL, _, _, KEYRING_SERVICE_ID, CLIENT_LOGIN_SECTION, config = _get_config_globals(config_file)
        config_path = _get_absolute_config_path(config_file) # Obtém o caminho absoluto novamente
        
        keyring_username = email
        try: 
            keyring.delete_password(KEYRING_SERVICE_ID, keyring_username)
        except Exception: 
            pass 
        
        if CLIENT_LOGIN_SECTION in config: 
             config[CLIENT_LOGIN_SECTION]['remember_me'] = 'False'
             if clear_email:
                 config[CLIENT_LOGIN_SECTION]['pilot_email'] = '' 
                 
        # CHAVE: Salva no caminho ABSOLUTO correto
        with open(config_path, 'w') as configfile: 
            config.write(configfile)
    except Exception as e: 
        print(f"Erro ao deletar credenciais: {e}")