import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { descrever } from "../componentes/LinhaDoTempo";
import { dataHora, relativo, ROTULO_ESTAGIO, titulo } from "../formato";
import { useSessao } from "../sessao";
import { ESTAGIOS, type AtividadeEquipe, type Membro } from "../tipos";

export function senhaProvisoria(): string {
  const letras = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  return Array.from(bytes, (b) => letras[b % letras.length]).join("");
}

interface Credencial {
  nome: string;
  usuario: string;
  senha: string;
}

export function AvisoCredencial({ c, aoFechar }: { c: Credencial; aoFechar: () => void }) {
  const texto = `Usuário: ${c.usuario}\nSenha provisória: ${c.senha}\nEndereço: ${window.location.origin}`;
  return (
    <div className="credencial" role="status">
      <div>
        <strong>Passe estes dados para {c.nome}.</strong>
        <p>
          Usuário <code>{c.usuario}</code>, senha provisória <code>{c.senha}</code>. No primeiro acesso a pessoa cria a própria senha.
          A senha provisória não aparece de novo depois que você fechar este aviso.
        </p>
      </div>
      <div className="acoes">
        <button
          type="button"
          className="botao"
          onClick={() => {
            navigator.clipboard?.writeText(texto);
            api.evento("ui.copiar_credencial", undefined, { usuario: c.usuario });
          }}
        >
          Copiar dados de acesso
        </button>
        <button type="button" className="botao-texto" onClick={aoFechar}>
          Fechar
        </button>
      </div>
    </div>
  );
}

/** Barra fina com a distribuição dos leads da pessoa por estágio. */
function MiniFunil({ porEstagio, total }: { porEstagio: Membro["leads_por_estagio"]; total: number }) {
  if (!total) return <span className="mudo">nenhum</span>;
  const resumo = ESTAGIOS.filter((e) => porEstagio[e]).map((e) => `${ROTULO_ESTAGIO[e]}: ${porEstagio[e]}`).join(", ");
  return (
    <span className="mini-funil" title={resumo} aria-label={resumo}>
      <span className="mini-num">{total}</span>
      <span className="mini-barra" aria-hidden="true">
        {ESTAGIOS.map((e) =>
          porEstagio[e] ? <span key={e} style={{ flexGrow: porEstagio[e], background: `var(--e-${e})` }} /> : null,
        )}
      </span>
    </span>
  );
}

export default function Equipe() {
  const { usuario, nomeDe } = useSessao();
  const aviso = useAviso();
  const [membros, setMembros] = useState<Membro[]>([]);
  const [atividades, setAtividades] = useState<AtividadeEquipe[]>([]);
  const [filtro, setFiltro] = useState("");
  const [adicionando, setAdicionando] = useState(false);
  const [credencial, setCredencial] = useState<Credencial | null>(null);

  const carregar = useCallback(() => {
    api.equipe().then(setMembros, (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível carregar a equipe.", "erro"));
  }, [aviso]);

  useEffect(carregar, [carregar]);
  useEffect(() => {
    api.atividadesEquipe(filtro || undefined).then(setAtividades, () => setAtividades([]));
  }, [filtro, membros]);

  async function acao(fn: () => Promise<unknown>, ok: string): Promise<boolean> {
    try {
      await fn();
      aviso(ok);
      carregar();
      return true;
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível concluir.", "erro");
      return false;
    }
  }

  function redefinir(m: Membro) {
    if (!window.confirm(`Criar uma senha provisória para ${m.nome_exibicao}? A sessão atual da pessoa será encerrada.`)) return;
    const senha = senhaProvisoria();
    acao(() => api.redefinirSenha(m.usuario, senha), "Senha provisória criada.").then(
      (ok) => ok && setCredencial({ nome: m.nome_exibicao, usuario: m.usuario, senha }),
    );
  }

  function remover(m: Membro) {
    if (!window.confirm(`Remover ${m.nome_exibicao}? O histórico continua com o nome da pessoa e os leads dela ficam livres para a equipe.`))
      return;
    api.removerMembro(m.usuario).then(
      (r) => {
        aviso(`${m.nome_exibicao} removido. ${r.leads_liberados} lead(s) ficaram livres.`);
        if (filtro === m.usuario) setFiltro("");
        carregar();
      },
      (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível remover.", "erro"),
    );
  }

  const ativos = membros.filter((m) => m.ativo);
  const semana = ativos.reduce((s, m) => s + m.contatos_7d, 0);

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Equipe</h1>
          <p className="sub">
            {ativos.length} {ativos.length === 1 ? "pessoa ativa" : "pessoas ativas"} na {usuario.empresa?.nome}, {semana}{" "}
            {semana === 1 ? "contato" : "contatos"} nos últimos 7 dias.
          </p>
        </div>
        {!adicionando && (
          <button type="button" className="botao botao-primario" onClick={() => setAdicionando(true)}>
            Adicionar pessoa
          </button>
        )}
      </header>

      {credencial && <AvisoCredencial c={credencial} aoFechar={() => setCredencial(null)} />}
      {adicionando && (
        <NovaPessoa
          aoCancelar={() => setAdicionando(false)}
          aoCriar={(c) => {
            setAdicionando(false);
            setCredencial(c);
            carregar();
          }}
        />
      )}

      <div className="tabela-area">
        <table className="tabela tabela-equipe">
          <thead>
            <tr>
              <th>Pessoa</th>
              <th>Leads com a pessoa</th>
              <th className="num">Clientes</th>
              <th className="num">Contatos 7 dias</th>
              <th>Última atividade</th>
              <th>
                <span className="oculto">Ações</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {membros.map((m) => {
              const eu = m.usuario === usuario.usuario;
              return (
                <tr key={m.usuario} className={`sem-clique${m.ativo ? "" : " inativa"}`}>
                  <td>
                    <span className="empresa-nome">
                      {m.nome_exibicao}
                      {eu && <span className="mudo"> (você)</span>}
                    </span>
                    <span className="empresa-meta">
                      {m.usuario}
                      <span className={`marcador${m.papel === "admin" ? " marcador-papel" : ""}`}>
                        {m.papel === "admin" ? "administrador" : "vendedor"}
                      </span>
                      {m.deve_trocar_senha && <span className="marcador">aguardando primeiro acesso</span>}
                      {!m.ativo && <span className="marcador marcador-alerta">desativado</span>}
                    </span>
                  </td>
                  <td>
                    <MiniFunil porEstagio={m.leads_por_estagio} total={m.leads} />
                  </td>
                  <td className="num">{m.clientes}</td>
                  <td className="num">{m.contatos_7d}</td>
                  <td className="mudo nowrap">{relativo(m.ultima_atividade)}</td>
                  <td className="direita acoes-linha">
                    <button type="button" className="botao-texto" onClick={() => setFiltro(m.usuario)}>
                      Ver atividades
                    </button>
                    <Link className="botao-texto" to={`/leads?responsavel=${encodeURIComponent(m.usuario)}`}>
                      Ver leads
                    </Link>
                    {!eu && (
                      <details className="menu">
                        <summary className="botao-texto">Mais</summary>
                        <div className="menu-itens">
                          <button
                            type="button"
                            onClick={() =>
                              acao(
                                () => api.alterarMembro(m.usuario, { papel: m.papel === "admin" ? "vendedor" : "admin" }),
                                m.papel === "admin" ? `${m.nome_exibicao} agora é vendedor.` : `${m.nome_exibicao} agora é administrador.`,
                              )
                            }
                          >
                            {m.papel === "admin" ? "Tornar vendedor" : "Tornar administrador"}
                          </button>
                          <button type="button" onClick={() => redefinir(m)}>
                            Criar senha provisória
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              acao(
                                () => api.alterarMembro(m.usuario, { ativo: !m.ativo }),
                                m.ativo ? `${m.nome_exibicao} não consegue mais entrar.` : `${m.nome_exibicao} pode entrar de novo.`,
                              )
                            }
                          >
                            {m.ativo ? "Desativar acesso" : "Reativar acesso"}
                          </button>
                          <button type="button" className="perigo" onClick={() => remover(m)}>
                            Remover
                          </button>
                        </div>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <section className="bloco atividade-equipe">
        <div className="alvo-topo">
          <h2>{filtro ? `Atividade de ${nomeDe(filtro)}` : "Atividade recente da equipe"}</h2>
          <label className="campo campo-inline">
            <span>Pessoa</span>
            <select value={filtro} onChange={(e) => setFiltro(e.target.value)}>
              <option value="">Todas</option>
              {membros.map((m) => (
                <option key={m.usuario} value={m.usuario}>
                  {m.nome_exibicao}
                </option>
              ))}
            </select>
          </label>
        </div>
        {atividades.length === 0 ? (
          <p className="mudo">Nenhuma atividade registrada{filtro ? " para essa pessoa" : ""}.</p>
        ) : (
          <ol className="feed">
            {atividades.map((a) => {
              const d = descrever(a, nomeDe);
              return (
                <li key={a.id}>
                  <span className="feed-quem">{nomeDe(a.criado_por)}</span>
                  <span className="feed-oque">
                    {d.titulo}
                    {" em "}
                    <Link to={`/leads/${a.chave}`}>{a.empresa_nome ? titulo(a.empresa_nome) : a.chave}</Link>
                  </span>
                  <time className="mudo" dateTime={a.criado_em}>
                    {dataHora(a.criado_em)}
                  </time>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    </div>
  );
}

function NovaPessoa({ aoCriar, aoCancelar }: { aoCriar: (c: Credencial) => void; aoCancelar: () => void }) {
  const aviso = useAviso();
  const [nome, setNome] = useState("");
  const [usuario, setUsuario] = useState("");
  const [tocouUsuario, setTocouUsuario] = useState(false);
  const [papel, setPapel] = useState<"vendedor" | "admin">("vendedor");
  const [senha, setSenha] = useState(senhaProvisoria);
  const [erro, setErro] = useState<string | null>(null);

  function sugerir(n: string) {
    setNome(n);
    if (!tocouUsuario) {
      const base = n
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .toLowerCase()
        .trim()
        .split(/\s+/);
      setUsuario(base.length > 1 ? `${base[0]}.${base[base.length - 1]}` : (base[0] ?? ""));
    }
  }

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    try {
      const r = await api.criarMembro({ usuario, nome_exibicao: nome, papel, senha_inicial: senha });
      aviso(`${nome} adicionado.`);
      aoCriar({ nome, usuario: r.usuario, senha });
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível adicionar.");
    }
  }

  return (
    <form className="bloco nova-pessoa" onSubmit={enviar}>
      <h2>Adicionar pessoa</h2>
      <div className="grade-form">
        <label className="campo">
          <span>Nome</span>
          <input autoFocus required value={nome} onChange={(e) => sugerir(e.target.value)} placeholder="Ex.: Dani Prado" />
        </label>
        <label className="campo">
          <span>Usuário de acesso</span>
          <input
            required
            value={usuario}
            pattern="[a-z0-9._\-]{2,64}"
            title="Letras minúsculas, números, ponto, hífen ou sublinhado"
            onChange={(e) => {
              setTocouUsuario(true);
              setUsuario(e.target.value.toLowerCase());
            }}
          />
        </label>
        <label className="campo">
          <span>Papel</span>
          <select value={papel} onChange={(e) => setPapel(e.target.value as "vendedor" | "admin")}>
            <option value="vendedor">Vendedor</option>
            <option value="admin">Administrador</option>
          </select>
        </label>
        <label className="campo">
          <span>Senha provisória</span>
          <input required minLength={8} value={senha} onChange={(e) => setSenha(e.target.value)} />
        </label>
      </div>
      <p className="nota">
        {papel === "vendedor"
          ? "Vendedor trabalha os leads livres e os que assumir. Não vê os leads dos colegas."
          : "Administrador vê toda a equipe, transfere leads, edita modelos e cadastra pessoas."}
      </p>
      {erro && (
        <p className="erro-linha" role="alert">
          {erro}
        </p>
      )}
      <div className="acoes">
        <button className="botao botao-primario">Adicionar</button>
        <button type="button" className="botao" onClick={aoCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
