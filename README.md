# Sentinela

Buscador de produtos e monitor de preços com alertas no Discord. Você escolhe termos como **ssd**, **memoria ram** ou **mochila tatica**; o programa procura produtos, mantém um catálogo automaticamente e acompanha os preços encontrados.

## Como usar

Requer Python 3.11 ou superior. Execute na raiz do projeto.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp -n .env.example .env
sentinela --check-config
sentinela --search
sentinela --dry-run
sentinela
```

- `--check-config`: valida arquivos e ambiente sem acessar rede ou banco.
- `--search`: procura produtos, mostra nomes e links e atualiza o catálogo. Não registra preços nem envia mensagens.
- `--dry-run`: procura e consulta preços, avaliando o histórico em memória. Não altera catálogo ou banco e não publica.
- Sem opções: procura produtos, atualiza catálogo, coleta preços, salva histórico e publica uma seleção de novidades, mudanças de preço e descontos com preços e links, respeitando orçamento e deduplicação.
- `--report-all`: inspeção manual de todos os resultados coletados, sem registrar republicação.
- `--ml-login`: login assistido do Mercado Livre, com sessão fora do Git.
- `--no-results`: desativa o resumo da busca; mantém apenas alertas de desconto com histórico.
- `--manual-only`: acompanha somente `config/products.json`, para quem quiser escolher produtos individualmente.

Os mesmos comandos funcionam com `python -m sentinela.cli` no lugar de `sentinela`. A biblioteca `python-dotenv` carrega o arquivo `.env`; não existe um script local com esse nome.

## Você escolhe buscas, não cada produto

Edite **`config/searches.json`**. A configuração atual cobre SSDs de 240/256 e 500/512 GB, NVMe, placa-mãe, controladora RGB, consoles e relógios Xiaomi/Samsung na KaBuM; luva, combat/farda, coyote, mochila, colete e MOLLE/modular na For Honor; produtos Atalaia na Coturno Atalaia; jaqueta e bota na Tutto Moto. Há até seis resultados por termo e limite de 120 produtos, distribuídos entre as lojas. A vitrine de ofertas do Mercado Livre está preparada para até três páginas e depende de acesso utilizável ou sessão autenticada. Alguns termos podem não ter resultados na primeira página. Termos de categoria foram simplificados para acompanhar os títulos usados pelas lojas: mochila, colete, combat e modular.

```json
{
  "max_products": 30,
  "searches": [
    {
      "term": "ssd 1tb",
      "category": "tecnologia",
      "stores": ["kabum"],
      "limit": 5,
      "exclude": ["case", "adaptador"],
      "enabled": true
    }
  ]
}
```

`category` aceita `tecnologia` ou `militar`; ela organiza o catálogo, não determina a busca. `term` define as palavras que devem aparecer no título, ignorando acentos e maiúsculas. `exclude` remove títulos contendo os termos indicados. `limit` limita os resultados por termo e loja (1–20); `max_products` limita o catálogo automático (1–300). `min_price_brl` e `max_price_brl` limitam quais preços podem gerar alertas. Relógios de treino estão configurados entre R$ 400 e R$ 1.600. Preços fora da faixa podem entrar no catálogo e histórico, mas não geram alertas; os limites são conferidos na página do produto, não no preço anunciado na busca.

Adaptadores: `kabum`, `forhonor`, `atalaia`, `tuttomoto`, `terabyte`, `mercadolivre` e `amazon`. Buscas públicas na KaBuM, For Honor, Coturno Atalaia e Tutto Moto foram verificadas; as demais fontes podem retornar bloqueio, exigir JavaScript ou não entregar produtos reconhecíveis. Não há promessa de funcionamento em todos os domínios. O programa informa a falha e não tenta contornar bloqueios.

A descoberta usa `Product` em JSON-LD e links de páginas de produto reconhecidos para cada loja. Títulos fora do termo, links de categorias e domínios externos são descartados. Não são usados percentuais promocionais das lojas como evidência de desconto.

O catálogo fica em `data/discovered.json`: produtos novos têm prioridade, e os anteriores continuam acompanhados enquanto houver espaço no limite. Quando cheio, os anteriores ao limite deixam de ser acompanhados; o histórico SQLite não é apagado. Falhas de busca permitem acompanhar os produtos já salvos, mas a execução informa falha. O catálogo é reconciliado com os termos habilitados a cada execução: produtos que deixaram de corresponder às buscas saem da seleção, e mudanças de faixa de preço também se aplicam aos produtos salvos. O histórico anterior permanece no SQLite. `config/products.json` permanece como lista manual opcional.

## Sua parte

1. **Escolher os termos** em `config/searches.json` e ajustar lojas, exclusões e limites. Não é necessário cadastrar produtos um por um.
2. **Revogar o webhook anteriormente exposto** e criar outro nas integrações do canal do Discord. Guardar o substituto no `.env`, em `DISCORD_WEBHOOK_URL`. A remoção do código não invalida a credencial exposta.
3. **Rodar busca e simulação** e conferir os resultados. Você pode coletar sem webhook; nesse caso não há publicação.
4. **Enviar o projeto ao GitHub** e cadastrar o secret `DISCORD_WEBHOOK_URL` para executar o monitor automaticamente.
5. **Acompanhar os logs** e conferir vendedor, variante, preço final e frete antes de comprar ou divulgar.

O bot publica uma seleção de até 12 produtos por rodada, priorizando queda frente ao histórico, desconto anunciado, baixa desde a última publicação e novidades. Preço anterior informado pela loja não é confundido com mediana histórica. A mesma oferta/preço não é repetida durante sete dias, e uma rodada sem novidades não reenviará o catálogo inteiro.

**Consulte [docs/OFERTAS.md](docs/OFERTAS.md) para as regras, os novos parâmetros e o passo a passo do Mercado Livre.** Sua etapa é concluir o login assistido e cadastrar o secret `ML_STORAGE_STATE` caso queira usar essa fonte no GitHub. O webhook já configurado continua sendo usado para as publicações.


## Ambiente

| Variável | Padrão | Uso |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | vazio | Webhook do Discord, opcional para coletar |
| `SEARCHES_FILE` | `config/searches.json` | Termos e lojas |
| `DISCOVERY_FILE` | `data/discovered.json` | Catálogo automático |
| `PRODUCTS_FILE` | `config/products.json` | Produtos manuais opcionais |
| `DATABASE_PATH` | `data/history.sqlite3` | Histórico e alertas enviados |
| `MIN_DISCOUNT` | `0.20` | Fração entre 0 e 1 |
| `MIN_SAMPLES` | `5` | Mínimo de observações anteriores |
| `MIN_HISTORY_DAYS` | `7` | Idade mínima do histórico |
| `REQUEST_TIMEOUT` | `15` | Timeout HTTP em segundos |
| `USER_AGENT` | `SentinelaOfertas/0.1` | Identificação HTTP |

Caminhos relativos ao diretório atual. `.env` deve ficar fora do Git. O `.env` local não configura o GitHub Actions. A lista manual, quando usada, exige ID único, nome, categoria, loja e URL HTTPS de produto; frete aceita zero ou `null`. O frete manual é mostrado, mas não entra no cálculo do desconto.

Códigos de saída: **0** sucesso; **1** ausência de buscas/produtos ou falha de busca, coleta ou envio; **2** configuração/persistência inválida. Execuções com falhas parciais salvam os resultados válidos. Produtos identificados com todas as ofertas declaradas sem estoque, descontinuadas ou em pré-venda são contados como indisponíveis e não causam erro na execução; ausência de dados ou bloqueio continua sendo falha de coleta. Sem webhook, uma coleta pode retornar sucesso, avisando que uma oferta não foi publicada.

## GitHub Actions

1. Envie os arquivos ao repositório, sem `.env` nem credenciais.
2. Em **Settings → Secrets and variables → Actions**, crie o secret `DISCORD_WEBHOOK_URL`.
3. Em **Settings → Actions → General**, permita leitura e escrita para workflows; a branch deve permitir push pelo bot.
4. Execute **Actions → Sentinela monitor → Run workflow** e escolha o modo: `monitor` coleta e avalia alertas; `search` apenas busca; `dry-run` simula sem gravar; `report-all` publica o catálogo inteiro para inspeção manual. Confira o resumo e os logs; os arquivos de dados também ficam disponíveis como artifact por sete dias.

O workflow executa automaticamente em pushes de código/configuração para `main`, permite disparo manual e procura e monitora a cada três horas (`17 */3 * * *`, UTC), sujeito a atrasos. Catálogo e SQLite são commitados mesmo após falhas parciais. Se o push falhar, os dados dessa execução não ficam persistidos. Os testes de CI não acessam lojas nem Discord. Versionar SQLite é uma solução inicial: para escala, use armazenamento persistente externo. Evite executar instâncias simultâneas sobre os mesmos arquivos.

## Código e testes

| Módulo | Responsabilidade |
| --- | --- |
| `config.py` | Ambiente, produtos e validação |
| `discovery.py` | Buscas, filtros e catálogo |
| `extraction.py` | Preço JSON-LD do produto identificado |
| `money.py` | Conversão monetária |
| `storage.py` | SQLite, mediana e publicações confirmadas |
| `pricing.py` | Observações de preço e referência anunciada |
| `selection.py` | Classificação, diversidade e intervalo de repetição |
| `mercadolivre.py` | Cards de ofertas, paginação e sessão opcional |
| `notifications.py` | Mensagens do Discord |
| `monitor.py` | Coleta e avaliação |
| `cli.py` | Fluxo automático e comandos |
| `core.py` | Reexportação das funções públicas anteriores |

Execute `pytest -q`. Os testes usam páginas e respostas simuladas, sem publicar mensagens. A seleção é deduplicada por ID/preço e intervalo, registrando apenas os lotes confirmados; envio malsucedido preserva a elegibilidade dos itens ainda não enviados.

## Limites

Buscas comuns consultam a primeira página pública; a vitrine do Mercado Livre admite paginação limitada e sessão opcional autenticada. Não substitui um mecanismo universal de busca, não compara automaticamente o mesmo modelo entre lojas e não verifica cupons, vendedor ou frete por CEP. Algumas ofertas JSON-LD não informam disponibilidade; isso não comprova estoque. A mediana usa todo o histórico, sem janela móvel. Ofertas referenciadas por `@id` fora do objeto ainda não são resolvidas. Produtos sem preço BRL atribuível à sua identidade são ignorados.
