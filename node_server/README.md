# Gestão do Servidor com PM2

Este guia contém os comandos essenciais para instalar, executar e gerir a aplicação Node.js com o PM2 (Process Manager 2).

### Instalação
Primeiro, instale o PM2 de forma global no seu servidor.
```bash
sudo npm install pm2 -g
```

---

### Iniciar e Gerir a Aplicação

| Ação | Comando | Notas |
| :--- | :--- | :--- |
| **Iniciar (Produção)** | `pm2 start server.js --name "skymetrics-server"` | Reinicia apenas se a aplicação falhar. |
| **Iniciar (Desenvolvimento)** | `pm2 start server.js --name "skymetrics-server" --watch` | Reinicia sempre que um ficheiro é alterado. |
| **Ignorar Ficheiros (Watch)**| `pm2 start ... --ignore-watch="node_modules *.log"` | Útil para não reiniciar com logs ou updates de pacotes. |
| **Parar Aplicação** | `pm2 stop skymetrics-server` | Para o processo, mas mantém-no na lista. |
| **Reiniciar Aplicação** | `pm2 restart skymetrics-server` | Aplica alterações de código sem perder o uptime. |
| **Apagar Aplicação** | `pm2 delete skymetrics-server` | Remove o processo da lista do PM2. |
| **Parar Todos** | `pm2 stop all` | Para todos os processos ativos. |
| **Apagar Todos** | `pm2 delete all` | Limpa completamente a lista de processos do PM2. |

---

### Monitorização e Logs

| Ação | Comando | Descrição |
| :--- | :--- | :--- |
| **Listar Processos** | `pm2 list` ou `pm2 status` | Mostra uma tabela com todos os processos e o seu estado. |
| **Ver Logs** | `pm2 logs skymetrics-server` | Exibe os logs da aplicação especificada em tempo real. |
| **Ver Todos os Logs** | `pm2 logs` | Exibe os logs de todas as aplicações geridas. |
| **Painel de Controlo**| `pm2 monit` | Abre um painel para monitorizar CPU e memória em tempo real. |

---

### Persistência (Iniciar com o Boot do Servidor)

| Ação | Comando | Descrição |
| :--- | :--- | :--- |
| **Gerar Script de Boot**| `pm2 startup` | Cria um script de serviço para iniciar o PM2 no boot. |
| **Guardar Processos** | `pm2 save` | Guarda a lista de processos atual para ser restaurada no boot. |
| **Remover Script de Boot**| `pm2 unstartup` | Remove o serviço do PM2 da inicialização do sistema. |