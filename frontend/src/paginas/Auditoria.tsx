import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api, ApiErro, type FiltroAuditoria } from "../api";
import { useAviso } from "../componentes/Aviso";
import { dataHora, reais, ROTULO_ESTAGIO, titulo } from "../formato";
import { useSessao } from "../sessao";
import type { Estagio, RegistroAuditoria } from "../tipos";

export const ROTULO_ACAO: Record<string, string> = {
  "login.ok": "Entrou no sistema",
  "login.falhou": "Tentativa de entrada recusada",
  "sessao.sair": "Saiu do sistema",
  "senha.trocar": "Trocou a própria senha",
  "senha.trocar_falhou": "Errou a senha atual ao tentar trocar",
  "senha.provisoria": "Criou senha provisória para alguém",
  "acesso.negado": "Acesso negado",
  "empresa.entrar": "Entrou numa empresa cliente",
  "leads.listar": "Consultou a lista de leads",
  "lead.ver": "Abriu a ficha do lead",
  "lead.estagio": "Mudou o estágio",
  "lead.responsavel": "Mudou o responsável",
  "lead.nota": "Escreveu uma nota",
  "lead.contato": "Registrou um contato",
  "lead.suprimir": "Marcou para não contatar",
  "lead.criar_manual": "Cadastrou um lead",
  "lead.editar": "Editou os dados do lead",
  "contato.preparar": "Preparou uma mensagem",
  "contato.bloqueado": "Contato barrado pela lista de não contatar",
  "tarefa.criar": "Agendou um próximo passo",
  "tarefa.editar": "Alterou um próximo passo",
  "tarefa.concluir": "Concluiu um próximo passo",
  "tarefa.reabrir": "Reabriu um próximo passo",
  "tarefa.apagar": "Apagou um próximo passo",
  "venda.registrar": "Registrou uma venda",
  "venda.estornar": "Estornou uma venda",
  "modelo.criar": "Criou modelo de mensagem",
  "modelo.editar": "Editou modelo de mensagem",
  "modelo.apagar": "Apagou modelo de mensagem",
  "supressao.incluir": "Incluiu na lista de não contatar",
  "supressao.liberar": "Liberou da lista de não contatar",
  "equipe.criar": "Cadastrou uma pessoa",
  "equipe.alterar": "Alterou uma pessoa",
  "equipe.remover": "Removeu uma pessoa",
  "empresa.criar": "Cadastrou empresa cliente",
  "empresa.alterar": "Alterou empresa cliente",
  "agente.perguntar": "Perguntou ao agente",
  "relatorio.ver": "Abriu os relatórios",
  "exportacao.leads": "Exportou leads",
  "exportacao.vendas": "Exportou vendas",
  "exportacao.atividades": "Exportou atividades",
  "exportacao.tarefas": "Exportou próximos passos",
  "exportacao.auditoria": "Exportou a auditoria",
  "auditoria.consultar": "Consultou a auditoria",
  "auditoria.verificar": "Conferiu a integridade da auditoria",
  "sincronizacao.concluida": "Sincronização com o módulo 3",
  "sincronizacao.falhou": "Sincronização com o módulo 3 falhou",
  "sincronizacao.manual": "Pediu sincronização na hora",
  "terminal.superadmin_criar": "Superadmin criado pelo terminal",
  "terminal.senha": "Senha trocada pelo terminal",
  "terminal.senha_provisoria": "Senha provisória pelo terminal",
  "ui.copiar_mensagem": "Copiou o texto de uma mensagem",
  "ui.abrir_site": "Abriu o site ou rede social",
  "ui.ligar": "Clicou para ligar",
  "ui.abrir_link_contato": "Abriu WhatsApp, e-mail ou formulário",
  "ui.copiar_credencial": "Copiou dados de acesso de uma pessoa",
};

const GRUPOS_ACAO: [string, string][] = [
  ["", "Todas as ações"],
  ["login.", "Entradas no sistema"],
  ["lead.", "Leads"],
  ["contato.", "Contatos"],
  ["venda.", "Vendas"],
  ["tarefa.", "Próximos passos"],
  ["equipe.", "Equipe"],
  ["supressao.", "Não contatar"],
  ["exportacao.", "Exportações"],
  ["empresa.", "Empresas clientes"],
  ["ui.", "Cliques na tela"],
];

const ROTULO_CAMPO: Record<string, string> = {
  estagio: "Estágio",
  responsavel: "Responsável",
  nome_exibicao: "Nome",
  papel: "Papel",
  ativo: "Ativo",
  valor_centavos: "Valor",
  vence_em: "Vence em",
  concluida: "Concluído",
  titulo: "Título",
  corpo: "Texto",
  assunto: "Assunto",
  perfis: "Perfis",
  ativa: "Ativa",
  estornada: "Estornada",
  motivo: "Motivo",
};

function valor(campo: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "(vazio)";
  if (typeof v === "boolean") return v ? "Sim" : "Não";
  if (campo === "estagio" && typeof v === "string") return ROTULO_ESTAGIO[v as Estagio] ?? v;
  if (campo === "valor_centavos" && typeof v === "number") return reais(v);
  if (Array.isArray(v)) return v.join(", ") || "(nenhum)";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Diferencas({ r }: { r: RegistroAuditoria }) {
  const campos = [...new Set([...Object.keys(r.antes ?? {}), ...Object.keys(r.depois ?? {})])];
  if (!campos.length) return null;
  return (
    <table className="tabela tabela-compacta diff">
      <thead>
        <tr>
          <th>Campo</th>
          <th>Antes</th>
          <th>Depois</th>
        </tr>
      </thead>
      <tbody>
        {campos.map((c) => (
          <tr key={c}>
            <td className="rotulo-linha">{ROTULO_CAMPO[c] ?? c}</td>
            <td className="antes">{r.antes && c in r.antes ? valor(c, r.antes[c]) : "—"}</td>
            <td className="depois">{r.depois && c in r.depois ? valor(c, r.depois[c]) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const ROTULO_RESULTADO = { ok: "ok", negado: "negado", recusado: "recusado", erro: "erro" };

function lerFiltro(p: URLSearchParams): FiltroAuditoria {
  const f: FiltroAuditoria = {};
  for (const k of ["de", "ate", "usuario", "acao", "resultado", "tipo", "entidade_id"] as const) {
    const v = p.get(k);
    if (v) f[k] = v;
  }
  if (p.get("empresa_id")) f.empresa_id = Number(p.get("empresa_id"));
  f.pagina = Number(p.get("pagina") || 1);
  return f;
}

export default function Auditoria() {
  const { eSuper, usuario, pessoas, nomeDe } = useSessao();
  const aviso = useAviso();
  const [params, setParams] = useSearchParams();
  const filtro = useMemo(() => lerFiltro(params), [params]);
  const [res, setRes] = useState<{ total: number; itens: RegistroAuditoria[] } | null>(null);
  const [verificacao, setVerificacao] = useState<string | null>(null);

  useEffect(() => {
    api.auditoria(filtro).then(setRes, (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível consultar.", "erro"));
  }, [filtro, aviso]);

  function definir(k: string, v: string | undefined) {
    const n = new URLSearchParams(params);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k !== "pagina") n.delete("pagina");
    setParams(n, { replace: true });
  }

  async function verificar() {
    try {
      const r = await api.verificarAuditoria();
      setVerificacao(
        r.integra
          ? `Íntegra: ${r.linhas} registros conferidos, nenhum alterado ou apagado.`
          : `ATENÇÃO: o registro ${r.adulterada_em} foi alterado ou apagado fora do sistema.`,
      );
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível verificar.", "erro");
    }
  }

  const paginas = res ? Math.max(1, Math.ceil(res.total / 50)) : 1;
  // Acessos iguais em sequência (mesma pessoa, mesma ação, mesmo alvo) aparecem numa linha só, com a contagem.
  // Nenhum registro some: a contagem diz quantos são e o CSV traz todos.
  const linhas: { r: RegistroAuditoria; vezes: number }[] = [];
  for (const r of res?.itens ?? []) {
    const ant = linhas[linhas.length - 1];
    if (ant && r.tipo === "acesso" && ant.r.tipo === "acesso" && ant.r.usuario === r.usuario && ant.r.acao === r.acao &&
        ant.r.entidade_id === r.entidade_id && ant.r.resultado === r.resultado) ant.vezes += 1;
    else linhas.push({ r, vezes: 1 });
  }
  const nomeEmpresa = (id: number | null) => usuario.empresas?.find((e) => e.id === id)?.nome;

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Auditoria</h1>
          <p className="sub">
            Tudo o que acontece no sistema: quem fez, quando, de onde e o que mudou. Inclui acessos negados e tentativas recusadas.
            Os registros não podem ser alterados nem apagados.
          </p>
        </div>
        <div className="cabecalho-acoes">
          {eSuper && (
            <button type="button" className="botao" onClick={verificar}>
              Conferir integridade
            </button>
          )}
          <button
            type="button"
            className="botao"
            onClick={() => api.exportarAuditoria(filtro).catch((e) => aviso(e instanceof ApiErro ? e.message : "Falhou.", "erro"))}
          >
            Exportar CSV
          </button>
        </div>
      </header>

      {verificacao && (
        <div className={`faixa ${verificacao.startsWith("ATENÇÃO") ? "faixa-erro" : "faixa-ok"}`} role="status">
          {verificacao}
        </div>
      )}

      <div className="filtros">
        <label className="campo campo-curto">
          <span>De</span>
          <input type="date" value={filtro.de ?? ""} onChange={(e) => definir("de", e.target.value)} />
        </label>
        <label className="campo campo-curto">
          <span>Até</span>
          <input type="date" value={filtro.ate ?? ""} onChange={(e) => definir("ate", e.target.value)} />
        </label>
        <label className="campo">
          <span>Pessoa</span>
          {eSuper && filtro.empresa_id === undefined ? (
            <input value={filtro.usuario ?? ""} placeholder="usuário" onChange={(e) => definir("usuario", e.target.value.trim().toLowerCase())} />
          ) : (
            <select value={filtro.usuario ?? ""} onChange={(e) => definir("usuario", e.target.value)}>
              <option value="">Todas</option>
              {pessoas.map((p) => (
                <option key={p.usuario} value={p.usuario}>
                  {p.nome_exibicao}
                </option>
              ))}
              <option value="sistema">Sistema</option>
            </select>
          )}
        </label>
        <label className="campo">
          <span>Ação</span>
          <select value={filtro.acao ?? ""} onChange={(e) => definir("acao", e.target.value)}>
            {GRUPOS_ACAO.map(([v, r]) => (
              <option key={v} value={v}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="campo">
          <span>Tipo</span>
          <select value={filtro.tipo ?? ""} onChange={(e) => definir("tipo", e.target.value)}>
            <option value="">Todos</option>
            <option value="alteracao">Alterações</option>
            <option value="acesso">Acessos e consultas</option>
            <option value="seguranca">Segurança</option>
            <option value="evento">Cliques na tela</option>
            <option value="sistema">Sistema</option>
          </select>
        </label>
        <label className="campo campo-curto">
          <span>Resultado</span>
          <select value={filtro.resultado ?? ""} onChange={(e) => definir("resultado", e.target.value)}>
            <option value="">Todos</option>
            <option value="ok">Ok</option>
            <option value="negado">Negado</option>
            <option value="recusado">Recusado</option>
            <option value="erro">Erro</option>
          </select>
        </label>
        {eSuper && (
          <label className="campo">
            <span>Empresa</span>
            <select value={filtro.empresa_id ?? ""} onChange={(e) => definir("empresa_id", e.target.value)}>
              <option value="">Todas</option>
              {usuario.empresas?.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.nome}
                </option>
              ))}
            </select>
          </label>
        )}
        {filtro.entidade_id && (
          <button type="button" className="botao-texto" onClick={() => definir("entidade_id", undefined)}>
            Mostrar todos os registros (filtro: {filtro.entidade_id})
          </button>
        )}
      </div>

      <div className="tabela-area">
        <table className="tabela tabela-auditoria">
          <thead>
            <tr>
              <th>Quando</th>
              <th>Quem</th>
              <th>O quê</th>
              <th>Resultado</th>
              <th>De onde</th>
            </tr>
          </thead>
          <tbody>
            {linhas.map(({ r, vezes }) => (
              <tr key={r.id} className={`sem-clique linha-${r.resultado}`}>
                <td className="nowrap tabular">{dataHora(r.quando)}</td>
                <td>
                  <span className="empresa-nome">{r.usuario ? (r.usuario === "sistema" ? "Sistema" : nomeDe(r.usuario)) : "Sem login"}</span>
                  {eSuper && r.empresa_id !== null && <span className="empresa-meta">{nomeEmpresa(r.empresa_id) ?? `empresa ${r.empresa_id}`}</span>}
                </td>
                <td>
                  <details className="registro">
                    <summary>
                      {ROTULO_ACAO[r.acao] ?? (r.acao.startsWith("rota.") ? `Acessou ${r.rota}` : r.acao)}
                      {r.entidade === "lead" && r.entidade_id && (
                        <>
                          {" "}
                          <Link to={`/leads/${r.entidade_id}`} onClick={(e) => e.stopPropagation()}>
                            {r.lead_nome ? titulo(r.lead_nome) : r.entidade_id}
                          </Link>
                        </>
                      )}
                      {r.entidade && r.entidade !== "lead" && r.entidade_id && <span className="mudo"> ({r.entidade} {r.entidade_id})</span>}
                      {vezes > 1 && <span className="marcador">{vezes} vezes seguidas</span>}
                    </summary>
                    <Diferencas r={r} />
                    {r.detalhe !== null && r.detalhe !== undefined && (
                      <pre className="detalhe">{typeof r.detalhe === "string" ? r.detalhe : JSON.stringify(r.detalhe, null, 2)}</pre>
                    )}
                    <p className="nota">
                      {r.metodo} {r.rota} → {r.status}. Navegador: {r.agente ?? "—"}. Requisição {r.requisicao}. Registro nº {r.id}.
                    </p>
                  </details>
                </td>
                <td>
                  <span className={`resultado resultado-${r.resultado}`}>{ROTULO_RESULTADO[r.resultado]}</span>
                </td>
                <td className="mudo tabular nowrap">{r.ip ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {res && res.itens.length === 0 && <div className="vazio-tabela">Nenhum registro com esses filtros.</div>}
      </div>

      {res && res.total > 50 && (
        <nav className="paginacao" aria-label="Páginas">
          <button type="button" className="botao" disabled={(filtro.pagina ?? 1) <= 1} onClick={() => definir("pagina", String((filtro.pagina ?? 1) - 1))}>
            Anterior
          </button>
          <span>
            Página {filtro.pagina} de {paginas}, {res.total} registros
          </span>
          <button
            type="button"
            className="botao"
            disabled={(filtro.pagina ?? 1) >= paginas}
            onClick={() => definir("pagina", String((filtro.pagina ?? 1) + 1))}
          >
            Próxima
          </button>
        </nav>
      )}
    </div>
  );
}
