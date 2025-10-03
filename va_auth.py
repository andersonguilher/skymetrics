# va_auth.py
import requests
import json
# Necessário instalar: pip install requests

# Constantes para os URLs das VAs
URL_BASE = {
    "KAFLT": "https://kafly.com.br",
    "CUBANA": "https://cubana-va.com"
}
LOGIN_ENDPOINT = "/dash/utils/login_check.php"
PILOTS_ENDPOINT = "/dash/utils/get_validated_pilots.php"


def check_login(va_key: str, email: str, password: str) -> bool:
    """
    Tenta autenticar o piloto na VA especificada usando o endpoint login_check.php.
    
    :param va_key: Chave da VA ('KAFLT' ou 'CUBANA').
    :param email: E-mail ou username do piloto.
    :param password: Senha do piloto.
    :return: True se o login for bem-sucedido (resposta 'true'), False caso contrário.
    """
    try:
        url = URL_BASE[va_key] + LOGIN_ENDPOINT
        data = {'username': email, 'password': password}
        
        # A resposta do PHP é 'true' ou 'false' como texto simples
        response = requests.post(url, data=data, timeout=10)
        return response.text.strip().lower() == 'true'
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao tentar login em {va_key}: {e}")
        return False


def is_pilot_validated(va_key: str, email: str) -> bool:
    """
    Verifica se o piloto consta na lista de pilotos validados da VA usando get_validated_pilots.php.
    
    :param va_key: Chave da VA ('KAFLT' ou 'CUBANA').
    :param email: E-mail do piloto (usado para comparação).
    :return: True se o e-mail do piloto estiver na lista, False caso contrário.
    """
    try:
        url = URL_BASE[va_key] + PILOTS_ENDPOINT
        
        # A resposta é um JSON com a lista de pilotos
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Lança exceção para códigos de erro HTTP
        
        pilots_list = response.json()
        
        # Verifica se o e-mail do piloto (COL_EMAIL_PILOTO no PHP) está na lista
        for pilot in pilots_list:
            # O campo é 'email_piloto' conforme o get_validated_pilots.php
            if pilot.get('email_piloto', '').lower() == email.lower():
                return True
                
        return False
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar lista de pilotos de {va_key}: {e}")
        return False
    except json.JSONDecodeError:
        print(f"Erro: Resposta do endpoint de pilotos de {va_key} não é um JSON válido.")
        return False