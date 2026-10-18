# Seleção e publicação de ofertas

O fluxo normal é: buscar candidatos, coletar preço atual, avaliar referências, filtrar orçamento, selecionar com diversidade de lojas e publicar apenas novidades elegíveis.

## O significado de cada publicação

1. **Queda frente à mediana histórica:** pelo menos cinco observações anteriores e sete dias, com queda de 20% por padrão. É uma comparação com as nossas coletas, não uma garantia universal de menor preço.
2. **Desconto anunciado:** comparação entre o preço atual e o preço anterior explicitamente declarado pela loja na mesma oferta. Publicado a partir de 10%, sem exigir sete dias. A mensagem informa que ainda não foi confirmado pelo histórico.
3. **Baixa desde a última publicação:** redução de pelo menos 1% frente ao último preço enviado ao canal. Pode aparecer antes de existir histórico suficiente para a primeira regra.
4. **Novo resultado:** produto que ainda não teve publicação registrada. É informado como novidade, sem alegação de desconto.

A seleção prioriza as regras nessa ordem e alterna lojas dentro de cada grupo. Por padrão, no máximo 12 produtos por rodada. Variações com a mesma URL base não ocupam várias posições no mesmo lote.

Não se republica o mesmo ID/preço durante sete dias. Um preço mais baixo pode entrar imediatamente, respeitando as demais regras. Preço inalterado sem promoção não volta apenas porque o intervalo expirou. Se não houver novidades, o bot não envia o catálogo novamente.

O SQLite só registra uma publicação depois de o Discord confirmar o lote. Falhas preservam as publicações já confirmadas; itens ainda não enviados continuam elegíveis. Não há garantia de entrega exatamente uma vez se a conexão cair depois de o Discord aceitar a mensagem e antes de devolver a confirmação.

## Configuração

| Variável | Padrão |
| --- | --- |
| `MAX_POSTS_PER_RUN` | `12` |
| `REPEAT_AFTER_DAYS` | `7` |
| `MIN_ADVERTISED_DISCOUNT` | `0.10` |
| `MIN_PRICE_CHANGE` | `0.01` |

`--report-all` permite inspeção manual do catálogo inteiro coletado, sem aplicar a seleção ou registrar esses resultados na deduplicação. `--dry-run` mostra a seleção sem publicar ou modificar os arquivos. `--no-results` preserva o modo antigo de enviar somente quedas frente ao histórico.

`data/last_run.json` informa a coleta, seleção, publicação e disponibilidade das fontes. O GitHub Actions exibe esses dados no resumo e no artifact da execução.

## Mercado Livre: sua etapa de autenticação

A vitrine `/ofertas` é consultada em até três páginas, filtrando pelos seus termos. O adaptador lê preço atual e anterior dentro do mesmo card; não usa o selo percentual sozinho como prova. Mantém `wid`, quando fornecido no link, para distinguir anúncios/vendedores de uma página de catálogo. Não verifica reputação do vendedor, frete por CEP ou elegibilidade de cupons.

No teste de acesso sem login, o Mercado Livre redirecionou para verificação de conta. Nesse caso a fonte fica com `requires_login`: outras lojas continuam funcionando, e o bot não registra preços inventados nem reutiliza preços antigos dessa fonte como nova coleta.

Para abrir o Mercado Livre no navegador padrão que você já usa:

```bash
bash scripts/login_mercadolivre.sh
```

No Linux, o script usa `xdg-open`. Não instala dependências nem baixa navegador; o login acontece na janela normal do seu navegador, com seu perfil habitual.

Se sua conta já estiver logada, não precisa repetir o login. Quando os produtos aparecerem, volte ao terminal. Se quiser disponibilizar a sessão ao coletor, digite `EXPORTAR`: o script lê somente cookies de `mercadolivre.com.br` e seus subdomínios, em modo somente leitura, do perfil Firefox escolhido. Não exporta cookies do Google, senhas ou histórico. Enter cancela sem ler cookies e preserva qualquer sessão anterior. Use uma janela normal, sem modo privado ou contêiner. A exportação de outros navegadores não está implementada.

O perfil é detectado pelas instalações Firefox comuns, Flatpak e Snap. Se houver vários perfis, escolha o usado no login. Você também pode informar o diretório raiz exibido em `about:profiles`:

```bash
bash scripts/login_mercadolivre.sh --profile "/caminho/do/perfil"
```

O destino padrão é `.secrets/ml_state.json`, com permissão 600, ignorado pelo Git. `--state-file` ou `ML_STORAGE_STATE_FILE` permite outro destino. Depois de exportar, o script tenta acessar a vitrine por HTTP com esses cookies e informa acesso confirmado, pedido de verificação ou resultado inconclusivo. Uma falha nessa consulta não significa que sua janela esteja deslogada. Confirme também a coleta em `dry-run`. A exportação não inclui localStorage nem cookies que o Firefox ainda não persistiu em disco.

`ML_BROWSER=firefox` configura apenas a coleta automatizada, não o navegador aberto no login. O coletor usa Playwright e precisa de suas dependências e navegador no ambiente em que for executado. No GitHub, essa preparação é feita pelo workflow quando o secret de sessão existe; a variável de repositório `ML_BROWSER` permite selecionar Firefox ou Chromium.

Para usar a sessão no GitHub Actions:

1. Abra Settings → Secrets and variables → Actions.
2. Crie o secret **`ML_STORAGE_STATE`**.
3. Copie o conteúdo JSON do arquivo `.secrets/ml_state.json` para esse secret. Não publique esse conteúdo em chat, commit ou artifact.
4. Execute o workflow em modo `dry-run` e confira o estado da fonte.

O workflow só instala o navegador e restaura a sessão quando esse secret existe. Se a plataforma exigir nova verificação, ou a sessão não funcionar no runner do GitHub, a fonte continua diagnosticada como indisponível. Autenticação não garante acesso permanente. A sessão não é renovada automaticamente no secret; gere e substitua o arquivo quando necessário.

## Referências de arquitetura

A organização coleta → filtro → seleção → publicação foi comparada com [bot-ofertas-telegram](https://github.com/rlucio01/bot-ofertas-telegram); a leitura de preço anterior e paginação foi comparada com [ml-crawler](https://github.com/Ysrial/ml-crawler). Os módulos do Sentinela foram escritos independentemente, sem importar código desses repositórios. A autenticação usa o mecanismo documentado de [storage state do Playwright](https://playwright.dev/python/docs/auth).

As sessões ficam fora dos arquivos versionados. O uso do navegador mantém as verificações normais da plataforma; o bot não tenta solucionar CAPTCHA ou esconder sua automação.
