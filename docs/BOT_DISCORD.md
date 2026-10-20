# Sentinela — bot Discord 24h

Este serviço executa **um único processo** usando `discord.py`. Ele responde a
comandos slash, consulta produtos a cada intervalo e envia as ofertas pelo próprio bot.

## 1. Criar bot no Discord

1. Em https://discord.com/developers/applications, crie uma aplicação e um Bot.
2. Copie o token para `DISCORD_BOT_TOKEN` (variável de ambiente, não no Git).
3. Em OAuth2 > URL Generator, selecione os escopos `bot` e `applications.commands`.
4. Permissões mínimas no canal: View Channel, Send Messages e Embed Links.
5. Convide o bot ao servidor e copie o ID do canal de ofertas para `DISCORD_CHANNEL_ID`.
6. Recomenda-se copiar o ID do servidor para `DISCORD_GUILD_ID`: os comandos
   aparecerão mais rapidamente em testes, sem esperar a propagação global.

Não é necessário habilitar **Message Content Intent**, pois os comandos são slash.

## 2. Testar localmente

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[bot,dev]'
cp -n .env.example .env
# Preencha DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID e DISCORD_GUILD_ID no .env
python -m sentinela.bot
```

Comece com `BOT_AUTO_SCAN=false`. Teste `/status`, `/buscar` e `/ultimas`.
`/verificar` realiza uma coleta e publica resultados elegíveis; execute apenas
depois de confirmar que o monitor antigo não está rodando ao mesmo tempo.

Comandos:
- `/status`: última execução e situação do monitor.
- `/buscar termo`: procura no catálogo *já coletado*; não faz pesquisa ao vivo.
- `/ultimas quantidade`: histórico de publicações.
- `/verificar`: coleta manual; exige permissão Manage Server.

## 3. Hospedar no Railway

1. Crie projeto no https://railway.com/ conectando o repositório do GitHub.
2. Adicione um serviço do repositório usando o `Dockerfile` deste projeto.
3. Configure **uma réplica** e o serviço **sem sleep/cron**.
4. Crie um **volume persistente** montado em `/data`.
5. Adicione as variáveis:

   - `DISCORD_BOT_TOKEN`: token do bot.
   - `DISCORD_CHANNEL_ID`: canal de ofertas.
   - `DISCORD_GUILD_ID`: servidor dos comandos (recomendado).
   - `DATABASE_PATH=/data/history.sqlite3`
   - `DISCOVERY_FILE=/data/discovered.json`
   - `RUN_REPORT_FILE=/data/last_run.json`
   - `BOT_SCAN_MINUTES=180`
   - `BOT_AUTO_SCAN=false` **durante a instalação**.

6. Consulte os logs e verifique se o bot ficou online.
7. Teste os comandos de leitura. Se precisar testar `/verificar`, desative
   antes a execução automática anterior, evitando gravação em duas instâncias.
8. Quando a instância estiver validada, em **GitHub > Settings > Secrets and
   variables > Actions > Variables**, crie `SENTINELA_RUNNER=bot`, impedindo
   o job do Actions de fazer novas coletas.
9. Ajuste `BOT_AUTO_SCAN=true` no Railway e reinicie/reimplante o serviço.
   Verifique a execução, publicações e persistência após reinício.

**Observação:** a primeira execução no volume vazio importa automaticamente
o SQLite e catálogo existentes no repositório. Depois disso, o **volume**
é a fonte única de dados; os commits automáticos do Actions deixam de atualizar
o banco. Não execute duas réplicas do bot com o mesmo token/volume.

## Falhas e recuperação

- Se o bot ficar offline: verifique logs, token, reinício automático e disponibilidade do host.
- Se não publicar: verifique `DISCORD_CHANNEL_ID` e as permissões Embed Links e Send Messages.
- Se reiniciar e perder histórico: confira a montagem de `/data` e as variáveis.
- Para voltar temporariamente ao Actions, **primeiro pare o bot/auto-scan**.
  Depois remova `SENTINELA_RUNNER=bot`. Observe que o banco do Actions estará
  defasado desde a migração; restaure os dados persistentes antes de retomar
  para evitar duplicações e inconsistências.
- O plano Railway Free tem limitações de crédito/reinícios; para conexão
  contínua e reinício automático indefinido, avalie o plano pago.

## Mercado Livre

A integração oficial via Mercado Livre Developers é a **próxima etapa**.
Até sua implementação, este bot usa os coletores atuais do Sentinela.
