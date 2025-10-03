# Skymetrics

Skymetrics é um sistema para monitoramento e análise de métricas em ambiente de simulação/visualização, com interface gráfica, autenticação VA (Voice Assistant / Virtual Agent ou outro módulo), coleta de dados simulados e monitoramento contínuo.

> ⚠️ Este README é um ponto de partida: ajuste descrições, exemplos e instruções de acordo com o estado real do projeto.

---

## Tabela de Conteúdos

- [Visão Geral](#visão-geral)  
- [Funcionalidades](#funcionalidades)  
- [Tecnologias](#tecnologias)  
- [Arquitetura / Módulos](#arquitetura--módulos)  
- [Instalação](#instalação)  
- [Como Usar](#como-usar)  
- [Configuração (config.ini)](#configuração-configini)  
- [Contribuição](#contribuição)  
- [Licença](#licença)  
- [Contato](#contato)  

---

## Visão Geral

Skymetrics permite capturar métricas em cenários simulados, com interface de visualização (GUI), autenticação e monitoramento. É útil para experimentos, visualização de dados em tempo real e testes de desempenho de sistemas de monitoramento.

---

## Funcionalidades

- Interface gráfica para visualização de métricas e dados.  
- Módulo de autenticação VA (ex: `va_auth.py`).  
- Gerenciamento e leitura contínua de métricas (ex: `va_monitor.py`).  
- Simulação de dados para testes e desenvolvimento (ex: `sim_data_monitor.py`).  
- Componentes GUI organizados em `gui_elements.py`.  
- Permite extensão e customização de módulos de coleta/monitoramento.  

---

## Tecnologias / Dependências

O projeto é implementado em **Python**.

Dependências (conforme `requirements.txt`):

```
# Exemplo extraído — ver arquivo real para versões exatas
# (adicione aqui as dependências reais, por exemplo:)
numpy
matplotlib
PyQt5
requests
...
```

---

## Arquitetura / Módulos

Aqui está uma visão geral dos módulos presentes:

| Módulo / Arquivo             | Propósito |
|-----------------------------|-----------|
| `gui_elements.py`           | Define elementos visuais usados na interface gráfica |
| `sim_data_monitor.py`       | Simula geração de dados métricos para testes |
| `va_auth.py`                | Lida com autenticação (ex: voz, token, login) |
| `va_monitor.py`             | Módulo principal de monitoramento de métricas |
| `requirements.txt`          | Lista de dependências do projeto |
| `assets/icons/`             | Ícones / recursos visuais utilizados na GUI |

---

## Instalação

Siga estes passos para configurar o projeto localmente:

1. Clone este repositório:

   ```bash
   git clone https://github.com/andersonguilher/skymetrics.git
   cd skymetrics
   ```

2. Crie e ative um ambiente virtual (opcional, mas recomendado):

   ```bash
   python3 -m venv venv
   source venv/bin/activate   # no Linux / macOS
   venv\Scripts\activate      # no Windows
   ```

3. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

---

## Como Usar

Aqui vai um exemplo básico de execução:

```bash
python va_monitor.py
```

Ou, se houver interface gráfica:

```bash
python gui_elements.py
```

### Exemplos de Uso

> ⚠️ Substitua pelos exemplos reais do seu projeto:

```python
from sim_data_monitor import SimDataMonitor
from va_monitor import VAMonitor

sim = SimDataMonitor()
monitor = VAMonitor(auth_token="meu_token")
monitor.run(sim)
```

Você pode configurar parâmetros como taxa de amostragem, filtros, modos de exibição etc.

---

## Configuração (config.ini)

O projeto utiliza um arquivo `config.ini` para armazenar configurações de endpoints e login.  
Aqui está um **exemplo seguro** (sem URLs reais):

```ini
[URLS]
kafly_base_url = sua_url_vai_aqui
cubana_base_url = sua_url_vai_aqui
login_endpoint = /sua_url_vai_aqui
pilots_endpoint = /sua_url_vai_aqui

[LOGIN]
remember_me =
pilot_email =
va_key_selected =
```

> 🔑 Substitua os valores por aqueles fornecidos pela sua VA (Virtual Airline) ou sistema correspondente.

---

## Contribuição

Contribuições são bem-vindas! Se quiser contribuir:

1. Faça *fork* do repositório.  
2. Crie uma nova *branch* (`feature/nova-funcionalidade`).  
3. Faça seus commits.  
4. Envie um *pull request* explicando as mudanças.  

Siga um estilo de código consistente e adicione testes / documentação conforme apropriado.

---

## Licença

Este projeto está licenciado sob a **MIT License** (ou outra licença de sua escolha).  
Consulte o arquivo `LICENSE` para mais detalhes.

---

## Contato

Autor: Anderson Guilher  
GitHub: [andersonguilher](https://github.com/andersonguilher)  
