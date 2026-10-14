# Registro de decisões

Formato: data · decisão · motivo. Decisões da seção 0 do escopo não são reabertas aqui.

## 2026-10-09 · Etapa 1

### D1. Endereço da fonte: compartilhamento Nextcloud da Receita
- O endereço antigo (`arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj`) foi substituído por um compartilhamento público Nextcloud:
  `https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK?dir=/Dados/Cadastros/CNPJ`, com uma pasta por mês (`AAAA-MM`).
- A listagem usa WebDAV do compartilhamento público (PROPFIND); o download usa GET com retomada.
- URL, token e caminho ficam no `.env` (`RECEITA_BASE_URL`, `RECEITA_SHARE_TOKEN`, `RECEITA_SHARE_PATH`). Se a Receita mudar o endereço, basta ajustar o `.env`.
- O site da Receita bloqueia leitura automatizada por robôs, então a listagem real não pôde ser conferida durante o desenvolvimento. O cliente falha com mensagem clara se a estrutura não bater.

### D2. Validação de layout por número de colunas e formato de campos-chave
- Os CSVs da Receita não têm linha de cabeçalho (`;`, aspas, Latin-1). Não há nomes de colunas para validar.
- Validação: (1) o mês precisa ter arquivos de todas as 10 tabelas; (2) as primeiras 1.000 linhas de cada arquivo precisam ter o número exato de colunas (Empresas 7, Estabelecimentos 30, Sócios 11, Simples 7, domínios 2) e campos-chave no formato esperado (CNPJ básico, datas `AAAAMMDD`, UF etc.); (3) a leitura completa no DuckDB roda em modo estrito, então qualquer linha com colunas a mais ou a menos interrompe a carga.

### D3. Código de município: somente o código da Receita
- A tabela de municípios da Receita usa código próprio (SIAFI/TOM), não IBGE. O escopo dizia o contrário.
- Decisão (confirmada pelo usuário): o campo `codigo_municipio_ibge` sai do modelo. O gold usa `codigo_municipio_receita`, `municipio` e `uf`.
- Se no futuro for preciso (mapa por município, CRM), basta carregar uma tabela de correspondência SIAFI→IBGE e fazer um join, sem recarregar a base.

### D4. CNPJ alfanumérico
- Desde julho de 2026 a Receita emite CNPJs alfanuméricos (12 posições `[0-9A-Z]` + 2 dígitos verificadores numéricos).
- Normalização, validação de layout e cálculo de DV aceitam esse formato. O CNPJ continua sendo texto de 14 caracteres com zeros à esquerda.

### D5. Silver por tabela; junção na gold
- A silver grava um Parquet tipado e deduplicado por tabela da Receita. A junção Empresas + Estabelecimentos + Simples + domínios acontece uma única vez, ao gerar a gold.
- Motivo: evita gravar duas vezes a maior tabela (dezenas de milhões de linhas) e mantém a silver reaproveitável.

### D6. Bancos separados para base e dados do usuário
- `data/gold/empresas.duckdb` é regerado a cada carga e publicado por troca atômica de arquivo. O histórico da tabela `carga` é copiado do banco anterior.
- Status de prospecção, contatos complementares e consultas salvas (Etapas 4 e 5) ficarão em um arquivo separado, ligado pelo CNPJ completo. Assim nenhuma carga mensal consegue apagá-los.
- Nome da tabela principal: `empresa` (seção 7 do escopo). A seção 6.1 a chamava de `empresas_prospeccao`.

### D7. Data de referência da flag `is_nova`
- `is_nova` é calculada na carga com a janela padrão (90 dias, `JANELA_NOVA_DIAS`) contra a data da carga. A data usada fica gravada em `carga.data_referencia_nova`.
- As consultas da Etapa 2 recalculam o intervalo dinamicamente; a flag serve só como atalho do padrão.

### D8. Contato original da Receita sem nenhuma transformação
- `ddd_receita_*`, `telefone_receita_*`, `fax_receita` e `email_receita` são copiados byte a byte da fonte (inclusive espaços e maiúsculas). O DDD fica em campo próprio. A formatação para exibição será feita só na interface.
- Exceção inevitável: campo vazio (`""`) vira `NULL`.

### D9. Porte
- `porte = 'MEI'` quando a tabela Simples indica opção pelo MEI. Caso contrário vale o porte da Receita: 01 = ME, 03 = EPP, 05 = DEMAIS, 00/vazio = NÃO INFORMADO. O código original fica em `porte_codigo`.

### D10. Sócios (LGPD, minimização)
- Gold guarda apenas `nome_socio`, `qualificacao`, `data_entrada` e `tipo_socio`. CPF/CNPJ do sócio, representante legal e faixa etária são descartados já na silver.

### D11. Disco
- Os `.zip` originais ficam na bronze. Os CSVs extraídos são apagados ao fim da carga (`APAGAR_CSV_EXTRAIDO=true`), porque podem ser regerados a partir dos zips.

## 2026-10-09 · Etapa 2

### D12. Carga em lotes ("baldes") por CNPJ básico
- No teste com 60 milhões de estabelecimentos, a junção e a ordenação da base inteira de uma vez usaram mais de 27 GB de disco temporário.
- Nova abordagem: a silver das tabelas grandes é gravada em 32 lotes por hash do CNPJ básico. A deduplicação e a junção da gold são feitas lote a lote, com cerca de 2 milhões de linhas por vez.
- Isso substitui a nota da D2 sobre leitura dupla: os registros lidos dos CSVs são contados na mesma passada que gera a silver.
- Proteções contra lotar o disco: `DUCKDB_MAX_TEMP` (teto do disco temporário do DuckDB; a carga falha com mensagem) e `ESPACO_MINIMO_GB` (a carga nem começa sem esse espaço livre; padrão 50 GB).

### D13. Sem índices ART; ordenação e zonemaps
- Dentro de cada lote, as linhas da gold são gravadas ordenadas por UF, município e data de abertura. O DuckDB guarda mínimo e máximo por bloco e pula blocos que não podem conter o resultado.
- Índices ART do DuckDB servem para busca pontual e ocupam muita memória em 60 milhões de linhas. As consultas medidas ficaram abaixo da meta sem eles (ver README).

### D14. Semântica dos filtros
- Critérios diferentes combinam com E. Valores do mesmo critério combinam com OU: duas localidades somam; código exato, prefixo e palavra-chave de CNAE somam.
- Textos (palavra-chave, bairro, descrição de CNAE, autocomplete) são comparados sem acento e sem diferenciar maiúsculas.
- CNAE secundário só entra quando pedido (`incluir_cnaes_secundarios`).
- "Simples = não" inclui empresas que nem constam na tabela do Simples.
- Período relativo ("últimos N dias") é recalculado a cada execução; datas fixas ficam fixas. Isso vale também para consultas salvas.

### D15. Docker
- Imagem `python:3.12-slim` com usuário sem privilégios; os dados ficam em `./data`, montado como volume.
- No Docker Desktop (Windows/Mac), a memória dos contêineres precisa ser maior que `DUCKDB_MEMORY_LIMIT`, com folga de cerca de 30%.

## 2026-10-09 · Etapa 3

### D16. Banco do usuário em SQLite (`data/app/app.sqlite`)
- Guarda login e, nas próximas etapas, status de prospecção, contatos complementares e consultas salvas, sempre ligados ao CNPJ completo.
- Motivo técnico: o DuckDB aceita só um processo com escrita por arquivo. A interface, a linha de comando (criar usuário) e a carga agendada precisam escrever ao mesmo tempo; o SQLite faz isso com travas transacionais, vem com o Python e não exige servidor.
- A base da Receita continua em DuckDB, como no escopo. A migração para PostgreSQL (fase 2) troca só a implementação do `AppStore`.
- Esquema versionado por migrações (`PRAGMA user_version`).
- O filtro "tem contato complementar" recebe a lista de CNPJs do banco do usuário, em vez de juntar os dois bancos; isso evita depender de extensões do DuckDB.

### D17. Login e acesso
- Senhas com scrypt (biblioteca padrão), sal aleatório e comparação em tempo constante. Mínimo de 8 caracteres.
- Atraso crescente a cada senha errada. Tempo de resposta igual para usuário inexistente.
- A sessão vale enquanto a aba do navegador estiver aberta (sem cookie persistente na fase 1).
- Usuários são criados pelo terminal (`prospeccao usuario criar NOME`), nunca por tela aberta.
- Por padrão a interface escuta só em `127.0.0.1`, inclusive no Docker. Para acesso pela rede é preciso um proxy com HTTPS na frente (seção 5 do escopo).

### D18. Interface e nova carga
- A interface mantém uma conexão somente leitura por processo e a reabre quando o arquivo da base muda. Depois de uma carga, a base nova aparece sem reiniciar nada.
- Exceção no Windows (fora do Docker): o arquivo aberto pela interface impede a troca. A carga tenta algumas vezes e depois explica o que fazer; o recomendado é rodar a carga com a interface fechada.

### D19. Seleção de município e CNAE
- O autocomplete usa a busca embutida dos seletores (digitar filtra a lista), com todos os municípios presentes na base e todos os CNAEs da tabela de domínio.
- Escolher UF e município ao mesmo tempo restringe ao município; UFs sem município escolhido valem inteiras.

## 2026-10-09 · Etapa 4

### D20. API de busca: Google em desligamento, Brave como alternativa, padrão nenhuma
- A Google Custom Search JSON API não aceita novos clientes e será desligada em 01/01/2027. Fica suportada só para quem já tem chave (`BUSCA_API_PROVEDOR=google`).
- A Brave Search API é a alternativa oficial suportada (`BUSCA_API_PROVEDOR=brave`). É paga por consulta e tem crédito mensal gratuito.
- O padrão é `nenhum`: ficam ativos só o site oficial e a busca manual assistida. Isso segue o ponto em aberto nº 4 (nenhuma API paga por padrão).

### D21. A API só indica o site provável; os contatos vêm do próprio site
- Da API guardamos apenas a URL do site provável e a URL do resultado (fonte). Não guardamos títulos nem trechos.
- Os contatos são lidos depois no próprio site, respeitando o robots.txt. Assim não dependemos dos termos de armazenamento de cada provedor.
- O usuário deve conferir os termos do provedor que contratar.

### D22. Leitura de site
- Lê só o domínio da empresa: página inicial e páginas de contato, no máximo 4 páginas e 2 MB por página.
- Respeita o robots.txt (RFC 9309): 4xx libera a leitura; 5xx ou site fora do ar bloqueia.
- Ignora redirecionamento para outro domínio. Mantém intervalo mínimo por site (`SITE_INTERVALO_SEGUNDOS`) e se identifica como robô no User-Agent.
- Extrai e-mails, telefones (inclusive links de WhatsApp) e links de redes sociais publicados no próprio site. Nunca acessa as redes sociais.

### D23. Regra de confiança
- **Alta:** site do domínio do e-mail da Receita em que o nome da empresa aparece; ou site indicado pela API com o mesmo domínio do e-mail da Receita.
- **Média:**
  - domínio do e-mail da Receita sem o nome da empresa no site (com observação: muitas vezes o e-mail cadastrado é do escritório de contabilidade);
  - site informado pelo usuário em que o nome aparece;
  - site indicado pela API com o nome no título ou no domínio.
- **Baixa:** site sem relação visível com o nome.
- **Registro manual:** o usuário escolhe a confiança (padrão: média).
- Contato igual ao da Receita não é duplicado como complementar.

### D24. Cache e limite
- A mesma empresa não é buscada de novo pelo mesmo método dentro de `CACHE_BUSCA_DIAS` (padrão 90), salvo com "buscar de novo".
- A API tem teto diário (`API_LIMITE_DIARIO`) e intervalo mínimo entre consultas.
- O lote é limitado a 100 empresas (uma página da tabela) e roda só quando o usuário pede.

## 2026-10-09 · Etapa 5

### D25. Status de prospecção
- O status fica no banco do usuário, ligado ao CNPJ completo, e por isso sobrevive a qualquer carga mensal (há teste para isso).
- Valores: não prospectada (padrão de quem não tem registro), em contato, contatada, sem interesse, descartada, cliente.
- Cada alteração vai para `status_historico`, com data e usuário.
- O filtro por status entra na consulta como lista de CNPJs (o mesmo mecanismo do contato complementar).

### D26. Consultas salvas e novidades
- A consulta guarda os filtros em JSON. Período relativo ("últimos N dias") é recalculado a cada execução sobre a base mais recente.
- Cada execução guarda os CNPJs retornados. "Novas" são as que não estavam na execução anterior.
- Mantemos só as duas últimas execuções por consulta, o necessário para comparar.
- "Mostrar só as novas" restringe tabela, indicadores e exportação a esses CNPJs.
- Executar uma consulta salva troca os filtros aplicados, mas não reposiciona os campos da barra lateral. O quadro "Filtros aplicados" mostra o que está valendo, e clicar em "Aplicar filtros" volta a usar a barra lateral.

### D27. Exportação
- **CSV:**
  - UTF-8 com BOM; separador configurável (padrão `;`), com decimal com vírgula quando o separador é `;`;
  - datas em DD/MM/AAAA;
  - CNPJ e CEP com máscara, para o Excel não apagar zeros à esquerda;
  - contato da Receita exatamente como na fonte.
- **XLSX:**
  - abas Empresas, Filtros (com dados da base, total e aviso LGPD) e Indicadores;
  - datas e valores com tipo real;
  - limite de 1.048.575 linhas do Excel, com aviso.
- **PDF:**
  - feito com ReportLab, que não exige bibliotecas de sistema (o WeasyPrint exigiria);
  - traz resumo, filtros, data da base, total, gráficos de CNAE, porte e mês de abertura, tabela dos top N (padrão 50, aberturas mais recentes) e aviso LGPD.
- **Contatos complementares e status** saem sempre em colunas separadas.
- **Limite pela interface:** até 500 mil empresas, porque o arquivo é montado na memória para download. Acima disso, use `prospeccao exportar`, que grava direto em disco.

## 2026-10-09 · Etapa 6

### D28. Agendamento: "se houver mês novo" em vez de data fixa
- A Receita publica por volta do meio do mês, sem data exata. Em vez de um dia fixo, o agendamento verifica todo dia (`AGENDADOR_INTERVALO_HORAS`) e só carrega se o mês publicado for mais novo que o da base.
- Isso resolve o ponto em aberto nº 1 (mensal fixa ou sob demanda): é mensal na prática e também pode ser rodado sob demanda.
- Três formas: serviço `agendador` no Docker, cron (`prospeccao carga --se-houver-novo`) ou Agendador de Tarefas do Windows.
- Uma trava do sistema operacional (`flock` no Linux/macOS, `msvcrt.locking` no Windows) sobre `data/.carga.lock` impede duas cargas simultâneas. O sistema solta a trava sozinho quando o processo termina, mesmo se morrer no meio. A primeira versão comparava o número do processo (PID) e falhava no Docker, onde cada contêiner tem um processo com PID 1; corrigido em 10/10/2026 após teste do usuário.
- Erro numa verificação é registrado no log e não derruba o agendador.

### D29. Limpeza de meses antigos
- Depois de cada carga, zips (bronze) e Parquet (silver) além dos `MESES_MANTIDOS` mais recentes (padrão 2) são apagados. A gold e o banco do usuário nunca são afetados.

### D30. FastAPI não usado na fase 1
- A interface Streamlit chama `consulta.py` e `domain/` diretamente, sem API intermediária. A pasta `api/` da seção 6.3 ("se usado") fica para a fase 2.
- A lógica já está separada da tela, então expor uma API depois não exige reescrever regras.

## 2026-10-10 · Correções após a primeira carga real

### D31. Codificação real dos arquivos: Windows-1252, convertida para UTF-8 na extração
- A primeira carga real falhou com "File is not latin-1 encoded" na tabela de estabelecimentos. Os arquivos trazem bytes 0x80–0x9F (aspas curvas, travessão), que só existem no Windows-1252, e o leitor do DuckDB recusa.
- Agora a bronze extrai cada CSV já convertido para UTF-8, em streaming (~260 MB/s, alguns minutos a mais na carga completa). Decodifica como Windows-1252; os 5 bytes que ele não define caem para Latin-1, então nenhum byte é perdido. O DuckDB passa a ler UTF-8.
- Os zips originais na bronze continuam intocados.
- `carga --sem-download` sem `--mes` agora usa o último mês já baixado.

### D32. Interface lia a base antiga depois de uma carga nova
- O DuckDB reaproveita o banco já aberto pelo mesmo caminho: mesmo depois de a carga trocar o arquivo, a interface continuava na base anterior até ser reiniciada.
- Agora a interface abre um banco em memória e anexa o arquivo da base (`ATTACH … READ_ONLY`). Cada versão do arquivo é aberta de novo e a conexão anterior é fechada. Basta apertar F5 depois de uma carga.

## 2026-10-11 · Melhorias pedidas após os testes

### D33. Login que sobrevive a recarregar a página
- Ao entrar, o navegador recebe um cookie com um código aleatório (`secrets.token_urlsafe(32)`); o banco do usuário guarda só o hash SHA-256 desse código e a validade (`SESSAO_HORAS`, padrão 12; `0` desliga).
- Cookie `SameSite=Strict`, e `Secure` quando a página está em HTTPS. Não é `HttpOnly`, porque o Streamlit só permite gravá-lo via JavaScript; o risco é baixo porque a interface não exibe HTML vindo de terceiros, e a sessão expira e pode ser revogada.
- **Sair** apaga a sessão no servidor e o cookie. **Trocar a senha** ou **remover o usuário** derrubam todas as sessões dele.

### D34. Bronze em paralelo e arquivos temporários fora da pasta compartilhada
- Cada zip é processado num processo Python separado (`python -m prospeccao.ingest.bronze_trabalho`), com até 8 ao mesmo tempo, os maiores primeiro (`BRONZE_PROCESSOS`, 0 = automático). Volumes pequenos, como a amostra, ficam num processo só, porque abrir processos custaria mais.
- Medido aqui com 2 núcleos: 6,6 s → 3,8 s (1,7x). Com mais núcleos, o ganho cresce.
- Processo independente em vez de `multiprocessing`, porque o modo "spawn" reimporta o script principal e quebra quando o processo principal é a interface ou os testes.
- A primeira carga real levou 19 min na bronze, ~10x mais lenta que aqui. O gargalo provável é gravar ~25 GB de CSV temporário na pasta compartilhada com o Docker. Agora esses arquivos e o disco auxiliar do DuckDB vão para `PROSPECCAO_TRABALHO_DIR`, que no Docker é um volume interno (`trabalho`), bem mais rápido. A pasta `data` fica só com zips, silver, gold e banco do usuário.
- A verificação de espaço livre olha as duas pastas quando ficam em discos diferentes.

## 2026-10-12 · Exportação para o agente no Colab

### D64. Parquet único de estabelecimentos ativos
*(Era a D35; renumerada ao juntar os módulos no mesmo repositório, porque o CRM usa de D35 a D56.)*
- A gold é um DuckDB de ~8 GB (73 milhões de estabelecimentos), pesado demais para o Colab. `prospeccao exportar-parquet` grava um único Parquet (ZSTD) só com os estabelecimentos ATIVOS (28 milhões, ~1,6 GB em 2026-09), com as colunas da exportação mais `cnaes_secundarios`. Não leva os sócios.
- O arquivo é ordenado por UF, município e data de abertura, para o DuckDB pular blocos nas consultas por localidade.
- É gravado num temporário e renomeado só no fim, para que uma falha (ex.: falta de memória) não deixe um arquivo truncado.

## 2026-10-13 · Agente classificador (Módulo 2)

O agente saiu deste módulo e virou o módulo 2, em `agente/` na raiz do repositório. As decisões dele estão em `agente/README.md` (D57 a D63).

## Pontos em aberto da seção 12 (opção mais simples)

| # | Ponto | Escolha |
|---|---|---|
| 1 | Frequência da carga | Mensal fixa via agendamento (Etapa 6), com execução manual possível a qualquer momento (`prospeccao carga`) |
| 2 | Identidade visual do PDF | Sem logo ou cores próprias na fase 1; layout neutro |
| 3 | Streamlit ou React na fase 2 | Streamlit na fase 1; a escolha da fase 2 fica para quando houver equipe |
| 4 | API de busca paga | Nenhuma API paga por padrão. Busca manual assistida e site oficial; API oficial opcional, ativada só com chave no `.env` |
