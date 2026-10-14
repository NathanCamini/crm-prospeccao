# Prospecção de empresas novas por localidade

Ferramenta que carrega os **Dados Abertos do CNPJ** da Receita Federal e encontra empresas recém-abertas por UF, município e bairro, com contatos, acompanhamento de prospecção e exportação. Feita para uso pessoal e pronta para evoluir para uma equipe pequena.

> Status: **Etapas 1 a 6 concluídas.** O roteiro completo de testes está em `docs/plano_de_testes.md`.

## Instalação (3 comandos)

Requer Python 3.11 ou mais novo.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                  # Windows: copy .env.example .env
```

Todas as configurações ficam no `.env`, explicadas no próprio `.env.example`. Ele nunca vai para o git.

## Uso rápido

```bash
prospeccao amostra                    # base FICTÍCIA para testar sem baixar nada
prospeccao usuario criar SEU_NOME     # pede a senha (mín. 8 caracteres) duas vezes
prospeccao web                        # abre a interface em http://localhost:8501
pytest                                # testes automatizados
```

Base real da Receita:

```bash
prospeccao meses                      # lista os meses publicados
prospeccao carga                      # baixa e processa o mês mais recente (dezenas de minutos)
```

## Com Docker

```bash
docker compose build
docker compose run --rm prospeccao amostra                 # ou: carga
docker compose run --rm prospeccao usuario criar SEU_NOME
docker compose up -d web                                   # http://localhost:8501
docker compose --profile agendador up -d agendador         # carga automática de cada mês novo (opcional)
docker compose run --rm testes
```

Detalhes:
- Os dados ficam em `./data` no seu computador.
- No Docker Desktop (Windows/Mac), reserve para os contêineres pelo menos 30% a mais de memória que o `DUCKDB_MEMORY_LIMIT`.
- Em Linux, se o seu usuário não tiver UID 1000, use `UID=$(id -u) GID=$(id -g) docker compose ...`.

## Comandos

| Comando | O que faz |
|---|---|
| `prospeccao meses` | Lista os meses publicados pela Receita |
| `prospeccao carga [--mes AAAA-MM] [--sem-download] [--se-houver-novo]` | Carga completa: download → bronze → silver → gold |
| `prospeccao agendador [--intervalo-horas 24] [--uma-vez]` | Verifica periodicamente e carrega cada mês novo |
| `prospeccao amostra [--volume N]` | Gera e carrega uma base fictícia (com N linhas sintéticas extras) |
| `prospeccao benchmark [--linhas 60000000]` | Mede carga e consultas com volume próximo ao real |
| `prospeccao usuario criar\|senha\|remover\|listar [NOME]` | Usuários da interface |
| `prospeccao web [--endereco 127.0.0.1] [--porta 8501]` | Interface (Streamlit) |
| `prospeccao exportar --consulta NOME --formato csv\|xlsx\|pdf [--saida ARQ]` | Exporta uma consulta salva |
| `prospeccao exportar-novas [--mes AAAA-MM] [--desde AAAA-MM-DD]` | Grava o arquivo mensal de empresas novas para o agente classificador (a carga já faz isso sozinha) |
| `prospeccao exportar-parquet [--saida ARQ]` | Grava os estabelecimentos ativos num único Parquet (para o agente no Colab) |
| `prospeccao exportar-parquet-amostra [--linhas 10000] [--saida ARQ]` | Grava só uma amostra de ativos, variada em municípios e CNAEs (para enviar a uma IA com poucos tokens) |

## Carga da base

1. **Bronze:**
   - baixa os 37 `.zip` do compartilhamento da Receita para `data/bronze/AAAA-MM/zip/`, com retomada e checagem de tamanho;
   - extrai e valida o layout (número de colunas e formato dos campos-chave), falhando com mensagem clara se mudar;
   - grava `manifest.json` com o SHA-256 de cada arquivo.
   - os 37 arquivos são processados em paralelo (`BRONZE_PROCESSOS`); os CSVs temporários vão para a pasta de trabalho, que no Docker é um volume interno rápido.
2. **Silver:** converte os CSVs (Latin-1) para Parquet UTF-8 tipado, em 32 lotes por CNPJ básico, e remove duplicidades.
3. **Gold:** gera `data/gold/empresas.duckdb`, com uma linha por estabelecimento, e o publica por troca atômica (a interface pode continuar aberta, exceto no Windows sem Docker).

Cada camada registra as contagens de entrada e saída, o tempo e o pico de memória. Tudo fica na tabela `carga`.

Recursos para a base real:

| Recurso | Necessidade |
|---|---|
| Disco | ~50 GB livres durante a carga: ~7 GB de zips, ~25 GB de CSV temporário (apagado ao final), Parquet e DuckDB. A carga não começa abaixo de `ESPACO_MINIMO_GB` |
| Memória | `DUCKDB_MEMORY_LIMIT` em torno de 50% da RAM; o excesso vai para `data/tmp/`, até `DUCKDB_MAX_TEMP` |
| Meses antigos | Zips e silver além dos `MESES_MANTIDOS` mais recentes são apagados automaticamente |

## Agendamento

A Receita publica um mês por vez, por volta do meio do mês. Há três formas de manter a base atualizada:

- **Docker:**
  ```bash
  docker compose --profile agendador up -d agendador
  ```
  Verifica a cada `AGENDADOR_INTERVALO_HORAS` (padrão 24 h) e carrega só se houver mês novo.
- **Linux/macOS sem Docker (cron),** todo dia às 3h:
  ```
  0 3 * * *  cd /caminho/prospeccao-empresas && .venv/bin/prospeccao carga --se-houver-novo >> data/carga.log 2>&1
  ```
- **Windows (Agendador de Tarefas):**
  - crie uma tarefa diária com o programa `C:\caminho\prospeccao-empresas\.venv\Scripts\prospeccao.exe`, os argumentos `carga --se-houver-novo` e "Iniciar em" igual à pasta do projeto;
  - deixe a interface fechada na hora da carga, porque o Windows não deixa substituir a base aberta.

Uma trava (`data/.carga.lock`) impede duas cargas ao mesmo tempo.

## Interface

- **Login:** senha guardada só como hash (scrypt), com atraso crescente após erros. O login dura `SESSAO_HORAS` (padrão 12 h): recarregar a página não pede a senha de novo. **Sair** encerra a sessão.
- **Filtros:**
  - UF, município com busca e bairro (só aparece quando a base tem bairro);
  - período (30 a 365 dias, datas personalizadas ou qualquer data);
  - CNAE (código, prefixo, palavra-chave, secundários);
  - porte, natureza jurídica, situação, capital, matriz/filial, Simples;
  - presença de contato, palavra no nome e status de prospecção.

  Nada é consultado até clicar em **Aplicar filtros**.
- **Aba Empresas:** tabela paginada e ordenável, com colunas configuráveis e coluna de status. Clique numa linha para abrir a ficha.
- **Aba Indicadores:** total, top 10 CNAEs, porte, mês de abertura e municípios.
- **Ficha da empresa:**
  - **contato original da Receita** em destaque, exatamente como na fonte;
  - dados cadastrais, sócios e outros estabelecimentos;
  - acompanhamento (status, observação, histórico);
  - contatos complementares.
- **Consultas salvas:** salvar, executar com a base atual e ver só as empresas novas desde a última execução.
- **Exportação:** CSV, Excel e PDF. Pela tela, até 500 mil empresas; acima disso, use `prospeccao exportar`.
- A data da base carregada aparece sempre no topo.

**Acesso pela rede:** a interface escuta só em `127.0.0.1`. Para expor, use `--endereco 0.0.0.0` atrás de um proxy com HTTPS (Caddy ou Nginx, por exemplo). Nunca exponha sem HTTPS.

## Agente de perguntas (Google Colab + Gemini)

`notebooks/agente_prospeccao_colab.ipynb` é um chat em que se pergunta em português, por exemplo: *"liste os possíveis clientes que abriram o CNPJ há menos de 60 dias em São Paulo, para uma empresa que vende soluções para academias"*. A lista vem com a possibilidade de fechar negócio e pode ser baixada em CSV. As regras e as instruções de uso estão no próprio notebook.

O agente lê um único arquivo, gerado a partir da gold:

```bash
prospeccao exportar-parquet        # Docker: docker compose run --rm prospeccao exportar-parquet
```

Sai em `data/exportacoes/empresas_ativas_AAAA-MM.parquet` (~1,6 GB, só estabelecimentos ativos, sem sócios). Envie o arquivo ao seu Google Drive, compartilhe como "Qualquer pessoa com o link" e coloque o ID do link no notebook. Quem rodar só precisa da própria chave do Gemini. A cada carga mensal, suba o arquivo novo como **nova versão** do mesmo arquivo no Drive, para o ID não mudar.

## Ligação com o agente (módulo 2)

O agente classificador fica em [`../agente`](../agente/README.md). Ele lê desta pasta `data/`, sem alterar nada:

- o arquivo mensal `exportacoes/novas/AAAA-MM/empresas_novas.parquet` com o `_manifest.json`, no formato de `agente/schema/empresas_novas.v1.json`;
- a tabela `socio` da gold (`gold/empresas.duckdb`), se existir, para o sinal de sócio holding de rede.

**O arquivo do mês sai no fim de cada carga** (`EXPORTAR_NOVAS=true`, padrão). Para gerar de novo: `prospeccao exportar-novas`.

- **Empresa nova** é o estabelecimento que está na base do mês e não estava na silver do mês anterior. Por isso mantenha `MESES_MANTIDOS` em 2 ou mais.
- Na primeira carga não há mês anterior: entram as aberturas a partir do dia 1 do mês anterior ao da carga (ou de `--desde`). O critério usado fica no manifesto.
- Vão junto as três marcações que o agente usa como sinal: `contato_compartilhado` (o mesmo e-mail ou telefone em mais de 5 CNPJs da base, em geral a contabilidade), `filial_de_empresa_existente` e `pessoa_fisica_identificavel` (empresário individual, MEI ou natureza de pessoa física).
- O Parquet é gravado num temporário e renomeado, e o manifesto vem por último com o SHA-256: o agente nunca lê um arquivo pela metade.
- Uma falha nesta etapa não desfaz a carga: fica no log e no resumo (`empresas_novas.erro`).

## Contatos complementares

Na ficha, abaixo do contato da Receita (que nunca é alterado):

- **Ler site oficial:** lê o site do domínio do e-mail da Receita. Não vale para gmail, hotmail e similares.
- **Indicar site pela API de busca:** opcional (`BUSCA_API_PROVEDOR`). A API só indica o site provável; os contatos vêm do próprio site.
- **Ler outro site:** lê um site informado por você ou indicado pela API.
- **Busca manual assistida:** links prontos do Google para abrir no navegador, e um formulário de registro.
- **Em lote:** busca as empresas da página da tabela, até 100 por vez.

Cada contato guarda tipo, valor, fonte (nome e URL), data e confiança (alta, média ou baixa).

Cuidados com os sites consultados:
- respeita o robots.txt e um intervalo mínimo entre requisições;
- não repete buscas recentes (`CACHE_BUSCA_DIAS`);
- **nunca** raspa resultados do Google nem redes sociais.

Sobre as APIs de busca:
- **Google Custom Search JSON API:** fechada para novos clientes e desligada pelo Google em 01/01/2027.
- **Brave Search API:** a alternativa suportada. É paga por consulta e tem crédito mensal gratuito; confira os termos do provedor antes de contratar.

## Uso de dados e LGPD

- **Finalidade:** prospecção comercial B2B com dados públicos da Receita Federal.
- **Minimização:** de sócios guardamos só nome, qualificação, data de entrada e tipo. O CPF (que já vem mascarado), o representante legal e a faixa etária são descartados.
- **Opt-out:** todo contato comercial feito a partir desta lista deve oferecer um canal de descadastro e atender pedidos de exclusão. Marque quem pedir como "descartada" para não contatar de novo. Esse aviso também sai nos relatórios exportados.
- **Rastreabilidade:** cada registro guarda o mês de referência da base e a data da carga; cada contato complementar guarda a fonte e a data.
- **Credenciais:** ficam só no `.env`. Senhas, só como hash.

## Estrutura

```
src/prospeccao/
├── ingest/      download (Nextcloud da Receita), layout, bronze, amostra fictícia
├── transform/   silver (Parquet em lotes) e gold (DuckDB)
├── domain/      regras puras: CNPJ, empresa nova, filtros, status
├── enrich/      contatos complementares (site, API, manual)
├── reports/     CSV, XLSX e PDF
├── web/         interface Streamlit
├── consulta.py  consultas à base     app_store.py  banco do usuário (SQLite)
├── pipeline.py  carga, trava, agendador         cli.py  linha de comando
docs/     decisoes.md · dicionario_de_dados.md · plano_de_testes.md
notebooks/  agente de prospecção no Colab (Gemini + Gradio), lendo o Parquet do exportar-parquet
```

## Documentação

- `docs/decisoes.md`: todas as decisões técnicas e os pontos em aberto do escopo.
- `docs/dicionario_de_dados.md`: tabelas e campos.
- `docs/plano_de_testes.md`: roteiro completo de testes.
