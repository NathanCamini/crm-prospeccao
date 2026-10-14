import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { data, dataHora, mesReferencia } from "../formato";
import type { ExecucaoProduto, FiltroProduto, Produto, PropostaProduto } from "../tipos";

const EXEMPLO =
  "Ex.: Vendemos sistema de gestão para academias e estúdios de pilates. Atendemos o Rio Grande do Sul e Santa Catarina.";

/** Situações em que a tela continua consultando sozinha até a classificação terminar. */
const EM_ANDAMENTO: ExecucaoProduto["situacao"][] = ["na_fila", "classificando"];

export default function Produtos() {
  const aviso = useAviso();
  const [produtos, setProdutos] = useState<Produto[]>([]);
  const [podeEditar, setPodeEditar] = useState(false);
  const [avisoAgente, setAvisoAgente] = useState<string | null>(null);
  const [sincronizando, setSincronizando] = useState(false);
  const [carregado, setCarregado] = useState(false);
  const [editando, setEditando] = useState<{ id?: string; descricao: string } | null>(null);

  const carregar = useCallback(
    () =>
      api.produtos().then(
        (r) => {
          setProdutos(r.itens);
          setPodeEditar(r.pode_editar);
          setAvisoAgente(r.aviso);
          setSincronizando(r.sincronizando);
          setCarregado(true);
        },
        (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível carregar os produtos.", "erro"),
      ),
    [aviso],
  );

  useEffect(() => {
    carregar();
  }, [carregar]);

  const andamento = sincronizando || produtos.some((p) => p.execucao && EM_ANDAMENTO.includes(p.execucao.situacao));
  useEffect(() => {
    if (!andamento) return;
    const id = window.setTimeout(carregar, 5000);
    return () => window.clearTimeout(id);
  }, [andamento, produtos, carregar]);

  async function remover(p: Produto) {
    if (!window.confirm(`Remover "${p.nome}"? Os leads novos dele deixam de chegar; os que a equipe já trabalhou continuam.`)) return;
    try {
      await api.removerProduto(p.id);
      aviso("Produto removido.");
      if (editando?.id === p.id) setEditando(null);
      carregar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível remover.", "erro");
    }
  }

  return (
    <div className="pagina pagina-media">
      <header className="cabecalho">
        <div>
          <h1>Produtos</h1>
          <p className="sub">
            Conte o que a sua empresa vende e para quem. O agente transforma isso no filtro de leads: todo mês, as
            empresas recém-abertas que podem comprar aparecem em Leads, com o motivo.
            {!podeEditar && carregado && " Só o administrador da empresa descreve os produtos."}
          </p>
        </div>
        {podeEditar && !editando && (
          <button type="button" className="botao botao-primario" onClick={() => setEditando({ descricao: "" })}>
            Novo produto
          </button>
        )}
      </header>

      {avisoAgente && <p className="faixa faixa-alerta">{avisoAgente}</p>}

      {editando && (
        <EditorProduto
          key={editando.id ?? "novo"}
          inicial={editando}
          aoSalvar={() => {
            setEditando(null);
            carregar();
          }}
          aoCancelar={() => setEditando(null)}
        />
      )}

      {carregado && !produtos.length && !editando && (
        <section className="bloco vazio-lateral">
          <h2>Nenhum produto ainda</h2>
          <p className="sub">
            {podeEditar
              ? "Descreva o primeiro produto para começar a receber leads."
              : "O administrador da empresa ainda não descreveu o que vocês vendem."}
          </p>
        </section>
      )}

      {produtos.map((p) => (
        <section key={p.id} className="bloco produto">
          <div className="produto-topo">
            <div>
              <h2>{p.nome}</h2>
              <p className="sub">{p.descricao}</p>
            </div>
            {podeEditar && (
              <div className="acoes-linha">
                <button type="button" className="botao" onClick={() => setEditando({ id: p.id, descricao: p.descricao })}>
                  Editar
                </button>
                <button type="button" className="botao-texto perigo" onClick={() => remover(p)}>
                  Remover
                </button>
              </div>
            )}
          </div>
          <Situacao execucao={p.execucao} alvos={p.alvos} sincronizando={sincronizando} />
          {p.filtro && <ResumoFiltro filtro={p.filtro} criterios={p.criterios ?? []} atividades={p.atividades ?? undefined} />}
          <p className="nota">
            Descrito por {p.criado_por} em {data(p.criado_em)}
            {p.versao > 1 && ` · versão ${p.versao}, alterada em ${data(p.atualizado_em)}`}
          </p>
        </section>
      ))}
    </div>
  );
}

function Situacao({ execucao, alvos, sincronizando }: { execucao: ExecucaoProduto | null; alvos: number | null; sincronizando: boolean }) {
  if (!execucao) return <p className="mudo">Aguardando o agente.</p>;
  switch (execucao.situacao) {
    case "na_fila":
      return <p className="pensando">Na fila para buscar leads…</p>;
    case "classificando":
      return <p className="pensando">Buscando leads entre as empresas novas de {mesReferencia(execucao.mes)}…</p>;
    case "aguardando_mes":
      return <p className="mudo">Os leads chegam quando a base do primeiro mês for carregada.</p>;
    case "erro":
      return <p className="faixa faixa-erro">Não foi possível buscar os leads: {execucao.erro ?? "erro no agente"}.</p>;
    default:
      return (
        <p>
          <strong>{alvos ?? execucao.alvos ?? 0} leads</strong> encontrados em {mesReferencia(execucao.mes)}
          <span className="mudo">
            {" "}
            · atualizado em {dataHora(execucao.fim)}
            {sincronizando && " · copiando para a lista de leads…"}
          </span>
        </p>
      );
  }
}

function ResumoFiltro({ filtro, criterios, atividades }: { filtro: FiltroProduto; criterios: string[]; atividades?: PropostaProduto["atividades"] }) {
  const regiao = [...(filtro.municipios ?? []), ...(filtro.ufs ?? [])];
  const codigos = atividades ?? [...(filtro.cnaes ?? []), ...(filtro.cnae_prefixos ?? [])].map((c) => ({ codigo: c, descricao: "" }));
  return (
    <details className="como-procura">
      <summary>Como o agente procura</summary>
      <dl className="dados">
        <dt>Atividades das empresas</dt>
        <dd>
          <ul className="lista-simples">
            {codigos.map((a) => (
              <li key={a.codigo}>
                <span className="tabular">{a.codigo}</span> {a.descricao}
              </li>
            ))}
          </ul>
        </dd>
        <dt>Região</dt>
        <dd>{regiao.length ? regiao.join(", ") : "Brasil todo"}</dd>
        <dt>O que o agente avalia em cada empresa</dt>
        <dd>
          <ul className="lista-simples">
            {criterios.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </dd>
      </dl>
    </details>
  );
}

function EditorProduto({
  inicial,
  aoSalvar,
  aoCancelar,
}: {
  inicial: { id?: string; descricao: string };
  aoSalvar: () => void;
  aoCancelar: () => void;
}) {
  const aviso = useAviso();
  const [descricao, setDescricao] = useState(inicial.descricao);
  const [proposta, setProposta] = useState<PropostaProduto | null>(null);
  const [descricaoDaProposta, setDescricaoDaProposta] = useState("");
  const [ocupado, setOcupado] = useState<"previa" | "salvar" | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  async function previa(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setOcupado("previa");
    try {
      setProposta(await api.sugerirProduto(descricao.trim(), inicial.id));
      setDescricaoDaProposta(descricao.trim());
    } catch (err) {
      setProposta(null);
      setErro(err instanceof ApiErro ? err.message : "O agente não conseguiu montar a prévia.");
    } finally {
      setOcupado(null);
    }
  }

  async function salvar() {
    if (!proposta) return;
    setOcupado("salvar");
    try {
      await api.salvarProduto(descricaoDaProposta, proposta.perfil, inicial.id);
      aviso("Produto salvo. O agente já está buscando os leads do último mês.");
      aoSalvar();
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível salvar.");
    } finally {
      setOcupado(null);
    }
  }

  const desatualizada = proposta !== null && descricao.trim() !== descricaoDaProposta;
  const p = proposta?.previa;

  return (
    <section className="bloco bloco-destaque">
      <h2>{inicial.id ? "Editar produto" : "Novo produto"}</h2>
      <form onSubmit={previa}>
        <label className="campo">
          <span>O que vocês vendem e para que tipo de empresa? Se atendem só uma região, diga qual.</span>
          <textarea
            autoFocus
            rows={4}
            minLength={15}
            maxLength={2000}
            required
            placeholder={EXEMPLO}
            value={descricao}
            onChange={(e) => setDescricao(e.target.value)}
          />
        </label>
        <div className="acoes">
          <button type="submit" className={`botao ${proposta && !desatualizada ? "" : "botao-primario"}`} disabled={ocupado !== null}>
            {ocupado === "previa" ? "O agente está lendo…" : proposta ? "Ver prévia de novo" : "Ver prévia"}
          </button>
          <button type="button" className="botao-texto" onClick={aoCancelar}>
            Cancelar
          </button>
        </div>
      </form>
      {erro && <p className="erro-linha">{erro}</p>}

      {proposta && p && (
        <div className={`proposta ${desatualizada ? "desatualizada" : ""}`}>
          <h3>{proposta.perfil.nome ?? proposta.perfil.produto}</h3>
          {proposta.explicacao && <p className="justificativa">{proposta.explicacao}</p>}
          {p.mes ? (
            <p>
              No último mês ({mesReferencia(p.mes)}), <strong>{p.total} empresas novas</strong> passariam neste filtro.
              O agente avalia cada uma e só as que fazem sentido viram leads.
            </p>
          ) : null}
          {p.aviso && <p className={`faixa ${p.cabe ? "faixa-simulado" : "faixa-alerta"}`}>{p.aviso}</p>}
          {p.exemplos.length > 0 && (
            <table className="tabela tabela-compacta">
              <caption className="nota">Algumas delas</caption>
              <thead>
                <tr>
                  <th>Empresa</th>
                  <th>Cidade</th>
                  <th>Atividade</th>
                  <th>Abertura</th>
                </tr>
              </thead>
              <tbody>
                {p.exemplos.map((ex, i) => (
                  <tr key={i}>
                    <td>{ex.nome}</td>
                    <td>{[ex.municipio, ex.uf].filter(Boolean).join("/")}</td>
                    <td className="mudo">{ex.atividade}</td>
                    <td className="nowrap">{data(ex.data_inicio_atividade)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ResumoFiltro filtro={proposta.perfil.filtro_sql} criterios={proposta.perfil.criterios_llm} atividades={proposta.atividades} />
          {desatualizada && <p className="nota">Você mudou a descrição: veja a prévia de novo antes de salvar.</p>}
          <div className="acoes">
            <button
              type="button"
              className="botao botao-primario"
              onClick={salvar}
              disabled={ocupado !== null || !p.cabe || desatualizada}
            >
              {ocupado === "salvar" ? "Salvando…" : "Salvar e buscar leads"}
            </button>
          </div>
          <p className="nota">Não ficou como você esperava? Ajuste a descrição (ramo, região, porte) e veja a prévia de novo.</p>
        </div>
      )}
    </section>
  );
}
