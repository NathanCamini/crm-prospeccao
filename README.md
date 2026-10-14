# CRM de prospecção (módulo 4)

Interface onde as equipes das empresas clientes trabalham os leads recém-abertos que o agente classificou: veem por que cada um é alvo, contatam por clique (e-mail, WhatsApp, formulário, ligação), mudam o estágio, agendam o próximo passo, registram vendas, cadastram leads que encontraram por conta própria e perguntam ao agente. Tudo o que cada pessoa faz fica na auditoria.

O CRM não processa a base. Ele guarda uma cópia dos leads que recebe do módulo 3, mostra e registra o que cada pessoa faz.

```
web (React + TS) ──► crm-api (FastAPI) ──► módulo 3 ──► módulo 2 (agente) ──► módulo 1 (ETL)
                       │                    (contatos)
                       └─ crm.sqlite: cópia dos leads, empresas clientes, pessoas, estágios, histórico,
                                      próximos passos, vendas, modelos, não contatar, auditoria
```

Cada elo só fala com o vizinho. O CRM nunca lê o Parquet, o DuckDB ou o CSV do agente; tudo chega pelo módulo 3, no formato de [`contrato/lead.v1.json`](contrato/lead.v1.json).

> **Atualizando.** Quem já usa a versão com empresas clientes não precisa fazer nada: o banco é migrado sozinho na subida e nada se perde. Só quem rodou a primeiríssima versão (sem empresas clientes) precisa apagar `dados/crm.sqlite` uma vez. Daqui em diante toda mudança de banco vem como migração.

## Rodar com Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose run --rm crm-api python -m crm superadmin criar seu-nome --nome-exibicao "Seu Nome"
```

Abra http://localhost:8080 e entre com o superadmin. Em **Clientes**, cadastre a primeira empresa cliente e o administrador dela. O sistema gera uma senha provisória para você repassar; no primeiro acesso a pessoa cria a própria senha e, em **Equipe**, cadastra os funcionários.

O login fica salvo por 30 dias se "Manter conectado" estiver marcado.

| Comando | Para quê |
|---|---|
| `docker compose run --rm crm-api python -m crm usuario listar` | listar todas as pessoas, com papel e empresa |
| `docker compose run --rm crm-api python -m crm usuario senha NOME` | senha provisória para qualquer pessoa (derruba as sessões abertas) |
| `docker compose run --rm crm-api python -m crm superadmin senha seu-nome` | trocar a sua senha pelo terminal |
| `docker compose run --rm crm-api python -m crm empresa listar` | listar empresas clientes |
| `docker compose run --rm crm-api python -m crm sincronizar` | copiar agora os leads do módulo 3 |
| `docker compose run --rm crm-api python -m crm auditoria verificar` | conferir se algum registro da auditoria foi alterado ou apagado direto no arquivo |
| `docker compose --profile testes run --rm testes` | testes da API |
| `docker compose --profile testes run --rm testes-agente` | testes do agente (módulo 2) |
| `docker compose --profile testes run --rm testes-modulo3` | testes do módulo 3 |
| `docker compose exec modulo3 python -m modulo3 enriquecer` | buscar agora os contatos das empresas-alvo |
| `docker compose logs -f crm-api` | acompanhar a API |

Os dados do CRM ficam em `./dados/crm.sqlite`. Em Linux, se aparecer "Permission denied" em `./dados`, rode com `UID=$(id -u) GID=$(id -g) docker compose …`.

### Rotina do mês, sem intervenção

Com `GEMINI_API_KEY` e `MODULO2_CSV=/modulo2/classificacao.csv` no `.env`:

```bash
docker compose --profile agendador up -d
```

| Etapa | Serviço | O que faz |
|---|---|---|
| 1 | `etl-agendador` | verifica todo dia se a Receita publicou um mês novo; carrega e grava o arquivo do mês para o agente |
| 2 | `agente-rotina` | a cada `AGENTE_ROTINA_HORAS` (6), classifica o mês mais novo em todos os perfis, em lote; quando tudo termina, grava o `classificacao.csv` |
| 3 | `modulo3` | relê o CSV e busca os contatos das empresas-alvo |
| 4 | `crm-api` | copia os leads a cada 30 minutos |

Cada etapa só lê o que a anterior gravou, então qualquer uma pode parar e retomar depois sem perder nada.

Ficam com a equipe:

- a conferência mensal das classificações (`agente revisao-mensal`);
- o acompanhamento de custos (`agente custos`).

O terminal do módulo 1 também está aqui: `docker compose --profile etl run --rm etl carga` (ou `amostra`, `exportar-novas`, `usuario criar NOME`). A base fica em `MODULO1_DADOS` (padrão `./etl/data`).

### HTTPS

Para acessar pela internet, aponte um domínio para a máquina, libere as portas 80 e 443 e, no `.env`:

```env
CRM_DOMINIO=crm.suaempresa.com.br
CRM_COOKIE_SECURE=true
```

```bash
docker compose --profile https up -d
```

O Caddy emite e renova o certificado sozinho (Let's Encrypt) e repassa ao nginx. A porta 8080 continua só em `127.0.0.1`.

## Rodar sem Docker (desenvolvimento)

Três terminais, Python 3.11+ e Node 20+:

```bash
# 1. módulo 3 (sem MODULO2_CSV, com os dados simulados)
cd modulo3 && pip install --no-deps -e ../etl && pip install -e ".[dev]" && uvicorn modulo3.main:app --port 8100

# 2. API do CRM
cd backend && pip install -e ".[dev]"
python -m crm superadmin criar seu-nome
MODULO3_URL=http://localhost:8100 python -m crm servir --porta 8000

# 3. interface (abre em http://localhost:5173, com /api redirecionado para a 8000)
cd frontend && npm install && npm run dev
```

## Quem faz o quê

O sistema atende várias empresas clientes. Cada uma tem a própria equipe, os próprios leads (estágio, responsável e histórico), os próprios modelos de mensagem e a própria lista de não contatar. Uma empresa não vê nada da outra, mesmo quando as duas trabalham o mesmo CNPJ.

| | Superadmin (você) | Administrador da empresa | Vendedor |
|---|---|---|---|
| Quantos | um só, criado pelo terminal | um ou mais por empresa | quantos quiser |
| Empresas clientes | cadastra, edita, desativa e entra em qualquer uma | — | — |
| Leads que vê | todos da empresa em que entrou | todos da empresa | os livres e os seus |
| Lead de um colega | altera e transfere | altera e transfere | não vê e não altera |
| Equipe | o mesmo que o administrador, em qualquer empresa | cadastra, muda o papel, cria senha provisória, desativa e remove; vê o que cada um fez | — |
| Modelos de mensagem | edita | edita | só lê |
| Não contatar | inclui, libera e cria bloqueios que valem para todas as empresas | inclui e libera os da empresa | só inclui |
| Cadastrar lead | sim | sim | sim (fica responsável por ele) |
| Próximos passos | de qualquer pessoa | de qualquer pessoa da empresa; agenda para outros | os seus |
| Vendas | registra e estorna | registra e estorna | registra; não estorna |
| Relatórios | da empresa em que entrou | da empresa | — |
| Auditoria | de todas as empresas, mais o teste de integridade | da empresa | — |

**Responsável pelo lead.** Lead sem responsável está livre. Quem registra o primeiro contato com um lead livre vira o responsável; o vendedor também vira responsável ao mudar o estágio ou anotar, e pode assumir ou liberar um lead pela ficha. Só administradores transferem de uma pessoa para outra. Cada mudança fica no histórico.

**Senhas provisórias.** Toda pessoa cadastrada pela tela recebe uma senha provisória e só consegue trabalhar depois de criar a própria. Desativar uma pessoa ou uma empresa encerra as sessões abertas na hora. Remover alguém deixa os leads dela livres e mantém o histórico com o nome.

**Perfis de lead por empresa.** No cadastro da empresa cliente você escolhe quais perfis do agente ela recebe (por exemplo, só hotelaria). Sem nenhum marcado, ela vê todos.

## Leads: cópia local e cadastro manual

**Cópia local.** A cada 30 minutos (`CRM_SINCRONIZAR_MINUTOS`) o CRM copia os leads do módulo 3 para a base dele. A lista, os filtros, a busca e a exportação trabalham sobre essa cópia, então o CRM continua funcionando se o módulo 3 estiver fora do ar, e um lead que sumiu da carga do mês continua visível para quem já mexeu nele, com o aviso "não veio na última carga". O superadmin vê a última sincronização e pode rodar uma na hora, em **Clientes**.

**Cadastro manual.** Em **Leads → Cadastrar lead**, qualquer pessoa registra uma empresa que achou por conta própria (indicação, feira, porta a porta). Precisa de nome e de pelo menos um contato; CNPJ é opcional, mas se vier é conferido (dígito verificador) e, se o lead já existir, o CRM aponta para ele em vez de duplicar. O lead manual pertence só à empresa cliente que o cadastrou, aparece com o selo "Cadastrado" e pode ser editado depois. Ele segue as mesmas regras de contato, não contatar e histórico dos demais, e as perguntas ao agente sobre ele levam os dados cadastrados.

## Próximos passos, vendas e relatórios

- **Próximos passos.** Na ficha, "o que fazer e quando" (ligar, mandar amostra, voltar a falar). A **Agenda** mostra os seus, separados em atrasados, hoje e próximos; o número no menu é o que está atrasado ou vence hoje. A lista de leads pode ser ordenada pelo próximo passo.
- **Vendas.** Valor, data e o que foi vendido. Registrar a primeira venda leva o lead a "Cliente"; mudar o estágio para "Cliente" oferece registrar a venda. Venda não se apaga: o administrador estorna com motivo, e ela fica riscada na ficha e fora dos totais.
- **Relatórios** (administradores). Por período: valor vendido, ticket médio, novos clientes, contatos, taxa de resposta, passos atrasados; vendas por semana; tabela por pessoa, por canal e por perfil; onde estão os leads trabalhados. Exporta vendas, atividades e próximos passos em CSV (abre direto no Excel). A lista de leads também exporta o que está filtrado.

## Auditoria

Tudo o que acontece fica registrado: quem, quando, de qual IP e navegador, em qual rota e com qual resultado.

- **Alterações** guardam o antes e o depois de cada campo que mudou: estágio, responsável, dados de um lead manual, modelo de mensagem, papel de uma pessoa, perfis de uma empresa, venda e estorno, não contatar.
- **Acessos**: abrir uma ficha, listar, exportar, consultar a própria auditoria.
- **Segurança**: login certo e errado (com o motivo), saída, troca e redefinição de senha, superadmin entrando numa empresa.
- **Recusas e negações**: um vendedor tentando abrir a equipe, um contato bloqueado pelo não contatar, uma requisição sem o cabeçalho de proteção. Esses ficam registrados mesmo quando nada foi alterado.
- **Cliques que não passam pelo servidor**: copiar a mensagem, abrir o site, clicar para ligar, abrir o WhatsApp, copiar a senha provisória de alguém.
- **Sistema**: sincronizações e comandos rodados no terminal.

A tela **Auditoria** filtra por período, pessoa, tipo de ação e resultado, mostra o antes e depois em tabela, junta acessos repetidos ("8 vezes seguidas") e exporta CSV. Na ficha, o administrador tem o atalho "Ver na auditoria".

**Ninguém consegue apagar nem mudar um registro.** A tabela recusa `UPDATE` e `DELETE` no próprio banco, e cada registro carrega o hash do anterior (cadeia SHA-256). Se alguém editar o arquivo direto, a conferência aponta o primeiro registro adulterado — botão **Conferir integridade** na Auditoria (superadmin) ou `python -m crm auditoria verificar`. Não há expiração: a auditoria só cresce (cerca de 0,5 KB por registro; uma equipe de 10 pessoas trabalhando o dia todo chega à ordem de algumas centenas de MB por ano, o que o SQLite aguenta bem).

## Dados simulados

Sem `MODULO2_CSV`, o módulo 3 serve 44 empresas fictícias de hotelaria e restaurantes no RS e em SC (39 classificadas como alvo), já com classificação, contatos complementares e alguns casos difíceis (unidade de rede, filial, e-mail de contabilidade).

Para não mandar mensagem a ninguém por engano:

- os e-mails fictícios usam o domínio reservado `.test`, que não recebe mensagens;
- quando a fonte é "simulado", os botões registram o contato no histórico mas **não abrem** WhatsApp, e-mail nem formulário. A interface mostra "Dados simulados" no topo.

## Ligar ao módulo 2 (agente)

O agente fica em [`agente/`](agente/README.md). Dá para usar a saída dele antes de existir o módulo 3 real.

**Leads vindos do CSV do agente.** Com o mês classificado, o agente grava `classificacao.csv` em `MODULO2_PASTA` (a mesma pasta que o módulo 3 lê). Defina o arquivo no `.env` uma vez; daí em diante o módulo 3 relê o CSV sozinho sempre que o agente grava um novo:

```bash
docker compose run --rm agente exportar-crm
```

```env
MODULO2_CSV=/modulo2/classificacao.csv
```

O agente grava uma linha por CNPJ × perfil, da versão vigente de cada perfil, com todas as colunas abaixo. Separador `;` ou `,`, UTF-8. Colunas obrigatórias: `cnpj`, `razao_social`, `perfil_id`, `score`. Opcionais: `nome_fantasia`, `uf`, `municipio`, `bairro`, `cnae_principal`, `cnae_descricao`, `porte`, `capital_social`, `data_inicio_atividade` (AAAA-MM-DD), `matriz_filial`, `situacao_cadastral`, `logradouro`, `numero`, `cep`, `ddd_1`, `telefone_1`, `ddd_2`, `telefone_2`, `email`, `e_alvo`, `perfil_versao`, `justificativa`, `e_franquia`, `marca`, `padrao`, `produto_sugerido`, `classificado_em`, `mes_referencia`. Sem `e_alvo`, entra como alvo quem tem score ≥ 60.

Com o CSV real os links passam a abrir de verdade (a fonte deixa de ser "simulado").

**Perguntas ao agente.** O agente responde em `POST /perguntar` com o comando `agente servir`. No Docker:

```env
GEMINI_API_KEY=...
AGENTE_URL=http://agente-perguntas:9000/perguntar
```

```bash
docker compose --profile perguntas up -d
```

O caminho segue elo a elo: CRM → módulo 3 → agente.

- Pergunta feita na ficha de uma empresa: o agente recebe os dados dela e a classificação. A resposta fica no histórico da empresa.
- Pergunta geral: o agente recebe o resumo das classificações e os principais alvos do último CSV.
- O agente nunca recebe CNPJ, telefone nem e-mail. Dos contatos encontrados, vai só o tipo e a confiança.
- Sem `AGENTE_URL`, o CRM segue com a resposta simulada.

## Módulo 3: contatos complementares

O módulo 3 ([`modulo3/`](modulo3)) fica entre o agente e o CRM. Com `MODULO2_CSV`, ele:

1. lê o `classificacao.csv` do agente, e de novo sempre que o arquivo muda;
2. busca em segundo plano os contatos das empresas-alvo (a cada `MODULO3_ENRIQUECER_MINUTOS`, até `MODULO3_LOTE` empresas por vez);
3. entrega cada lead ao CRM com os contatos encontrados, cada um com fonte, endereço e confiança.

A busca é a mesma do módulo 1 (`etl/src/prospeccao/enrich`), sem cópia de código:

- lê o site do domínio do e-mail da Receita (não vale para gmail, hotmail e similares), respeitando o robots.txt e um intervalo mínimo por site;
- link de WhatsApp no site vira contato `whatsapp`;
- com `BUSCA_API_PROVEDOR=brave`, quem não tem site próprio no e-mail passa pela API, que só indica o site provável. Se a confiança não for baixa, o módulo 3 lê esse site;
- nunca raspa o Google nem redes sociais;
- uma empresa já buscada só volta à fila depois de `CACHE_BUSCA_DIAS`;
- o contato original da Receita nunca é alterado e não é repetido como complementar.

Os contatos ficam no volume `modulo3_dados` (`modulo3.sqlite`). O `GET /saude` mostra quantas empresas já têm contato e o resumo do último ciclo.

| Endpoint | Retorno |
|---|---|
| `GET /saude` | `{"fonte": "modulo2" \| "simulado", "mes_referencia", "enriquecimento", ...}` |
| `GET /v1/perfis` | lista de perfis com total de alvos |
| `POST /v1/leads/busca` | página de leads; aceita `cnpjs`, `excluir_cnpjs` e `apenas_cnpjs` (o CRM usa para filtrar por estágio) |
| `GET /v1/leads/{cnpj}` | um lead |
| `POST /v1/perguntar` | repassa ao agente |

## O que o CRM faz

- **Leads**: funil com a contagem por estágio (clique filtra), busca, responsável, perfil, origem (agente ou cadastrado), UF, município, pontuação mínima, ordenação (inclusive por próximo passo e última atividade) e exportação CSV. Os filtros ficam na URL, então voltar da ficha mantém a lista.
- **Ficha**: por que é alvo, contato original da Receita (sem alteração), contatos do módulo 3 com fonte e confiança, dados cadastrais, próximos passos, vendas, histórico.
- **Contatar**: escolhe canal, destino e modelo; a mensagem chega pronta e editável. O clique abre o WhatsApp, o programa de e-mail ou o formulário do site com o texto, e registra o envio. Empresa em "Novo" passa para "Em contato".
- **Não contatar**: e-mail, telefone, domínio ou CNPJ. Bloqueia a preparação e o registro de qualquer contato. "Não contatar mais" na ficha bloqueia a empresa e todos os contatos dela de uma vez.
- **Modelos de mensagem**: variáveis `{nome_fantasia}`, `{municipio}`, `{produto}`, `{vendedor}` e outras. Os modelos iniciais dizem de onde veio o contato e como pedir para sair.
- **Perguntar ao agente**: conversa geral ou pergunta sobre uma empresa.
- **Equipe** (administradores): quantos leads cada pessoa tem e em que estágio, contatos dos últimos 7 dias, última atividade e o feed do que a equipe fez, filtrável por pessoa.
- **Clientes** (superadmin): empresas clientes com pessoas, leads em trabalho, clientes fechados e contatos da semana; botão para entrar em cada uma.
- **Agenda**, **Relatórios** e **Auditoria**: ver acima.
- **Minha conta**: trocar a senha.

Não há envio automático. Ele entra na fase seguinte, só para e-mail e com aprovação, como no plano.

## Segurança

- Senhas com scrypt; login lento a cada erro e mesma resposta para usuário inexistente.
- Sessão em cookie `HttpOnly`, `SameSite=Strict`; no banco só fica o hash do código da sessão. `Secure` liga com `CRM_COOKIE_SECURE=true` (use atrás de HTTPS).
- Toda requisição que altera dados exige o cabeçalho `X-CRM: 1`, que outro site não consegue enviar.
- Por padrão a porta só escuta em `127.0.0.1`.
- Contêineres sem root; nginx com cabeçalhos de segurança e CSP.
- O IP gravado na auditoria é o que o nginx viu; um `X-Forwarded-For` mandado pelo cliente é ignorado.
- Atrás do Caddy (perfil `https`), o nginx aceita o IP do cliente só vindo do endereço fixo do Caddy na rede `borda`. Quem acessa direto a 8080 continua com o IP da conexão.
- Se puser outro proxy na frente, troque `set_real_ip_from` em `frontend/nginx.conf` pelo endereço dele.

## Testes

```bash
cd backend && pytest        # 77 testes: login, sessão, papéis, isolamento entre empresas, estágios, contato, supressão, agente,
                            # cópia local, lead manual, próximos passos, vendas, relatórios, exportação, auditoria e integridade
cd modulo3 && pytest        # 15 testes: dados fictícios, busca, CSV do agente, releitura, busca de contatos
cd etl && pytest            # 192 testes do módulo 1: carga, arquivo do mês para o agente, consultas, contatos, exportação, interface
cd agente && pytest         # 88 testes: contrato, perfil, funil, sinais, lote retomável, regras, avaliação, CSV do CRM, perguntas, rotina
cd frontend && npm run build  # checagem de tipos + build
```

## Estrutura

```
backend/        API do CRM (FastAPI + SQLite)
frontend/       interface (React + TypeScript + Vite), servida por nginx
deploy/         Caddyfile do HTTPS (perfil "https")
etl/            módulo 1: carga dos Dados Abertos do CNPJ (DuckDB), interface Streamlit e agente do Colab — ver etl/README.md
modulo3/        módulo 3: contatos complementares e entrega ao CRM (FastAPI); dados simulados sem o CSV
agente/         módulo 2: agente classificador (perfis, contrato do mês, Gemini) — ver agente/README.md
contrato/       contrato v1 entre módulo 3 e CRM (JSON Schema)
dados/          banco do CRM (montado no contêiner)
modulo2_saida/  CSV que o agente grava e o módulo 3 lê
```

## Decisões

| # | Decisão | Motivo |
|---|---|---|
| D35 | O CRM fala só com o módulo 3 por HTTP; o módulo 3 só com o 2. | Pedido explícito; cada módulo pode ser trocado sem mexer nos outros. |
| D36 | Banco próprio do CRM (`crm.sqlite`). *(Revista na D49: o CRM passou a guardar uma cópia dos leads.)* | Estágio e histórico ficam ligados ao CNPJ e sobrevivem a qualquer carga, como na D25. Os dados da empresa são sempre os mais recentes do módulo 3. A partir daqui o estágio vive no CRM, não mais no `prospeccao_status` do módulo 1. |
| D37 | Estágios: novo, em contato, respondeu, interessado, amostra enviada, cliente, sem interesse, descartado. | Cobre o funil de produto físico com amostra; "novo" é quem não tem registro. |
| D38 | Filtro por estágio passa ao módulo 3 a lista de CNPJs. | Mesmo mecanismo da D25; o CRM não precisa guardar a base para filtrar. |
| D39 | Contato só por clique, com registro. Sem envio automático nesta fase. | Regras do WhatsApp e da LGPD (ver documento da solução); o envio automático de e-mail fica para a fase com aprovação. |
| D40 | Fonte "simulado" desliga a abertura de links e os e-mails fictícios usam `.test`. | Evita mandar mensagem a um número real a partir de dado inventado. |
| D41 | Interface e API na mesma origem (nginx). | O cookie de sessão não precisa de CORS nem de `SameSite=None`. |
| D42 | Login persistente por cookie de 30 dias, revogável. | Pedido do usuário; trocar senha ou sair encerra as sessões. |
| D43 | Três papéis: superadmin (um só), administrador da empresa cliente e vendedor. | Pedido do usuário: o acesso total fica só com o dono do sistema; cada empresa se administra. |
| D44 | O superadmin só é criado pelo terminal, e o comando recusa um segundo. | Nenhuma tela, nem a de um administrador, consegue criar ou promover alguém a superadmin. |
| D45 | Tudo que é de trabalho fica separado por empresa cliente (`empresa_id`). | Duas empresas podem prospectar o mesmo CNPJ sem ver o estágio, o histórico ou os contatos uma da outra. |
| D46 | Vendedor vê leads livres e os seus; contato em lead livre torna a pessoa responsável. | Evita dois vendedores abordando a mesma empresa e dá ao administrador o que cada um está fazendo. |
| D47 | Não contatar: qualquer pessoa inclui, só administrador libera; o superadmin tem bloqueios globais. | Um pedido de descadastro não pode esperar aprovação (LGPD); liberar é decisão de quem responde pela empresa. |
| D48 | Cadastro pela tela gera senha provisória e obriga a troca no primeiro acesso. | O administrador nunca precisa saber a senha definitiva da equipe. |
| D49 | O CRM guarda uma cópia dos leads do módulo 3, atualizada a cada 30 min, e lista a partir dela. | Lead manual, ordenação por próximo passo, exportação e relatórios precisam dos dados juntos. O CRM continua só lendo o módulo 3 e nada volta para ele (D35 mantida). |
| D50 | Lead manual pertence a uma empresa cliente; lead do agente é compartilhado e identificado pelo CNPJ. | Uma indicação de um cliente não deve aparecer para outro. A chave do lead manual é gerada (`M…`), porque o CNPJ é opcional. |
| D51 | Banco com migrações numeradas (`PRAGMA user_version`). | Atualizar o CRM não pode mais exigir apagar o banco. |
| D52 | Venda nunca é apagada; o administrador estorna com motivo. | Valor vendido é dado que alguém vai cobrar; um erro se corrige com estorno visível, não sumindo. |
| D53 | Auditoria em tabela própria, só de inclusão (gatilhos no banco), com cadeia de hashes. | Pedido do usuário: tudo auditado. Os gatilhos impedem mudança pela aplicação; a cadeia denuncia mudança feita direto no arquivo. |
| D54 | A auditoria é gravada por um middleware, depois da resposta, em transação separada. | Toda requisição gera registro, inclusive as negadas e as que dão erro, e uma falha na auditoria não perde a ação do usuário (o erro vai para o log do servidor). |
| D55 | Cliques que não passam pelo servidor são enviados de uma lista fechada de eventos. | Registra "copiou a mensagem" ou "abriu o WhatsApp" sem deixar a tela gravar qualquer texto na auditoria. |
| D56 | Datas de agenda e relatórios no fuso `America/Sao_Paulo` (`CRM_FUSO`); horários gravados em UTC. | "Hoje" e "semana" precisam bater com o relógio da equipe; UTC no banco evita ambiguidade. |
| D66 | O módulo 3 real nasce do simulado e usa o serviço de contatos do módulo 1 (`prospeccao.enrich`), com banco próprio. | A busca de contatos já existia, testada, no módulo 1; o módulo 3 só a aplica às empresas-alvo do agente. O contrato v1 e o CRM não mudam. O pacote do módulo 1 entra sem as dependências pesadas (Streamlit, pandas), que o enriquecimento não usa. |
| D67 | O módulo 3 relê o CSV do agente quando o arquivo muda, em vez de ler só ao subir. | A rotina do mês deixa de depender de reiniciar o contêiner; o agente já grava o CSV de uma vez (D63), então nunca se lê um arquivo pela metade. |
| D68 | Perguntas respondidas pelo próprio agente (`agente servir`), com servidor HTTP da biblioteca padrão e o mesmo cliente Gemini da classificação. | Sem dependência nova no agente e sem outro serviço de LLM. A minimização de dados da D61 vale também para as perguntas. O serviço fica num perfil do compose: sem chave do Gemini, o CRM continua no modo simulado em vez de dar erro. |
| D70 | HTTPS pelo Caddy, num perfil do compose; o nginx confia no `X-Forwarded-For` só vindo do endereço fixo do Caddy. | Certificado automático sem configuração. Confiar na faixa inteira deixaria forjar o IP da auditoria pela porta 8080, que chega pelo gateway da mesma rede. |
