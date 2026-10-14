import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { dataHora, relativo } from "../formato";
import { useSessao } from "../sessao";
import type { EmpresaCliente } from "../tipos";
import { AvisoCredencial, senhaProvisoria } from "./Equipe";

/** Empresas que usam o sistema. Só o administrador do sistema vê esta tela. */
export default function Clientes() {
  const { usuario, atualizarUsuario } = useSessao();
  const aviso = useAviso();
  const navegar = useNavigate();
  const [empresas, setEmpresas] = useState<EmpresaCliente[]>([]);
  const [editando, setEditando] = useState<EmpresaCliente | "nova" | null>(null);
  const [credencial, setCredencial] = useState<{ nome: string; usuario: string; senha: string } | null>(null);

  const carregar = useCallback(() => {
    api.empresasClientes().then(setEmpresas, () => aviso("Não foi possível carregar as empresas.", "erro"));
  }, [aviso]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function entrarNa(e: EmpresaCliente) {
    try {
      atualizarUsuario(await api.escolherEmpresa(e.id));
      navegar("/leads");
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível entrar.", "erro");
    }
  }

  async function depoisDeSalvar(cred?: { nome: string; usuario: string; senha: string }) {
    setEditando(null);
    carregar();
    atualizarUsuario(await api.eu());
    if (cred) setCredencial(cred);
  }

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Clientes</h1>
          <p className="sub">
            Empresas que usam o sistema. Cada uma tem a própria equipe, os próprios leads e o próprio histórico; uma não vê a outra.
          </p>
        </div>
        {editando === null && (
          <button type="button" className="botao botao-primario" onClick={() => setEditando("nova")}>
            Cadastrar empresa
          </button>
        )}
      </header>

      {credencial && <AvisoCredencial c={credencial} aoFechar={() => setCredencial(null)} />}
      {editando !== null && (
        <FormEmpresa
          empresa={editando === "nova" ? null : editando}
          aoCancelar={() => setEditando(null)}
          aoSalvar={depoisDeSalvar}
        />
      )}

      <PainelSincronizacao />

      {empresas.length === 0 && editando === null ? (
        <div className="bloco vazio-lateral">
          <p>Nenhuma empresa cliente ainda. Cadastre a primeira e o administrador dela, que depois adiciona a própria equipe.</p>
        </div>
      ) : (
        <div className="tabela-area">
          <table className="tabela">
            <thead>
              <tr>
                <th>Empresa</th>
                <th>Produtos</th>
                <th>Administradores</th>
                <th className="num">Pessoas</th>
                <th className="num">Em trabalho</th>
                <th className="num">Clientes fechados</th>
                <th className="num">Contatos 7 dias</th>
                <th>Última atividade</th>
                <th>
                  <span className="oculto">Ações</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {empresas.map((e) => (
                <tr key={e.id} className={`sem-clique${e.ativa ? "" : " inativa"}`}>
                  <td>
                    <span className="empresa-nome">{e.nome}</span>
                    <span className="empresa-meta">
                      {e.documento}
                      {usuario.empresa?.id === e.id && <span className="marcador marcador-papel">atual</span>}
                      {!e.ativa && <span className="marcador marcador-alerta">desativada</span>}
                    </span>
                  </td>
                  <td>{e.produtos ?? <span className="mudo">nenhum ainda</span>}</td>
                  <td>{e.admins ?? <span className="mudo">nenhum</span>}</td>
                  <td className="num">{e.pessoas}</td>
                  <td className="num">{e.leads_trabalhados}</td>
                  <td className="num">{e.clientes}</td>
                  <td className="num">{e.contatos_7d}</td>
                  <td className="mudo nowrap">{relativo(e.ultima_atividade)}</td>
                  <td className="direita acoes-linha">
                    {e.ativa && (
                      <button type="button" className="botao-texto" onClick={() => entrarNa(e)}>
                        Entrar
                      </button>
                    )}
                    <button type="button" className="botao-texto" onClick={() => setEditando(e)}>
                      Editar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Cópia local dos leads do módulo 3: quando rodou, o que trouxe, e um botão para rodar agora. */
function PainelSincronizacao() {
  const aviso = useAviso();
  const [dados, setDados] = useState<Awaited<ReturnType<typeof api.sincronizacao>> | null>(null);
  const [rodando, setRodando] = useState(false);
  const carregar = useCallback(() => {
    api.sincronizacao().then(setDados, () => setDados(null));
  }, []);
  useEffect(carregar, [carregar]);

  async function agora() {
    setRodando(true);
    try {
      const r = await api.sincronizar();
      aviso(`Sincronizado: ${r.novos} novos, ${r.atualizados} atualizados, ${r.ausentes} saíram da base.`);
      carregar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "A sincronização falhou.", "erro");
    } finally {
      setRodando(false);
    }
  }

  const ultima = dados?.execucoes[0];
  return (
    <section className="bloco sincronizacao">
      <div>
        <h2>Leads do agente</h2>
        <p className="sub">
          {dados ? (
            <>
              {dados.leads.do_agente ?? 0} leads copiados do módulo 3
              {dados.leads.fora_da_base ? `, ${dados.leads.fora_da_base} já fora da base` : ""}, e {dados.leads.manuais ?? 0} cadastrados
              pelas equipes.{" "}
              {ultima &&
                (ultima.erro ? (
                  <span className="texto-alerta">Última tentativa falhou {relativo(ultima.inicio)}: {ultima.erro}</span>
                ) : (
                  <>Última sincronização em {dataHora(ultima.fim)}.</>
                ))}
            </>
          ) : (
            "Carregando…"
          )}
        </p>
      </div>
      <button type="button" className="botao" onClick={agora} disabled={rodando}>
        {rodando ? "Sincronizando…" : "Sincronizar agora"}
      </button>
    </section>
  );
}

function FormEmpresa({
  empresa,
  aoSalvar,
  aoCancelar,
}: {
  empresa: EmpresaCliente | null;
  aoSalvar: (cred?: { nome: string; usuario: string; senha: string }) => void;
  aoCancelar: () => void;
}) {
  const [nome, setNome] = useState(empresa?.nome ?? "");
  const [documento, setDocumento] = useState(empresa?.documento ?? "");
  const [ativa, setAtiva] = useState(empresa?.ativa ?? true);
  const [adminNome, setAdminNome] = useState("");
  const [adminUsuario, setAdminUsuario] = useState("");
  const [senha] = useState(senhaProvisoria);
  const [erro, setErro] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    try {
      if (empresa) {
        if (empresa.ativa && !ativa && !window.confirm(`Desativar ${empresa.nome}? Todas as pessoas dela saem do sistema na hora.`)) return;
        await api.alterarEmpresaCliente(empresa.id, { nome, documento, ativa });
        aoSalvar();
      } else {
        const r = await api.criarEmpresaCliente({
          nome,
          documento: documento || undefined,
          perfis: [],
          admin: { usuario: adminUsuario, nome_exibicao: adminNome, senha_inicial: senha },
        });
        aoSalvar({ nome: adminNome, usuario: r.admin, senha });
      }
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível salvar.");
    }
  }

  return (
    <form className="bloco nova-pessoa" onSubmit={enviar}>
      <h2>{empresa ? `Editar ${empresa.nome}` : "Cadastrar empresa cliente"}</h2>
      <div className="grade-form">
        <label className="campo">
          <span>Nome da empresa</span>
          <input autoFocus required value={nome} onChange={(e) => setNome(e.target.value)} />
        </label>
        <label className="campo">
          <span>CNPJ (opcional)</span>
          <input value={documento} onChange={(e) => setDocumento(e.target.value)} />
        </label>
      </div>

      <p className="nota">
        Os leads desta empresa vêm dos produtos que o administrador dela descrever em <strong>Produtos</strong>: o
        agente transforma cada descrição no filtro de empresas novas.
      </p>

      {empresa ? (
        <label className="marcar">
          <input type="checkbox" checked={ativa} onChange={(e) => setAtiva(e.target.checked)} />
          <span>Empresa ativa (desmarcar tira o acesso de todas as pessoas dela)</span>
        </label>
      ) : (
        <fieldset className="perfis">
          <legend>Administrador da empresa</legend>
          <div className="grade-form">
            <label className="campo">
              <span>Nome</span>
              <input required value={adminNome} onChange={(e) => setAdminNome(e.target.value)} />
            </label>
            <label className="campo">
              <span>Usuário de acesso</span>
              <input
                required
                pattern="[a-z0-9._\-]{2,64}"
                title="Letras minúsculas, números, ponto, hífen ou sublinhado"
                value={adminUsuario}
                onChange={(e) => setAdminUsuario(e.target.value.toLowerCase())}
              />
            </label>
          </div>
          <p className="nota">Uma senha provisória é gerada ao salvar. O administrador troca no primeiro acesso e cadastra a própria equipe.</p>
        </fieldset>
      )}

      {erro && (
        <p className="erro-linha" role="alert">
          {erro}
        </p>
      )}
      <div className="acoes">
        <button className="botao botao-primario">{empresa ? "Salvar" : "Cadastrar empresa"}</button>
        <button type="button" className="botao" onClick={aoCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
