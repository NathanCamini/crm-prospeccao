# Agente classificador (módulo 2)

Classifica, a cada mês, as empresas novas da Receita para cada produto, em duas etapas: o SQL elimina o grosso no DuckDB e o LLM (Gemini) decide só o que o SQL não decide. O resultado fica no banco do agente e sai num CSV que o módulo 3 entrega ao CRM.

Medido em 2026-09: 674.928 empresas novas → **190** passaram no funil do perfil `amenities_hotel` em 1,6 s, antes de qualquer chamada ao LLM.

```
módulo 1 (ETL) ──► módulo 2 (este agente) ──► módulo 3 ──► CRM
exportacoes/novas/     agente/dados/agente.sqlite   lê modulo2_saida/classificacao.csv
AAAA-MM/               └─ exportar-crm ──► modulo2_saida/classificacao.csv
```

O agente **não** baixa dados da Receita, não busca contatos e não envia mensagens: isso é dos módulos 1, 3 e 4.

## Rodar

Com Docker, da raiz do repositório:

```bash
# no .env: GEMINI_API_KEY e MODULO1_DADOS (a pasta data/ do módulo 1)
docker compose build agente
docker compose run --rm agente validar --mes 2026-09
docker compose run --rm agente classificar --mes 2026-09 --perfil amenities_hotel --modo online --amostra 10
docker compose run --rm agente classificar --mes 2026-09 --perfil amenities_hotel
docker compose run --rm agente exportar-crm
docker compose restart modulo3
```

| Pasta | O que é | No contêiner |
|---|---|---|
| `MODULO1_DADOS` (padrão `agente/modulo1/`) | saída do módulo 1: `exportacoes/novas/AAAA-MM/` e, se existir, `gold/empresas.duckdb` | `/modulo1`, só leitura |
| `agente/perfis/` | perfis (YAML) e lista de redes | `/app/perfis`, só leitura |
| `agente/dados/` | banco do agente (`agente.sqlite`) e arquivos de lote | `/dados` |
| `MODULO2_PASTA` (padrão `modulo2_saida/`) | `classificacao.csv` para o módulo 3 | `/saida` |

Sem Docker (Python 3.11+), de dentro de `agente/`:

```bash
pip install -e ".[dev]"
AGENTE_ENTRADA_DIR=../../prospeccao-empresas/data/exportacoes/novas agente validar --mes 2026-09
```

Variáveis: `GEMINI_API_KEY`, `AGENTE_ENTRADA_DIR` (padrão `modulo1/exportacoes/novas`), `AGENTE_GOLD_DB` (opcional), `AGENTE_DADOS_DIR` (`dados`), `AGENTE_SAIDA_DIR` (`../modulo2_saida`), `AGENTE_PERFIS_DIR` (`perfis`), `AGENTE_SCHEMA_DIR` (`schema`), `AGENTE_MODELO_TRIAGEM`, `AGENTE_MODELO_REVISAO`.

## Comandos

| Comando | O que faz |
|---|---|
| `agente perfis` | Lista e valida os perfis de `perfis/` |
| `agente validar --mes AAAA-MM` | Confere o arquivo do mês contra o contrato e mostra quantas empresas passam no funil de cada perfil (sem LLM) |
| `agente classificar --mes AAAA-MM --perfil P` | Classifica em lote (padrão). Rode de novo mais tarde para continuar |
| `... classificar ... --aguardar` | Espera o lote terminar, até 26 h (para um agendador) |
| `... classificar ... --modo online --amostra 10` | Chamadas uma a uma, só 10 empresas: para testar perfil, chave e modelos |
| `... classificar ... --forcar` | Reclassifica mesmo o que não mudou |
| `agente lotes` | Lotes enviados à API e o estado de cada um |
| `agente alvos --perfil P [--mes M] [--saida alvos.csv]` | Empresas classificadas como alvo |
| `agente exportar-crm [--perfil P] [--mes M] [--saida arquivo]` | Grava o CSV do CRM (padrão: todos os perfis e meses classificados) |
| `agente amostrar-rotulos --mes M --perfil P --n 200 --saida rotulos.csv` | Planilha para rotular à mão |
| `agente avaliar --perfil P --rotulos rotulos.csv` | Precisão e cobertura; sai com código 1 abaixo da meta |
| `agente revisao-mensal --mes M --perfil P --saida revisao.csv` | 20 classificações aleatórias para conferir |
| `agente custos [--mes M]` | Tokens e custo estimado por mês e perfil |

Erro de contrato, de perfil ou de funil grande sai com código 2 e a explicação no log.

## Rotina

**Primeiro perfil:**

1. `classificar --modo online --amostra 10`: confere chave, modelos e respostas.
2. `amostrar-rotulos --n 200`: uma pessoa preenche `e_alvo_humano` (sim/não) e, quando souber, `e_franquia_humano`.
3. `classificar` do mês inteiro e `avaliar --rotulos`. Para confiar no perfil, a precisão precisa ser de pelo menos 85%, com 20 ou mais empresas previstas como alvo.

**Todo mês:**

1. O módulo 1 publica `exportacoes/novas/AAAA-MM/`.
2. `validar --mes AAAA-MM`.
3. `classificar --mes AAAA-MM --perfil P --aguardar`, um perfil por vez.
4. `exportar-crm` e `docker compose restart modulo3`. O CRM copia os leads na sincronização seguinte (ou na hora, em **Clientes**).
5. `revisao-mensal`: 20 classificações conferidas por uma pessoa.
6. `custos`.

## Contrato de dados (entrada)

`schema/empresas_novas.v1.json` define colunas, tipos e significado do arquivo do mês. Antes de ler, o agente confere:

- o manifesto (`mes`, `total_linhas`, `sha256`, `versao_schema`);
- que conhece a versão (`empresas_novas.v<versao>.json` existe), senão recusa;
- o SHA-256 e o total de linhas;
- as colunas obrigatórias e os tipos, inclusive das opcionais, quando presentes.

As três marcações da gold (`contato_compartilhado`, `filial_de_empresa_existente`, `pessoa_fisica_identificavel`) são opcionais na v1: entram como sinal quando o arquivo as tiver. Coluna nova, removida ou com outro significado exige a v2, com um novo arquivo em `schema/`.

## Perfil (YAML)

Um arquivo por produto em `perfis/`; o nome do arquivo é o `id`.

| Campo | Padrão | Para quê |
|---|---|---|
| `id`, `versao`, `produto` | — | identificação; **suba a `versao`** a cada mudança de critério |
| `filtro_sql.cnae_prefixos` / `cnaes` | — | atividade; pelo menos um dos dois é obrigatório |
| `filtro_sql.incluir_cnaes_secundarios` | `false` | também quem tem a atividade só como secundária |
| `filtro_sql.situacao` | `[ATIVA]` | situação cadastral |
| `filtro_sql.capital_social_min` / `_max` | — | faixa de capital social |
| `filtro_sql.excluir_mei` | `false` | exclui MEI |
| `filtro_sql.portes`, `ufs`, `matriz_filial` | — | demais filtros |
| `criterios_llm` | — | perguntas que só o modelo responde |
| `canal_preferido` | `[]` | informativo; o agente não usa |
| `produtos_sugeriveis` | só `produto` | valores aceitos em `produto_sugerido` |
| `padroes` | econômico, médio, alto, indefinido | valores aceitos em `padrao` (precisa conter `indefinido`) |
| `limiar_alvo` | 60 | score mínimo para virar alvo |
| `revisao.score_min` | 60 | a partir deste score, o modelo maior confirma |
| `revisao.confianca_baixa` | `true` | confiança baixa também vai ao modelo maior |
| `limite_candidatos` | 5000 | trava de custo: funil maior interrompe a execução |

## Sinais de franquia e rede

O cadastro da Receita não diz se a empresa é franquia. O agente calcula sinais e o modelo decide com base neles; sem sinal, `e_franquia` fica nulo (indeterminado), nunca adivinhado pelo nome.

| Sinal | Indica | Fonte |
|---|---|---|
| nome fantasia ou razão social com o termo da marca (palavra inteira, sem acento) | marca | `perfis/marcas_rede.csv` |
| o mesmo, aproximado (erro de digitação, 85% de semelhança) | marca, evidência fraca | idem |
| e-mail da Receita no domínio da rede | rede | idem + arquivo do mês |
| sócio pessoa jurídica com nome de holding da rede | rede | tabela `socio` da gold do módulo 1 (se `AGENTE_GOLD_DB` existir) |
| filial de empresa existente | nova unidade de rede | marcação do módulo 1 |

Na base real de 2026-09, entre as 1.412 hospedagens novas (CNAE 5510/5590), os sinais acharam 14 SPEs com sócio Atlantica Hotels, 10 empresas com e-mail `@accor.com` e sócio Hotelaria Accor, e 3 filiais com "Comfort Hotel" na razão social. A lista de `marcas_rede.csv` é **inicial**, de hotelaria: complete com o catálogo da ABF e só inclua domínios confirmados.

## Saída

**Banco do agente (`agente.sqlite`).** Tabela `classificacao`, uma linha por CNPJ × perfil × versão do perfil (chave). Linhas de versões antigas ficam como histórico; a visão `classificacao_vigente` traz só a versão mais nova de cada perfil.

| Coluna | Significado |
|---|---|
| `cnpj`, `perfil_id`, `perfil_versao` | chave |
| `mes_referencia` | mês do arquivo classificado |
| `e_alvo`, `score`, `confianca` | decisão final, depois das regras |
| `e_franquia`, `marca` | `e_franquia` nulo = indeterminado; marca só vinda dos sinais |
| `revisado` | 1 = confirmado pelo modelo maior |
| `modelo`, `prompt_hash`, `entrada_hash` | rastreabilidade: qual modelo, qual prompt e quais dados geraram a linha |
| `resultado_json` | `final` (todos os campos), avisos das regras, motivo da revisão e a resposta da triagem |
| `custo_tokens`, `custo_usd` | tokens das duas etapas e custo estimado |
| `classificado_em` | data e hora |

**CSV do CRM (`exportar-crm`).** `classificacao.csv`, separador `;`, UTF-8 com BOM, nas colunas que o módulo 3 lê (lista em "Ligar ao módulo 2" no [README da raiz](../README.md)): dados cadastrais e contato da Receita tirados do arquivo do mês, mais `perfil_id`, `perfil_versao`, `score`, `e_alvo` e `e_franquia` (`sim`/`nao`, vazio = indeterminado), `justificativa`, `marca`, `padrao`, `produto_sugerido`, `classificado_em` e `mes_referencia`.

- O arquivo é gravado ao lado e renomeado: o módulo 3 nunca lê um CSV pela metade.
- Mês cujo arquivo sumiu ou mudou fica fora, com aviso. Se nada sobrar, o CSV anterior é mantido.
- Lead que deixa de vir no CSV não some do CRM para quem já o trabalhou (o CRM marca como ausente).

## Quando reclassifica

Uma empresa já classificada só volta ao LLM se:

- os dados enviados mudaram, incluindo os sinais (`entrada_hash`);
- o prompt mudou, seja nos critérios, na instrução ou no schema (`prompt_hash`);
- a `versao` do perfil subiu (nova chave);
- ou se usou `--forcar`.

Rodar o mesmo mês duas vezes não gasta nada.

## Custo

Custo estimado por empresa, ao preço de lote:

| Etapa | Tokens por empresa | Custo por empresa |
|---|---|---|
| Triagem (`gemini-2.5-flash-lite`) | ~600 de entrada, ~100 de saída | ~US$ 0,00005 |
| Revisão (`gemini-3.8-flash`) | o mesmo, mais o raciocínio do modelo | ~US$ 0,0004 |

Com 190 empresas por mês e cerca de 20% indo à revisão, o custo fica em centavos de dólar por perfil. O valor real de cada execução aparece em `custos`. A tabela de preços fica em `agente/llm.py` (fonte e data no código): os Gemini 3.6/3.8 Flash dobram de preço em 01/01/2027.

## Privacidade (LGPD)

O modelo recebe só o necessário para classificar:

| Envia | Não envia |
|---|---|
| atividade principal e secundárias | CNPJ (o resultado é ligado pela chave do lote) |
| natureza jurídica, porte e capital | telefones |
| município, bairro e data de abertura | e-mail, exceto o domínio quando não é provedor gratuito |
| nome fantasia | CPF: mascarado na razão social |
| sinais de rede | razão social de MEI e empresário individual (é o nome do titular) |

Nada é inferido sobre religião, saúde ou origem. O prompt trata os campos da empresa como dados, nunca como instruções. O CSV do CRM leva o contato da Receita, que é o que a equipe usa para o primeiro contato; ele não sai da máquina.

## Robustez

| Situação | O que acontece |
|---|---|
| Arquivo fora do contrato ou de versão desconhecida | Recusado, com a lista do que está errado |
| Funil acima de `limite_candidatos` | A execução para antes de gastar |
| Falha no envio do lote | O lote fica registrado como `criando` e é reenviado na execução seguinte, sem duplicar |
| Lote expirado (48 h) ou com falha | Marcado; as empresas voltam na execução seguinte |
| Item sem resposta ou resposta fora do schema | Vai para a revisão; se falhar de novo, é listado como falha e fica para a próxima |
| Revisão falha | Grava o resultado da triagem, com `revisado = 0` e aviso |
| Chave inválida, sem permissão ou modelo inexistente | Para na primeira empresa e guarda o que já foi feito |
| Erro transitório da API (429, 5xx) | Retentativa com espera crescente |
| Versão nova do perfil com lote antigo aberto | O lote antigo é encerrado |

## Testes

```bash
cd agente && pytest                                       # 64 testes, com LLM falso (não gasta API)
docker compose --profile testes run --rm testes-agente   # o mesmo, no contêiner
```

## Pontos em aberto

- **Contrato v1 a confirmar com o módulo 1:** o `schema/empresas_novas.v1.json` segue o documento de arquitetura; o ETL precisa produzir exatamente esse arquivo em `exportacoes/novas/AAAA-MM/`.
- **Primeira execução real:** o JSON Schema no formato do lote (`responseJsonSchema`) ainda não foi testado contra a API real. Faça o teste online com `--amostra` e um lote pequeno antes do mês inteiro.
- **Capital mínimo no perfil de exemplo:** na base real de 2026-09, todas as 27 empresas com sinal de rede (Atlantica, Accor) têm capital de R$ 4.600 a R$ 50.000. Com `capital_social_min: 100000`, hotéis de bandeira ficam fora do funil. Revise se eles interessam.
- **Franquia ou unidade própria:** sócio holding e e-mail da rede mostram que a unidade é da rede, mas não distinguem franquia de unidade própria. O modelo decide com os dados, e a rotulagem mede o acerto.
- **Perguntas do CRM:** o endpoint `POST /perguntar` que o módulo 3 chama (`AGENTE_URL`) não faz parte deste agente; sem ele o CRM responde em modo simulado.

## Decisões

Numeração contínua com o README da raiz.

| # | Decisão | Motivo |
|---|---|---|
| D57 | Funil em duas etapas: filtro SQL do perfil no DuckDB, LLM só em quem passou; perfil sem filtro de atividade é recusado e funil acima de `limite_candidatos` para a execução. | Em 2026-09, 674.928 empresas viraram 190 candidatas em 1,6 s; a trava impede gasto por erro de perfil. |
| D58 | Contrato de dados versionado na entrada (manifesto, SHA-256, total, colunas e tipos). | O agente nunca classifica um arquivo pela metade ou com outro significado; versão desconhecida é recusada. |
| D59 | Triagem com `gemini-2.5-flash-lite` pela API de lote; revisão com `gemini-3.8-flash` só para score alto, dúvida, incoerência ou falha; lote gravado no banco antes do envio. | O modelo mais barato, pela metade do preço, faz o grosso; queda, expiração ou falha de envio não perdem nem duplicam empresas. |
| D60 | Saída validada por Pydantic e corrigida por regras: franquia sem sinal vira indeterminada, marca só vinda dos sinais, padrão e produto só do perfil, alvo exige `limiar_alvo`. | Franquia não se adivinha pelo nome; o modelo não consegue furar as regras do negócio. |
| D61 | Minimização de dados pessoais no que vai ao LLM (sem CNPJ, telefone e e-mail; CPF mascarado; sem razão social de MEI). | LGPD: o modelo recebe só o necessário para classificar. |
| D62 | Banco próprio do agente (`agente.sqlite`), chave CNPJ × perfil × versão; reclassifica só se mudarem os dados enviados ou o prompt. | Cada módulo tem o próprio estado (como o `crm.sqlite`); rodar o mesmo mês de novo não gasta nada. |
| D63 | O agente entrega ao módulo 3 um CSV (`exportar-crm`), gravado de uma vez, na pasta que o módulo 3 já lê. | É o formato que o módulo 3 simulado aceita (`MODULO2_CSV`); a cadeia segue elo a elo e o CRM não muda. O sinal por site saiu, porque o site vem do módulo 3, que roda depois do agente. |
