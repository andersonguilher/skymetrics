# va_auth.py
import requests
import json
import configparser # Novo import
import os

# Constantes de Arquivo
CONFIG_FILE = 'config.ini'

# Função para carregar as configurações de URL
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


def is_pilot_validated(va_key: str, email: str) -> bool:
    """
    Verifica se o piloto consta na lista de pilotos validados da VA.
    """
    try:
        if va_key not in URL_BASE:
            print(f"Erro: Chave de VA '{va_key}' não encontrada nas configurações.")
            return False
            
        url = URL_BASE[va_key] + PILOTS_ENDPOINT
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        pilots_list = response.json()
        
        for pilot in pilots_list:
            if pilot.get('email_piloto', '').lower() == email.lower():
                return True
                
        return False
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar lista de pilotos de {va_key}: {e}")
        return False
    except json.JSONDecodeError:
        print(f"Erro: Resposta do endpoint de pilotos de {va_key} não é um JSON válido.")
        return False