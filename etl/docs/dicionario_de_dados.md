# Dicionário de dados

Dois bancos:

| Banco | Arquivo | Conteúdo | Ciclo de vida |
|---|---|---|---|
| Base (DuckDB) | `data/gold/empresas.duckdb` | Dados da Receita | Regerado a cada carga mensal (troca atômica) |
| Usuário (SQLite) | `data/app/app.sqlite` | Login, status, contatos complementares, consultas salvas | Permanente; ligado ao CNPJ completo |

Camadas intermediárias:
- `data/bronze/AAAA-MM/`: zips originais da Receita e `manifest.json` (tamanho, SHA-256, arquivo interno).
- `data/silver/AAAA-MM/`: Parquet UTF-8 por tabela. As grandes (empresas, estabelecimentos, sócios, simples) ficam em `balde=0..31`.

---

## Base (`empresas.duckdb`)

### empresa — uma linha por estabelecimento

| Coluna | Tipo | Origem / regra |
|---|---|---|
| cnpj | VARCHAR(14) | básico + ordem + DV, com zeros à esquerda; pode ser alfanumérico (desde 07/2026) |
| cnpj_basico, cnpj_ordem, cnpj_dv | VARCHAR | partes do CNPJ |
| razao_social | VARCHAR | Empresas |
| nome_fantasia | VARCHAR | Estabelecimentos |
| situacao_cadastral_codigo | VARCHAR | 01, 02, 03, 04 ou 08 |
| situacao_cadastral | VARCHAR | NULA, ATIVA, SUSPENSA, INAPTA ou BAIXADA |
| data_situacao | DATE | data da situação cadastral |
| motivo_situacao | VARCHAR | tabela Motivos |
| data_inicio_atividade | DATE | data de abertura; em mudança de sede ou município pode ser anterior ao cadastro atual |
| cnae_principal | VARCHAR(7) | código CNAE |
| cnae_principal_descricao | VARCHAR | tabela Cnaes |
| cnaes_secundarios | VARCHAR[] | lista de códigos |
| natureza_juridica_codigo / natureza_juridica | VARCHAR | tabela Naturezas |
| porte_codigo | VARCHAR | código da Receita: 00, 01, 03 ou 05 |
| porte | VARCHAR | MEI (pela tabela Simples), ME, EPP, DEMAIS ou NÃO INFORMADO |
| capital_social | DECIMAL(18,2) | Empresas |
| opcao_simples, opcao_mei | BOOLEAN | Simples; NULL quando a empresa não consta |
| matriz_filial | VARCHAR | MATRIZ ou FILIAL |
| tipo_logradouro, logradouro, numero, complemento, cep | VARCHAR | Estabelecimentos |
| bairro | VARCHAR | NULL quando ausente na fonte |
| uf | VARCHAR(2) | EX para exterior |
| codigo_municipio_receita | VARCHAR(4) | código SIAFI/TOM usado pela Receita (não é o código IBGE; ver decisão D3) |
| municipio | VARCHAR | tabela Municipios |
| pais | VARCHAR | BRASIL ou país do exterior |
| ddd_receita_1, telefone_receita_1, ddd_receita_2, telefone_receita_2, ddd_fax_receita, fax_receita, email_receita | VARCHAR | **Contato original da Receita, copiado sem nenhuma alteração** |
| mes_referencia_base | VARCHAR | AAAA-MM da pasta da Receita |
| data_ingestao | TIMESTAMP | momento da carga |
| is_nova | BOOLEAN | início de atividade em [data de referência − janela, data de referência], inclusivo |

Ordem física: dentro de cada lote, por `uf`, `codigo_municipio_receita` e `data_inicio_atividade`. Isso permite ao DuckDB pular blocos nas consultas por localidade.

### socio
| Coluna | Descrição |
|---|---|
| cnpj_basico | liga à empresa |
| nome_socio | nome |
| qualificacao | descrição (tabela Qualificações) |
| data_entrada | data de entrada na sociedade |
| tipo_socio | PESSOA FÍSICA, PESSOA JURÍDICA ou ESTRANGEIRO |

Sem CPF/CNPJ do sócio, representante legal ou faixa etária (minimização, LGPD).

### municipio
`uf`, `codigo_municipio_receita`, `municipio`, `estabelecimentos`, `tem_bairro`. Usado no seletor de município; `tem_bairro` decide se o filtro de bairro aparece.

### cnae, natureza_juridica
Domínios (`codigo`, `descricao`).

### carga
`mes_referencia`, `data_ingestao`, `data_referencia_nova`, `janela_nova_dias`, `versao_layout`, `contagens` (JSON: camada, tabela, entrada, saída) e `metricas` (JSON: etapa, segundos, pico de memória). O histórico é mantido entre cargas.

---

## Usuário (`app.sqlite`)

A versão do esquema fica em `PRAGMA user_version` e as migrações são automáticas.

### usuario
`nome` (chave, minúsculas), `senha_hash` (scrypt$n$r$p$sal$hash), `criado_em`, `ultimo_acesso`.

### contato_complementar
| Coluna | Descrição |
|---|---|
| id | identificador |
| cnpj | CNPJ completo (14) |
| tipo | telefone, email, site ou rede_social |
| valor | como exibido |
| valor_normalizado | para evitar duplicatas (único por cnpj + tipo) |
| fonte_nome | ex.: "site oficial (domínio do e-mail da Receita)", "Brave Search API", "busca manual" |
| fonte_url | página onde foi encontrado |
| data_busca | data e hora |
| confianca | alta, media ou baixa (regra na decisão D23) |
| observacao | ex.: "pode ser do contador" |
| criado_por | usuário |

### busca_log
`cnpj`, `metodo` (site_oficial ou api_busca), `executado_em`, `resultado`, `detalhe`. Usado para o cache (`CACHE_BUSCA_DIAS`) e o limite diário da API.

### prospeccao_status
`cnpj` (chave), `status` (nao_prospectada, em_contato, contatada, sem_interesse, descartada ou cliente), `observacao`, `atualizado_em`, `atualizado_por`. CNPJ sem registro = não prospectada.

### status_historico
`cnpj`, `status`, `observacao`, `alterado_em`, `alterado_por`: uma linha por alteração.

### consulta_salva
`id`, `nome` (único), `filtros` (JSON de `Filtros`), `criado_em`, `criado_por`, `ultima_execucao`.

### consulta_execucao / consulta_execucao_cnpj
Execuções de cada consulta (`executado_em`, `mes_referencia`, `total`) e os CNPJs retornados. Ficam só as duas últimas por consulta, para calcular as empresas novas.

### Tabelas do agente classificador
Saíram deste banco: o agente (módulo 2) tem banco próprio, em `agente/` na raiz do repositório. Veja `agente/README.md`.

---

## Arquivo mensal de empresas novas (`data/exportacoes/novas/AAAA-MM/`)
Entrada do agente (módulo 2). Colunas, tipos e significado em `agente/schema/empresas_novas.v1.json`; o manifesto `_manifest.json` traz `mes`, `total_linhas`, `sha256` e `versao_schema`. Este módulo ainda não gera o arquivo.
