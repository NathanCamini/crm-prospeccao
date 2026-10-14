import type {
  AtividadeEquipe,
  Canal,
  DadosManuais,
  EmpresaCliente,
  Estagio,
  Ficha,
  Filtro,
  ItemSupressao,
  Membro,
  MensagemPreparada,
  Modelo,
  PaginaLeads,
  Perfil,
  Pessoa,
  Produto,
  PropostaProduto,
  RegistroAuditoria,
  Relatorio,
  Resumo,
  Tarefa,
  Usuario,
} from "./tipos";

export class ApiErro extends Error {
  constructor(
    public status: number,
    mensagem: string,
    /** Detalhes extras do servidor, ex.: a chave do lead que já existe. */
    public extra: Record<string, unknown> | null = null,
  ) {
    super(mensagem);
  }
}

function mensagemDeErro(status: number, dados: unknown): string {
  const detalhe = (dados as { detail?: unknown } | null)?.detail;
  if (typeof detalhe === "string") return detalhe;
  if (detalhe && typeof detalhe === "object" && "mensagem" in detalhe) return String((detalhe as { mensagem: unknown }).mensagem);
  if (Array.isArray(detalhe) && detalhe.length) {
    const campo = (detalhe[0] as { loc?: string[] }).loc?.slice(-1)[0];
    return campo ? `Confira o campo "${campo}".` : "Confira os dados informados.";
  }
  if (status >= 500) return "O servidor do CRM não respondeu. Tente de novo em instantes.";
  return "Não foi possível concluir a ação.";
}

async function req<T>(metodo: string, caminho: string, corpo?: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(caminho, {
      method: metodo,
      credentials: "same-origin",
      headers: { "X-CRM": "1", ...(corpo !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: corpo !== undefined ? JSON.stringify(corpo) : undefined,
    });
  } catch {
    throw new ApiErro(0, "Sem conexão com o CRM. Confira se o serviço está rodando.");
  }
  const dados = resp.headers.get("content-type")?.includes("json") ? await resp.json() : null;
  if (resp.status === 401 && !caminho.endsWith("/entrar")) {
    window.dispatchEvent(new Event("crm:sessao-expirada"));
  }
  if (!resp.ok) {
    const detalhe = (dados as { detail?: unknown } | null)?.detail;
    const extra = detalhe && typeof detalhe === "object" && !Array.isArray(detalhe) ? (detalhe as Record<string, unknown>) : null;
    throw new ApiErro(resp.status, mensagemDeErro(resp.status, dados), extra);
  }
  return dados as T;
}

/** Baixa um CSV gerado pela API (GET ou POST) e salva com o nome que o servidor sugerir. */
async function baixar(metodo: "GET" | "POST", caminho: string, corpo?: unknown): Promise<void> {
  const resp = await fetch(caminho, {
    method: metodo,
    credentials: "same-origin",
    headers: { "X-CRM": "1", ...(corpo !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: corpo !== undefined ? JSON.stringify(corpo) : undefined,
  });
  if (!resp.ok) {
    const dados = resp.headers.get("content-type")?.includes("json") ? await resp.json() : null;
    throw new ApiErro(resp.status, mensagemDeErro(resp.status, dados));
  }
  const nome = /filename="([^"]+)"/.exec(resp.headers.get("content-disposition") ?? "")?.[1] ?? "exportacao.csv";
  const url = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: nome });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function consulta(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export interface FiltroAuditoria {
  de?: string;
  ate?: string;
  usuario?: string;
  acao?: string;
  resultado?: string;
  tipo?: string;
  entidade_id?: string;
  empresa_id?: number;
  pagina?: number;
}

const enc = encodeURIComponent;

export const api = {
  eu: () => req<Usuario>("GET", "/api/auth/eu"),
  entrar: (usuario: string, senha: string, lembrar: boolean) =>
    req<Usuario>("POST", "/api/auth/entrar", { usuario, senha, lembrar }),
  sair: () => req<{ ok: boolean }>("POST", "/api/auth/sair"),
  trocarSenha: (senha_atual: string, senha_nova: string) => req("POST", "/api/auth/senha", { senha_atual, senha_nova }),
  escolherEmpresa: (empresa_id: number) => req<Usuario>("POST", "/api/auth/empresa", { empresa_id }),

  saude: () => req<{ crm: string; modulo3: Record<string, unknown> | null; erro?: string }>("GET", "/api/saude"),
  perfis: () => req<Perfil[]>("GET", "/api/perfis"),

  buscar: (f: Filtro) => req<PaginaLeads>("POST", "/api/leads/busca", f),
  resumo: (f: Filtro) => req<Resumo>("POST", "/api/leads/resumo", f),
  ficha: (chave: string) => req<Ficha>("GET", `/api/leads/${enc(chave)}`),
  exportarLeads: (f: Filtro) => baixar("POST", "/api/leads/exportar", f),
  criarManual: (d: DadosManuais) => req<{ chave: string }>("POST", "/api/leads/manual", d),
  editarManual: (chave: string, d: DadosManuais) =>
    req<{ alterados: string[] }>("PUT", `/api/leads/${enc(chave)}/dados`, d),
  criarTarefa: (chave: string, titulo: string, vence_em: string, responsavel?: string) =>
    req<{ id: number }>("POST", `/api/leads/${enc(chave)}/tarefas`, { titulo, vence_em, responsavel }),
  registrarVenda: (chave: string, valor: string, data?: string, descricao?: string, vendedor?: string) =>
    req<{ id: number }>("POST", `/api/leads/${enc(chave)}/vendas`, { valor, data, descricao, vendedor }),
  mudarEstagio: (cnpj: string, estagio: Estagio, nota?: string) =>
    req("PUT", `/api/leads/${enc(cnpj)}/estagio`, { estagio, nota }),
  mudarResponsavel: (cnpj: string, responsavel: string | null) =>
    req<{ responsavel: string | null }>("PUT", `/api/leads/${enc(cnpj)}/responsavel`, { responsavel }),
  nota: (cnpj: string, texto: string) => req("POST", `/api/leads/${enc(cnpj)}/notas`, { texto }),
  preparar: (cnpj: string, canal: Canal, destino: string, modelo_id?: number | null) =>
    req<MensagemPreparada>("POST", `/api/leads/${enc(cnpj)}/contato/preparar`, { canal, destino, modelo_id }),
  registrar: (cnpj: string, canal: Canal, destino: string, texto: string, assunto?: string) =>
    req<{ estagio: Estagio; url: string | null; simulado: boolean }>(
      "POST",
      `/api/leads/${enc(cnpj)}/contato/registrar`,
      { canal, destino, texto, assunto },
    ),
  suprimir: (cnpj: string, motivo: string) =>
    req<{ suprimidos: number }>("POST", `/api/leads/${enc(cnpj)}/suprimir`, { motivo }),

  produtos: () => req<{ itens: Produto[]; aviso: string | null; pode_editar: boolean; sincronizando: boolean }>("GET", "/api/produtos"),
  sugerirProduto: (descricao: string, perfil_id?: string) =>
    req<PropostaProduto>("POST", "/api/produtos/sugerir", { descricao, perfil_id }),
  salvarProduto: (descricao: string, perfil: PropostaProduto["perfil"], perfil_id?: string) =>
    req<{ id: string }>("POST", "/api/produtos", { descricao, perfil, perfil_id }),
  removerProduto: (id: string) => req("DELETE", `/api/produtos/${enc(id)}`),

  perguntar: (pergunta: string, chave?: string) =>
    req<{ resposta: string; fonte: "simulado" | "modulo2" }>("POST", "/api/agente/perguntar", { pergunta, chave }),

  tarefas: (quem = "eu", situacao: "abertas" | "concluidas" = "abertas") =>
    req<Tarefa[]>("GET", `/api/tarefas${consulta({ quem, situacao })}`),
  contagemTarefas: () => req<{ atrasadas: number; hoje: number; data: string }>("GET", "/api/tarefas/contagem"),
  alterarTarefa: (id: number, dados: { titulo?: string; vence_em?: string; responsavel?: string; concluida?: boolean }) =>
    req("PUT", `/api/tarefas/${id}`, dados),
  apagarTarefa: (id: number) => req("DELETE", `/api/tarefas/${id}`),
  estornarVenda: (id: number, motivo: string) => req("POST", `/api/vendas/${id}/estornar`, { motivo }),

  relatorio: (de?: string, ate?: string) => req<Relatorio>("GET", `/api/relatorios${consulta({ de, ate })}`),
  exportarRelatorio: (tipo: "vendas" | "atividades" | "tarefas", de?: string, ate?: string) =>
    baixar("GET", `/api/relatorios/exportar${consulta({ tipo, de, ate })}`),

  auditoria: (f: FiltroAuditoria) =>
    req<{ total: number; pagina: number; por_pagina: number; itens: RegistroAuditoria[] }>(
      "GET",
      `/api/auditoria${consulta({ ...f })}`,
    ),
  exportarAuditoria: (f: FiltroAuditoria) => baixar("GET", `/api/auditoria/exportar${consulta({ ...f, pagina: undefined })}`),
  verificarAuditoria: () => req<{ linhas: number; integra: boolean; adulterada_em: number | null }>("GET", "/api/auditoria/verificar"),
  /** Registra na auditoria uma ação que só acontece na tela. Nunca atrapalha o uso se falhar. */
  evento: (acao: string, entidade_id?: string, detalhe?: Record<string, string> | string) => {
    req("POST", "/api/auditoria/evento", { acao, entidade_id, detalhe }).catch(() => undefined);
  },

  modelos: () => req<{ itens: Modelo[]; variaveis: string[]; pode_editar: boolean }>("GET", "/api/modelos"),
  salvarModelo: (m: Omit<Modelo, "id" | "atualizado_em">, id?: number) =>
    id ? req("PUT", `/api/modelos/${id}`, m) : req("POST", "/api/modelos", m),
  apagarModelo: (id: number) => req("DELETE", `/api/modelos/${id}`),

  supressao: () =>
    req<{ itens: ItemSupressao[]; pode_liberar: boolean; pode_global: boolean }>("GET", "/api/supressao"),
  suprimirContato: (tipo: ItemSupressao["tipo"], valor: string, motivo?: string, global = false) =>
    req("POST", "/api/supressao", { tipo, valor, motivo, global }),
  removerSupressao: (id: number) => req("DELETE", `/api/supressao/${id}`),

  nomes: () => req<Pessoa[]>("GET", "/api/equipe/nomes"),
  equipe: () => req<Membro[]>("GET", "/api/equipe"),
  criarMembro: (dados: { usuario: string; nome_exibicao: string; papel: "admin" | "vendedor"; senha_inicial: string }) =>
    req<{ usuario: string }>("POST", "/api/equipe", dados),
  alterarMembro: (nome: string, dados: { nome_exibicao?: string; papel?: "admin" | "vendedor"; ativo?: boolean }) =>
    req("PUT", `/api/equipe/${enc(nome)}`, dados),
  redefinirSenha: (nome: string, senha_provisoria: string) =>
    req("POST", `/api/equipe/${enc(nome)}/senha`, { senha_provisoria }),
  removerMembro: (nome: string) => req<{ leads_liberados: number }>("DELETE", `/api/equipe/${enc(nome)}`),
  atividadesEquipe: (usuario?: string) =>
    req<AtividadeEquipe[]>("GET", `/api/equipe/atividades${usuario ? `?usuario=${enc(usuario)}` : ""}`),

  todosPerfis: () => req<Perfil[]>("GET", "/api/admin/perfis"),
  sincronizacao: () =>
    req<{
      execucoes: { id: number; inicio: string; fim: string | null; lidos: number | null; novos: number | null; atualizados: number | null; ausentes: number | null; fonte: string | null; erro: string | null }[];
      leads: { total: number; do_agente: number | null; manuais: number | null; fora_da_base: number | null };
    }>("GET", "/api/admin/sincronizacao"),
  sincronizar: () => req<{ lidos: number; novos: number; atualizados: number; ausentes: number }>("POST", "/api/admin/sincronizacao"),
  empresasClientes: () => req<EmpresaCliente[]>("GET", "/api/admin/empresas"),
  criarEmpresaCliente: (dados: {
    nome: string;
    documento?: string;
    perfis: string[];
    admin: { usuario: string; nome_exibicao: string; senha_inicial: string };
  }) => req<{ id: number; admin: string }>("POST", "/api/admin/empresas", dados),
  alterarEmpresaCliente: (id: number, dados: { nome?: string; documento?: string; perfis?: string[]; ativa?: boolean }) =>
    req("PUT", `/api/admin/empresas/${id}`, dados),
};
