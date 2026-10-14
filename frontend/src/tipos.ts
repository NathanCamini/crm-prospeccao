// Espelho do contrato v1 do módulo 3 (contrato/lead.v1.json) + campos do CRM.

export type Canal = "email" | "whatsapp" | "formulario" | "telefone";
export type TipoContato = "email" | "telefone" | "whatsapp" | "site" | "formulario" | "rede_social";
export type Confianca = "alta" | "media" | "baixa";

export const ESTAGIOS = [
  "novo",
  "em_contato",
  "respondeu",
  "interessado",
  "amostra_enviada",
  "cliente",
  "sem_interesse",
  "descartado",
] as const;
export type Estagio = (typeof ESTAGIOS)[number];

export interface ContatoReceita {
  ddd_1: string | null;
  telefone_1: string | null;
  ddd_2: string | null;
  telefone_2: string | null;
  email: string | null;
}

export interface Contato {
  tipo: TipoContato;
  valor: string;
  fonte: string;
  url: string | null;
  confianca: Confianca;
  observacao: string | null;
}

export interface Classificacao {
  perfil_id: string;
  perfil_versao: number;
  score: number;
  e_alvo: boolean;
  justificativa: string;
  e_franquia: boolean | null;
  marca: string | null;
  padrao: string | null;
  produto_sugerido: string | null;
  classificado_em: string | null;
}

export interface Lead {
  chave: string;
  origem: "modulo3" | "manual";
  ausente_desde: string | null;
  cnpj: string | null;
  contato_nome?: string | null;
  perfil_id?: string | null;
  razao_social: string;
  nome_fantasia: string | null;
  matriz_filial: string | null;
  situacao_cadastral: string | null;
  data_inicio_atividade: string | null;
  cnae_principal: string | null;
  cnae_descricao: string | null;
  porte: string | null;
  capital_social: number | null;
  logradouro: string | null;
  numero: string | null;
  bairro: string | null;
  municipio: string | null;
  uf: string | null;
  cep: string | null;
  contato_receita: ContatoReceita;
  contatos: Contato[];
  classificacao: Classificacao | null;
  canal_sugerido: Canal | null;
  mes_referencia: string | null;
}

export interface CrmResumo {
  estagio: Estagio;
  responsavel: string | null;
  ultima_atividade_em: string | null;
  ultima_atividade_tipo: string | null;
  proximo_passo_em: string | null;
  proximo_passo_titulo: string | null;
  suprimido: boolean;
}

export interface LeadLinha extends Lead {
  crm: CrmResumo;
}

export interface PaginaLeads {
  fonte: "simulado" | "modulo2" | null;
  mes_referencia: string | null;
  sincronizado_em: string | null;
  total: number;
  pagina: number;
  por_pagina: number;
  itens: LeadLinha[];
}

export interface Resumo {
  total: number;
  por_estagio: Record<Estagio, number>;
  fonte: "simulado" | "modulo2" | null;
  mes_referencia: string | null;
}

export interface Atividade {
  id: number;
  tipo:
    | "nota"
    | "estagio"
    | "contato"
    | "agente"
    | "supressao"
    | "responsavel"
    | "cadastro"
    | "dados"
    | "tarefa"
    | "tarefa_concluida"
    | "venda"
    | "venda_estornada";
  canal: Canal | null;
  destino: string | null;
  assunto: string | null;
  texto: string | null;
  criado_em: string;
  criado_por: string;
}

export interface Tarefa {
  id: number;
  chave: string;
  titulo: string;
  vence_em: string;
  responsavel: string;
  concluida_em: string | null;
  concluida_por: string | null;
  criado_em: string;
  criado_por: string;
  lead_nome?: string;
  municipio?: string | null;
  uf?: string | null;
  estagio?: Estagio;
}

export interface Venda {
  id: number;
  valor_centavos: number;
  data: string;
  descricao: string | null;
  vendedor: string;
  criado_em: string;
  criado_por: string;
  estornada_em: string | null;
  estornada_por: string | null;
  motivo_estorno: string | null;
}

export interface Ficha {
  lead: Lead;
  crm: { estagio: Estagio; responsavel: string | null; atualizado_em: string | null };
  atividades: Atividade[];
  tarefas: Tarefa[];
  vendas: Venda[];
  total_vendas_centavos: number;
  cadastro: { criado_em: string; criado_por: string } | null;
  supressoes: { tipo: string; valor: string; motivo: string | null; global: boolean }[];
  simulado: boolean;
}

export interface Perfil {
  id: string;
  nome: string;
  produto: string | null;
  total_alvos: number;
}

/** O que a empresa cliente vende: o agente transforma a descrição num filtro de leads. */
export interface FiltroProduto {
  cnaes?: string[];
  cnae_prefixos?: string[];
  incluir_cnaes_secundarios?: boolean;
  ufs?: string[];
  municipios?: string[];
}

export interface ExecucaoProduto {
  situacao: "na_fila" | "classificando" | "pronto" | "erro" | "aguardando_mes";
  mes?: string;
  inicio?: string;
  fim?: string;
  alvos?: number;
  gravados?: number;
  erro?: string | null;
}

export interface Produto {
  id: string;
  nome: string;
  descricao: string;
  versao: number;
  criado_por: string;
  criado_em: string;
  atualizado_em: string;
  filtro: FiltroProduto | null;
  criterios: string[] | null;
  execucao: ExecucaoProduto | null;
  alvos: number | null;
}

export interface PropostaProduto {
  perfil: { nome?: string; produto: string; filtro_sql: FiltroProduto; criterios_llm: string[]; [k: string]: unknown };
  explicacao: string;
  atividades: { codigo: string; descricao: string; grupo?: boolean }[];
  previa: {
    mes: string | null;
    total: number | null;
    limite: number;
    cabe: boolean;
    aviso?: string | null;
    exemplos: { nome: string; municipio: string | null; uf: string | null; atividade: string | null; data_inicio_atividade: string | null }[];
  };
}

export interface Modelo {
  id: number;
  nome: string;
  canal: Canal;
  perfil_id: string | null;
  assunto: string | null;
  corpo: string;
  atualizado_em: string;
}

export interface MensagemPreparada {
  canal: Canal;
  destino: string;
  modelo_id: number | null;
  assunto: string;
  texto: string;
  url: string | null;
  simulado: boolean;
}

export interface ItemSupressao {
  id: number;
  tipo: "email" | "telefone" | "dominio" | "cnpj";
  valor: string;
  motivo: string | null;
  criado_em: string;
  criado_por: string;
  global: boolean;
}

export type Papel = "superadmin" | "admin" | "vendedor";

export interface Usuario {
  usuario: string;
  nome_exibicao: string;
  papel: Papel;
  deve_trocar_senha: boolean;
  empresa: { id: number; nome: string } | null;
  empresas?: { id: number; nome: string; ativa: number }[];
}

export interface Pessoa {
  usuario: string;
  nome_exibicao: string;
  papel: Papel;
  ativo: number | boolean;
}

export interface Membro {
  usuario: string;
  nome_exibicao: string;
  papel: "admin" | "vendedor";
  ativo: boolean;
  deve_trocar_senha: boolean;
  criado_em: string;
  ultimo_acesso: string | null;
  leads: number;
  leads_por_estagio: Partial<Record<Estagio, number>>;
  clientes: number;
  contatos_7d: number;
  atividades_7d: number;
  ultima_atividade: string | null;
}

export interface AtividadeEquipe extends Atividade {
  chave: string;
  empresa_nome: string | null;
}

export interface DadosManuais {
  nome: string;
  cnpj?: string | null;
  razao_social?: string | null;
  contato_nome?: string | null;
  telefone?: string | null;
  whatsapp?: string | null;
  email?: string | null;
  site?: string | null;
  logradouro?: string | null;
  bairro?: string | null;
  municipio?: string | null;
  uf?: string | null;
  perfil_id?: string | null;
  observacao?: string | null;
}

export interface Relatorio {
  periodo: { de: string; ate: string };
  totais: {
    contatos: number;
    leads_trabalhados: number;
    respostas: number;
    novos_clientes: number;
    vendas_qtd: number;
    vendas_centavos: number;
    ticket_medio_centavos: number;
    tarefas_concluidas: number;
    tarefas_atrasadas: number;
  };
  por_pessoa: {
    usuario: string;
    nome_exibicao: string;
    contatos: number;
    leads_trabalhados: number;
    respostas: number;
    novos_clientes: number;
    vendas_qtd: number;
    vendas_centavos: number;
    tarefas_concluidas: number;
    tarefas_atrasadas: number;
  }[];
  por_canal: { canal: string; contatos: number; leads: number; viraram_cliente: number }[];
  por_perfil: { perfil_id: string; leads_trabalhados: number; novos_clientes: number; vendas_centavos: number }[];
  vendas_por_semana: { semana: string; qtd: number; centavos: number }[];
  funil: Record<Estagio, number>;
}

export type ResultadoAuditoria = "ok" | "negado" | "recusado" | "erro";

export interface RegistroAuditoria {
  id: number;
  quando: string;
  requisicao: string | null;
  usuario: string | null;
  papel: string | null;
  empresa_id: number | null;
  tipo: "acesso" | "alteracao" | "seguranca" | "evento" | "sistema";
  acao: string;
  entidade: string | null;
  entidade_id: string | null;
  antes: Record<string, unknown> | null;
  depois: Record<string, unknown> | null;
  detalhe: unknown;
  resultado: ResultadoAuditoria;
  status: number | null;
  metodo: string | null;
  rota: string | null;
  ip: string | null;
  agente: string | null;
  lead_nome: string | null;
}

export interface EmpresaCliente {
  id: number;
  nome: string;
  documento: string | null;
  perfis: string[];
  ativa: boolean;
  criada_em: string;
  pessoas: number;
  admins: string | null;
  leads_trabalhados: number;
  clientes: number;
  contatos_7d: number;
  ultima_atividade: string | null;
  produtos: string | null;
}

export interface Filtro {
  perfil_id?: string;
  uf?: string[];
  municipio?: string;
  score_min?: number;
  texto?: string;
  ordenar?: "score" | "abertura" | "nome" | "atividade" | "proximo_passo" | "cadastro";
  estagio?: Estagio;
  responsavel?: string;
  origem?: "agente" | "manual";
  pagina?: number;
  por_pagina?: number;
}
