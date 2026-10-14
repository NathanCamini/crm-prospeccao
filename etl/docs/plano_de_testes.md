# Plano de testes – Prospecção de empresas novas

Roteiro para validar o projeto de ponta a ponta. Siga as fases em ordem. Cada teste tem um identificador (ex.: **B5**) para facilitar o retorno: basta dizer "B5 falhou" e colar a mensagem.

| Fase | O que valida | Base usada | Tempo estimado |
|---|---|---|---|
| A | Testes automatizados | nenhuma (gera a própria) | 5 min |
| B | Interface completa com dados conhecidos | fictícia (`amostra`) | 40 min |
| C | Download e carga da base real | real (Receita) | 1 a 3 h (a maior parte é download) |
| D | Contatos complementares com empresas reais | real | 20 min |
| E | Exportação com dados reais | real | 15 min |
| F | Operação: agendador, trava, nova carga mensal | real | 15 min (+ próximo mês) |
| G | Segurança e LGPD | qualquer | 10 min |
| H | Docker (se for usar Docker) | fictícia e real | 30 min |

---

## 1. Pré-requisitos

| Item | Mínimo | Recomendado |
|---|---|---|
| Python (sem Docker) | 3.11 | 3.12 |
| Docker (opcional) | Docker Engine com Compose v2 | Docker Desktop atualizado |
| Memória RAM | 8 GB | 16 GB |
| Disco livre (base real) | 50 GB | 80 GB |
| Internet | acesso a `arquivos.receitafederal.gov.br` e ao PyPI | — |

As fases A e B não precisam de internet (só para instalar os pacotes) nem de muito disco.

---

## 2. Instalação

### 2.1 Sem Docker (Linux, macOS ou Windows)

Descompacte o projeto, abra um terminal na pasta `prospeccao-empresas` e rode:

```bash
# Linux/macOS
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

```bat
:: Windows (Prompt de Comando)
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

Confira com `prospeccao --help`; deve listar os comandos `carga`, `meses`, `amostra`, `benchmark`, `usuario`, `exportar`, `agendador` e `web`.

> Em todo terminal novo, ative o ambiente antes: `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`).

### 2.2 Com Docker

```bash
cp .env.example .env          # Windows: copy .env.example .env
docker compose build
```

No Docker Desktop (Windows/Mac): **Settings → Resources → Memory**. Reserve pelo menos 30% a mais que o `DUCKDB_MEMORY_LIMIT` do `.env`; por exemplo, 6 GB para `DUCKDB_MEMORY_LIMIT=4GB`.

---

## 3. Configuração do `.env`

O `.env` fica na raiz do projeto e **nunca** vai para o git (já está no `.gitignore`). O `.env.example` traz todos os campos comentados. Para os testes, só os campos de memória e threads precisam de ajuste.

### 3.1 Todas as variáveis

| Variável | Padrão | O que faz | Quando mudar |
|---|---|---|---|
| `PROSPECCAO_DATA_DIR` | `./data` | Pasta de bronze, silver, gold, banco do usuário e temporários | Para usar outro disco com mais espaço. No Docker é sempre `/app/data`, mapeada para `./data` |
| `RECEITA_BASE_URL` | `https://arquivos.receitafederal.gov.br` | Servidor da Receita | Só se a Receita mudar o endereço |
| `RECEITA_SHARE_TOKEN` | `gn672Ad4CF8N6TK` | Token do compartilhamento público (Nextcloud) da Receita | Só se o link público mudar |
| `RECEITA_SHARE_PATH` | `/Dados/Cadastros/CNPJ` | Pasta dentro do compartilhamento | Só se a Receita reorganizar as pastas |
| `DUCKDB_MEMORY_LIMIT` | `4GB` | Memória máxima do motor de dados | **Ajuste à sua RAM** (tabela 3.2) |
| `DUCKDB_THREADS` | `4` | Núcleos usados | **Use o número de núcleos** da máquina |
| `DUCKDB_MAX_TEMP` | `20GB` | Teto de disco temporário; se estourar, a carga para com mensagem em vez de lotar o disco | Aumente se a carga falhar por esse limite e houver disco |
| `ESPACO_MINIMO_GB` | `50` | A carga real nem começa com menos espaço livre | Diminua só se souber o que está fazendo |
| `APAGAR_CSV_EXTRAIDO` | `true` | Apaga os CSVs extraídos (~25 GB) no fim; os zips ficam | `false` só para depuração |
| `MANTER_ARQUIVOS_CARGA` | `false` | Zips e silver apagados depois de cada carga bem-sucedida | `true` mantém os do mês carregado |
| `JANELA_NOVA_DIAS` | `90` | Janela da marcação `is_nova` gravada na base | Raramente; a interface escolhe o período livremente |
| `CACHE_BUSCA_DIAS` | `90` | Não repete busca de contato da mesma empresa nesse período | Conforme o uso |
| `SITE_INTERVALO_SEGUNDOS` | `2` | Intervalo mínimo entre acessos ao mesmo site | Não diminua (boa conduta) |
| `BUSCA_API_PROVEDOR` | `nenhum` | API de busca: `nenhum`, `google` ou `brave` | Só se contratar uma API |
| `GOOGLE_CSE_CHAVE`, `GOOGLE_CSE_CX` | vazio | Chave e ID do Google Custom Search | Só para quem já tem chave; o Google desliga a API em 01/01/2027 |
| `BRAVE_SEARCH_CHAVE` | vazio | Chave da Brave Search API | Se `BUSCA_API_PROVEDOR=brave` |
| `API_LIMITE_DIARIO` | `100` | Teto de consultas à API por dia | Conforme o orçamento |
| `API_INTERVALO_SEGUNDOS` | `1` | Intervalo entre consultas à API | Conforme o plano contratado |
| `AGENDADOR_INTERVALO_HORAS` | `24` | Frequência da verificação de mês novo | Raramente |
| `WEB_ENDERECO` | `127.0.0.1` | Endereço da interface (fora do Docker) | `0.0.0.0` só atrás de proxy com HTTPS |
| `WEB_PORTA` | `8501` | Porta da interface (no Docker, a porta do seu computador) | Se a 8501 estiver ocupada |

Variáveis do terminal, fora do `.env`, só para Docker em Linux: `UID` e `GID`. Use quando o seu usuário não for o 1000, por exemplo `UID=$(id -u) GID=$(id -g) docker compose ...`.

### 3.2 Memória e threads sugeridas

| RAM da máquina | `DUCKDB_MEMORY_LIMIT` | `DUCKDB_THREADS` | Docker Desktop (memória) |
|---|---|---|---|
| 8 GB | `4GB` | 4 (ou nº de núcleos) | 6 GB |
| 16 GB | `8GB` | nº de núcleos | 11 GB |
| 32 GB ou mais | `16GB` | nº de núcleos | 21 GB |

---

## 4. Usuários da interface

A senha deve ter pelo menos 8 caracteres e é guardada só como hash (scrypt). O nome é gravado em minúsculas.

| Ação | Sem Docker | Com Docker |
|---|---|---|
| Criar | `prospeccao usuario criar ana` | `docker compose run --rm prospeccao usuario criar ana` |
| Listar | `prospeccao usuario listar` | `docker compose run --rm prospeccao usuario listar` |
| Trocar senha | `prospeccao usuario senha ana` | `docker compose run --rm prospeccao usuario senha ana` |
| Remover | `prospeccao usuario remover ana` | `docker compose run --rm prospeccao usuario remover ana` |

O comando pede a senha duas vezes, sem mostrar o que você digita. Em scripts, a senha pode vir pela entrada padrão: `echo "minha-senha-123" | prospeccao usuario criar ana`.

---

## Fase A – Testes automatizados

| ID | Passo | Resultado esperado |
|---|---|---|
| A1 | `pytest -o addopts="" -q` (Docker: `docker compose run --rm testes`) | **180 passed**, sem falhas |
| A2 | Se algo falhar: `pytest -o addopts="" -q -x` | Para no primeiro erro; envie a saída completa |

Os testes cobrem:
- normalização de CNPJ, inclusive alfanumérico;
- filtro de data com limites inclusivos;
- resolução de município e marcação de empresa nova;
- preservação do contato original da Receita;
- validação de layout, filtros, interface, contatos complementares, status, consultas salvas, exportação e operação.

---

## Fase B – Interface com a base fictícia

A base fictícia tem 7 estabelecimentos em municípios **inventados**, criados para cada teste ter um resultado conhecido. As datas são relativas ao dia em que você rodar o comando.

| Empresa (fantasia) | CNPJ | Município | Abertura | Destaques |
|---|---|---|---|---|
| PADARIA SÃO JOÃO | 44.555.666/0001-81 | SÃO JOÃO DO TESTE/RS, bairro VILA NOVA | hoje | MEI; telefone com espaço no início; e-mail com maiúsculas |
| TECH BETA | A1.B2C.3D4/0001-93 | VILA BETA/SC | há 5 dias | CNPJ alfanumérico; porte não informado; não optante do Simples |
| ALFA OBRAS | 00.012.345/0001-65 | CIDADE ALFA/RS, bairro CENTRO | há 10 dias | Matriz; 2 sócios; CNAEs secundários |
| ALFA OBRAS FILIAL | 00.012.345/0002-46 | SÃO JOÃO DO TESTE/RS, sem bairro | há exatamente 90 dias | Filial; sem e-mail |
| (sem fantasia) | 11.222.333/0001-81 | CIDADE ALFA/RS | há 91 dias | Fora da janela de 90 dias |
| ENCERRADA | 77.888.999/0001-81 | CIDADE ALFA/RS | há 30 dias | Situação BAIXADA; sem contato |
| FUTURA | 55.666.777/0001-81 | VILA BETA/SC | daqui a 3 dias | Data inconsistente; nunca entra como "nova" |

### B.1 Preparar

| ID | Passo | Resultado esperado |
|---|---|---|
| B1 | `prospeccao amostra` | Log com `camada=bronze`, `camada=silver` e `camada=gold`; a linha `camada=silver tabela=estabelecimentos entrada=8 saida=7` mostra 1 duplicata removida. Termina com `Carga 2026-09 concluída` |
| B2 | `prospeccao web` **antes** de criar usuário; abra http://localhost:8501 | Aviso "Nenhum usuário cadastrado", com o comando para criar |
| B3 | Em outro terminal: `prospeccao usuario criar ana` (senha `senha-teste-123`) | `Usuário 'ana' criado.` |
| B4 | Recarregue a página; entre com senha errada | "Usuário ou senha inválidos." e espera de 1 s; a segunda tentativa espera 2 s |
| B5 | Entre com a senha certa | Tela principal com **4 empresas encontradas**; no topo, "Base: referência 09/2026, carregada em …" |

### B.2 Filtros (clique em **Aplicar filtros** depois de cada mudança; **Limpar** volta ao padrão)

| ID | Passo | Resultado esperado |
|---|---|---|
| B6 | Município: digite "sao jo" e escolha SÃO JOÃO DO TESTE/RS | Aparece o campo "Bairros em SÃO JOÃO DO TESTE" com a opção VILA NOVA |
| B7 | Aplicar sem escolher bairro | **2** empresas (PADARIA e ALFA OBRAS FILIAL) |
| B8 | Escolha o bairro VILA NOVA e aplique | **1** empresa (PADARIA) |
| B9 | Limpar. UF = SC | **1** empresa (TECH BETA); a FUTURA não entra por ter data futura |
| B10 | Limpar. Período "Qualquer data" e Situação cadastral **vazia** (remova ATIVA) | **7** empresas |
| B11 | Limpar. Período "Datas personalizadas", de **90 dias atrás** até **90 dias atrás** (o mesmo dia) | **1** empresa (ALFA OBRAS FILIAL): o limite é inclusivo |
| B12 | Mesmo teste com **91 dias atrás** até 91 dias atrás, Situação vazia | **1** empresa (a sem fantasia, 11.222.333) |
| B13 | Limpar. "Códigos ou prefixos" = `43` | **2** (ALFA OBRAS e FILIAL) |
| B14 | Limpar. "Palavra na descrição do CNAE" = `padaria` | **1** (PADARIA) |
| B15 | Limpar. Códigos = `4330404` | **0**. Marque "Incluir CNAEs secundários" e aplique: **1** (ALFA OBRAS) |
| B16 | Limpar. Porte = MEI | **1** (PADARIA) |
| B17 | Limpar. Optante pelo Simples = Não | **1** (TECH BETA) |
| B18 | Limpar. Tem e-mail (Receita) = Não | **1** (ALFA OBRAS FILIAL) |
| B19 | Limpar. Palavra no nome = `sao joao` | **1** (PADARIA): sem acento também funciona |
| B20 | Limpar. Capital mín. = 100000 | **2** (ALFA OBRAS e FILIAL) |
| B21 | Limpar. Matriz ou filial = Filial | **1** |
| B22 | Abra "Filtros aplicados" no topo | Lista legível do que está valendo |

### B.3 Tabela, indicadores e ficha

| ID | Passo | Resultado esperado |
|---|---|---|
| B23 | Aba **Indicadores** (filtros padrão) | Total 4. Top CNAE: 4399103 com 2. Porte: ME 2, MEI 1, NÃO INFORMADO 1. Gráfico por mês e gráfico por município (3 municípios) |
| B24 | Filtre só SÃO JOÃO DO TESTE e veja Indicadores | Sem gráfico por município (só um município) |
| B25 | Aba Empresas: mude "Ordenar por" para Razão social, desligue "Decrescente" | ALFA OBRAS… primeiro |
| B26 | Em "Colunas", adicione Capital social e retire Bairro | A tabela muda na hora |
| B27 | Clique na linha da PADARIA | A ficha abre abaixo da tabela |
| B28 | No quadro **Contato original da Receita Federal** | DDD 53, Telefone `99999000` (na fonte há um espaço antes; a tela não mostra, mas o dado exportado mantém), e-mail `Joao.Silva@Exemplo.com` com as maiúsculas originais |
| B29 | "Abrir ficha pelo CNPJ": digite `00.012.345/0001-65` | Ficha da ALFA OBRAS: 2 sócios (sem CPF), CNAEs secundários 4399199 e 4330404, "Outros estabelecimentos da mesma empresa (1)" |
| B30 | Digite `abc!` no mesmo campo | "CNPJ inválido." |
| B31 | Digite `A1B2C3D4000193` | Ficha da TECH BETA (CNPJ alfanumérico) |

### B.4 Contatos complementares (base fictícia)

| ID | Passo | Resultado esperado |
|---|---|---|
| B32 | Ficha da ALFA OBRAS → **Ler site oficial** | Mensagem "site bloqueado ou fora do ar": o domínio é fictício. Valida o tratamento de erro |
| B33 | Passe o mouse sobre "Ler site oficial" na ficha da ALFA OBRAS | A dica mostra o domínio que será lido (alfa-obras.com.br). O caso de e-mail genérico (gmail, hotmail), em que o botão fica desabilitado, é testado no D1 com a base real |
| B34 | "Busca manual assistida" → clique num link | Abre o Google no navegador com a pesquisa pronta (a ferramenta não acessa o Google) |
| B35 | No formulário: Tipo Telefone, Valor `(53) 3999-1234`, URL `https://exemplo.com`, Confiança Média → **Registrar contato** | "Contato registrado." A tabela de contatos complementares mostra tipo, valor, confiança Média, fonte "busca manual", URL e data de hoje |
| B36 | Registre de novo `53 3999 1234` | "Esse contato já estava registrado." |
| B37 | Registre o telefone `123` | Erro "Telefone inválido: informe DDD + número" |
| B38 | Confira que o quadro **Contato original da Receita** não mudou | Igual ao B28 |
| B39 | Filtro "Tem contato complementar" = Sim | Só a empresa em que você registrou |
| B40 | "Remover um contato complementar" → Remover | O contato some |
| B41 | Aba Empresas → "Buscar contatos complementares das empresas desta página" → Site oficial → Iniciar | Barra de progresso; resumo com contagens como "sem domínio próprio" e "site bloqueado ou fora do ar" (domínios fictícios) |
| B42 | Repita a busca em lote | Agora aparece "já buscado recentemente" (cache de 90 dias) para as que tinham domínio |

### B.5 Acompanhamento, consultas salvas e exportação

| ID | Passo | Resultado esperado |
|---|---|---|
| B43 | Ficha da PADARIA → Status "Em contato", Observação "Ligar terça" → **Salvar status** | "Status salvo: Em contato."; a coluna Status da tabela mostra "Em contato" |
| B44 | Mude para "Cliente" e abra "Histórico" | 2 linhas, com data e usuário |
| B45 | Filtro "Status de prospecção" = Cliente | **1**. Com "Não prospectada": **3** |
| B46 | Limpar. Barra lateral → "Consultas salvas" → nome "Teste 90 dias" → **Salvar consulta** | "Consulta salva." |
| B47 | Escolha a consulta → **Executar** | Faixa azul: "primeira execução, 4 empresas registradas" |
| B48 | Executar de novo | "**0** empresa(s) nova(s) desde a última execução" e o botão "Mostrar só as novas" |
| B49 | "Exportar resultado" → CSV → Gerar arquivo → Baixar; abra no **Excel** com duplo clique | Colunas separadas e acentos corretos (SÃO JOÃO); CNPJ `00.012.345/0001-65` com zeros; colunas "DDD 1 (Receita)", "Telefone 1 (Receita)", "E-mail (Receita)"; "Status da prospecção"; colunas de contatos complementares separadas |
| B50 | Exportar Excel (.xlsx) | Abas **Empresas**, **Filtros** (mês da base, data da carga, total, filtros, aviso LGPD) e **Indicadores** |
| B51 | Exportar PDF | Título; resumo com mês da base, data da carga e total; filtros aplicados; gráficos (CNAE, porte, mês); tabela "Top N prospects"; aviso de descadastro (LGPD); número de página |
| B52 | Terminal: `prospeccao exportar --consulta "Teste 90 dias" --formato xlsx` | "4 empresas exportadas em data/exportacoes/…" |
| B53 | **Sair** | Volta para a tela de login |

### B.6 Paginação e lote maiores (opcional)

| ID | Passo | Resultado esperado |
|---|---|---|
| B54 | `prospeccao amostra --volume 5000` (recria a base fictícia; status e contatos ficam) | Base com ~5 mil empresas sintéticas |
| B55 | Período "Qualquer data", Por página 25; Próxima/Anterior | Páginas navegáveis; "Página X de Y" correto |
| B56 | Status salvo no B43 continua na PADARIA | Sim: o status sobrevive à nova carga |

---

## Fase C – Base real da Receita

> Feche a interface antes no **Windows sem Docker** (o Windows não deixa substituir a base aberta). No Linux, macOS e Docker ela pode ficar aberta.

| ID | Passo | Resultado esperado |
|---|---|---|
| C1 | `prospeccao meses` | Lista de meses `AAAA-MM`. Se der erro, a mensagem cita `RECEITA_SHARE_TOKEN` e o endereço: **pare e me envie a mensagem** (a Receita pode ter mudado o compartilhamento) |
| C2 | `prospeccao carga` | Downloads com retomada, depois bronze, silver (32 lotes) e gold. Pode levar de 1 a 3 h; o download é a parte mais lenta, porque o servidor da Receita é lento |
| C3 | Acompanhe o log | Para cada tabela, `camada=bronze` (arquivos e linhas lidas), `camada=silver` (lidas, gravadas; diferença = duplicatas) e `camada=gold tabela=empresa entrada=N saida=N` (os dois números **iguais**) |
| C4 | No fim, o resumo JSON | `metricas` com `segundos` e `pico_memoria_mb` por etapa: **envie esses números** |
| C5 | Se a carga parar com "Layout inválido em …, linha …" | É o comportamento esperado se a Receita mudou o formato: **me envie a mensagem completa** |
| C6 | `prospeccao carga --se-houver-novo` | "Base já atualizada; nada a fazer." |
| C7 | `prospeccao web` → filtre **sua cidade**, últimos 90 dias | Resultado em até ~3 s; a data da base no topo é a do mês carregado |
| C8 | Procure uma empresa que você conhece (pelo CNPJ) e compare a ficha com o Comprovante de Inscrição no site da Receita | Razão social, endereço, situação, CNAE e contato iguais aos da Receita (do mês de referência) |
| C9 | Escolha uma cidade grande | O filtro de bairro aparece com a lista de bairros |
| C10 | Teste os filtros B13 a B21 com valores reais | Respostas coerentes e rápidas |
| C11 | (Opcional) `prospeccao benchmark` (exige ~15 GB livres; demora ~15 min) | Tabela de tempos: **envie a saída**. Meta: menos de 3 s por consulta |

Para conferir as métricas gravadas a qualquer momento:

```bash
python -c "import duckdb; c=duckdb.connect('data/gold/empresas.duckdb', read_only=True); print(c.sql('select mes_referencia, data_ingestao, metricas from carga'))"
```

---

## Fase D – Contatos complementares com empresas reais

| ID | Passo | Resultado esperado |
|---|---|---|
| D1 | Filtre sua cidade, "Tem e-mail (Receita)" = Sim; abra empresas com e-mail de domínio próprio e uma com gmail/hotmail | Domínio próprio: "Ler site oficial" habilitado, mostrando o domínio. Gmail/hotmail: botão desabilitado com explicação |
| D2 | **Ler site oficial** em 5 empresas | Para cada uma: "contatos encontrados", "nada novo" ou "site bloqueado ou fora do ar". Os contatos têm fonte (URL da página), data e confiança |
| D3 | Confira a confiança | **Alta** quando o nome da empresa aparece no site do domínio do e-mail. **Média** com a observação "pode ser do contador" quando o site é de outra empresa, o que é comum com e-mail de escritório contábil |
| D4 | "Ler outro site": informe o site de uma empresa que você conhece | Contatos com confiança média ou baixa, conforme o nome apareça ou não |
| D5 | Busca em lote (site oficial) numa página de 50 empresas | Termina; o resumo mostra as contagens. Espere ~2 s por site (intervalo de cortesia) |
| D6 | (Só com API contratada) `.env`: `BUSCA_API_PROVEDOR=brave` e `BRAVE_SEARCH_CHAVE=...`; reinicie a interface; "Indicar site pela API de busca" | Grava **só** o site provável (tipo Site), com a URL da fonte; depois use "Ler outro site" nele |
| D7 | Com `API_LIMITE_DIARIO=2`, faça 3 buscas pela API | A terceira diz "limite diário da API" |

---

## Fase E – Exportação com dados reais

| ID | Passo | Resultado esperado |
|---|---|---|
| E1 | Sua cidade, 90 dias → CSV → abrir no Excel | Acentos corretos; CNPJ com máscara e zeros; CEP com hífen; datas DD/MM/AAAA; capital com vírgula decimal |
| E2 | Mesmo resultado em Excel (.xlsx) | 3 abas; datas reconhecidas como data pelo Excel (dá para ordenar) |
| E3 | PDF com top 100 | Gráficos legíveis; tabela com 100 linhas em várias páginas, com cabeçalho repetido |
| E4 | Resultado acima de 500 mil empresas (ex.: Brasil, 365 dias) em CSV pela tela | Aviso para usar o terminal |
| E5 | Salve essa consulta e rode `prospeccao exportar --consulta "NOME" --formato csv` | Arquivo grande gerado em `data/exportacoes/` sem estourar memória |

---

## Fase F – Operação

| ID | Passo | Resultado esperado |
|---|---|---|
| F1 | `prospeccao agendador --uma-vez` | "Base já está no mês mais recente publicado (…). Nada a fazer." |
| F2 | Rode `prospeccao carga --mes <mês atual> --sem-download` e, enquanto roda, o mesmo comando em outro terminal | O segundo para com "Já existe uma carga em andamento (processo …)" |
| F3 | Configure o agendamento: Docker (`docker compose --profile agendador up -d agendador`), cron ou Agendador do Windows (exemplos no README) | O serviço ou tarefa aparece ativo; o log mostra "Agendador iniciado" |
| F4 | **Quando a Receita publicar o próximo mês** (por volta do dia 15) | O agendador carrega sozinho; a data da base no topo da interface muda |
| F5 | Depois da nova carga: status e contatos complementares | Continuam iguais |
| F6 | Depois da nova carga: execute a consulta salva | Mostra "N empresa(s) nova(s) desde a última execução"; "Mostrar só as novas" lista só essas |
| F7 | Depois da terceira carga mensal | Em `data/bronze` e `data/silver` ficam só os 2 meses mais recentes |

---

## Fase G – Segurança e LGPD

| ID | Passo | Resultado esperado |
|---|---|---|
| G1 | `git status` (se versionar) | `.env` e `data/` **não** aparecem |
| G2 | `python -c "import sqlite3; print(sqlite3.connect('data/app/app.sqlite').execute('select nome, senha_hash from usuario').fetchall())"` | A senha aparece só como `scrypt$…`, nunca em texto |
| G3 | De outro computador ou celular na mesma rede, tente abrir `http://IP-DA-MAQUINA:8501` | **Não** abre (a interface escuta só no próprio computador) |
| G4 | Ficha de qualquer empresa → Sócios | Só nome, qualificação, data de entrada e tipo; sem CPF |
| G5 | PDF e aba Filtros do Excel | Aviso de finalidade e de canal de descadastro (opt-out) |
| G6 | Observe os logs da carga e da interface | Nenhuma chave de API aparece |

---

## Fase H – Docker

| ID | Passo | Resultado esperado |
|---|---|---|
| H1 | `docker compose build` | Imagem `prospeccao:latest` criada |
| H2 | `docker compose run --rm testes` | Testes sem falhas |
| H3 | `docker compose run --rm prospeccao amostra` | Base fictícia em `./data` no seu computador |
| H4 | `docker compose run --rm prospeccao usuario criar ana` | Pede a senha no terminal |
| H5 | `docker compose up -d web` e depois `docker compose ps` | `web` com status **healthy** após ~30 s; http://localhost:8501 abre |
| H6 | Repita a Fase B pela interface do Docker | Mesmos resultados |
| H7 | `docker compose run --rm prospeccao carga` | Igual à Fase C (os dados ficam em `./data`) |
| H8 | Linux: erro "Permission denied" em `./data` | Rode com `UID=$(id -u) GID=$(id -g) docker compose …` |
| H9 | `docker compose --profile agendador up -d agendador` e `docker compose logs -f agendador` | "Agendador iniciado: verificação a cada 24.0 h" |
| H10 | `docker compose down` e `docker compose up -d web` | Usuários, status, contatos e base continuam (estão em `./data`) |

---

## Fase I – Agente classificador (Módulo 2)

O agente saiu deste módulo: testes e roteiro em `agente/README.md`, na raiz do repositório.

---

## 5. Critérios de aceite do escopo (seção 10) → testes

| Critério | Testes |
|---|---|
| A carga completa de um mês termina sem erro e registra as contagens por etapa | C2, C3, C4 (e A1) |
| Município + 90 dias retorna só empresas no intervalo e no município | B7, B11, B12, C7 |
| O contato original da Receita aparece sempre na ficha, sem alteração | B28, B38, C8 |
| Contatos complementares em campos separados, com fonte e data | B35, D2, B49 |
| O filtro de bairro só aparece quando há bairro | B6, C9 |
| CSV abre no Excel com acentuação correta | B49, E1 |
| PDF inclui filtros, data da base e total | B51, E3 |
| Status de prospecção sobrevive a uma nova carga | B56, F5 |
| Testes automatizados cobrem CNPJ, data, município e contato original | A1 |

---

## 6. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `prospeccao: command not found` | Ambiente virtual não ativado | `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`) |
| "Não foi possível listar o compartilhamento da Receita…" | Receita fora do ar ou endereço mudou | Tente mais tarde; se continuar, envie a mensagem |
| "Layout inválido em …" | A Receita mudou o formato dos arquivos | Envie a mensagem completa (arquivo e linha) |
| "Espaço livre … precisa de pelo menos 50 GB" | Pouco disco | Libere espaço ou aponte `PROSPECCAO_DATA_DIR` para outro disco |
| Carga morre sem mensagem ou o computador trava | Memória insuficiente | Reduza `DUCKDB_MEMORY_LIMIT` e `DUCKDB_THREADS` (tabela 3.2); no Docker Desktop, aumente a memória |
| "Out of Memory" ou "max_temp_directory_size" | Limite de memória ou de disco temporário | Aumente `DUCKDB_MAX_TEMP` (se houver disco) ou reduza `DUCKDB_THREADS` |
| "Não foi possível substituir … o arquivo está em uso" (Windows) | Interface aberta durante a carga | Feche a interface e renomeie `empresas.novo.duckdb` para `empresas.duckdb`, ou rode a carga de novo |
| "Já existe uma carga em andamento" | Outra carga rodando | Aguarde; se nenhuma estiver rodando, a trava órfã é descartada sozinha na próxima tentativa |
| Porta 8501 ocupada | Outro programa usa a porta | `prospeccao web --porta 8502` (Docker: `WEB_PORTA=8502` no `.env`) |
| Esqueci a senha | — | `prospeccao usuario senha NOME` |
| "Ler site oficial" sempre "bloqueado" | Site fora do ar, robots.txt proíbe ou rede bloqueia | Normal para parte dos sites; use a busca manual assistida |
| Interface não mostra a base nova depois da carga | Cache do navegador | Recarregue a página (F5) |

---

## 7. Como me enviar o resultado

Para cada fase, mande uma lista curta:

```
Fase B: OK, exceto
- B11: mostrou 0 em vez de 1. Print anexo.
- B49: no Excel, a coluna CNPJ ficou ... 
Fase C:
- C2: terminou em 1h40. Métricas (C4): bronze ... s / ... MB; silver ...; gold ...
- C7: consulta da minha cidade levou ~2 s.
```

Ajuda muito anexar:
- a **mensagem de erro completa** (o texto, não só o print);
- o **resumo JSON** do fim da carga (C4);
- a saída de `pytest -o addopts="" -q` se algum teste automatizado falhar;
- o sistema operacional, a RAM, e se usou Docker ou não.
