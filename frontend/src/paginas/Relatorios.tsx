import { useEffect, useState } from "react";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import GraficoSemanas from "../componentes/GraficoSemanas";
import { data, hojeIso, reais, ROTULO_CANAL, ROTULO_ESTAGIO } from "../formato";
import { useSessao } from "../sessao";
import { ESTAGIOS, type Canal, type Perfil, type Relatorio } from "../tipos";

function presets(): { id: string; rotulo: string; de: string; ate: string }[] {
  const h = new Date();
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const inicioMes = new Date(h.getFullYear(), h.getMonth(), 1);
  const inicioMesPassado = new Date(h.getFullYear(), h.getMonth() - 1, 1);
  const fimMesPassado = new Date(h.getFullYear(), h.getMonth(), 0);
  return [
    { id: "mes", rotulo: "Este mês", de: iso(inicioMes), ate: hojeIso() },
    { id: "passado", rotulo: "Mês passado", de: iso(inicioMesPassado), ate: iso(fimMesPassado) },
    { id: "30", rotulo: "Últimos 30 dias", de: hojeIso(-29), ate: hojeIso() },
    { id: "ano", rotulo: "Este ano", de: `${h.getFullYear()}-01-01`, ate: hojeIso() },
  ];
}

const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "—");

export default function Relatorios() {
  const { usuario } = useSessao();
  const aviso = useAviso();
  const opcoes = presets();
  const [de, setDe] = useState(opcoes[0].de);
  const [ate, setAte] = useState(opcoes[0].ate);
  const [rel, setRel] = useState<Relatorio | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [perfis, setPerfis] = useState<Perfil[]>([]);
  useEffect(() => {
    api.perfis().then(setPerfis, () => undefined);
  }, []);

  useEffect(() => {
    if (!de || !ate) return;
    api.relatorio(de, ate).then(
      (r) => {
        setRel(r);
        setErro(null);
      },
      (e) => setErro(e instanceof ApiErro ? e.message : "Não foi possível montar o relatório."),
    );
  }, [de, ate]);

  async function exportar(tipo: "vendas" | "atividades" | "tarefas") {
    try {
      await api.exportarRelatorio(tipo, de, ate);
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível exportar.", "erro");
    }
  }

  const ativo = opcoes.find((o) => o.de === de && o.ate === ate)?.id;
  const t = rel?.totais;

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Relatórios</h1>
          <p className="sub">
            O que a equipe da {usuario.empresa?.nome} fez e vendeu no período. Vendas estornadas ficam de fora.
          </p>
        </div>
        <div className="cabecalho-acoes">
          <button type="button" className="botao" onClick={() => exportar("vendas")}>
            Vendas (CSV)
          </button>
          <button type="button" className="botao" onClick={() => exportar("atividades")}>
            Atividades (CSV)
          </button>
          <button type="button" className="botao" onClick={() => exportar("tarefas")}>
            Próximos passos (CSV)
          </button>
        </div>
      </header>

      <div className="filtros periodo">
        <div className="origens" role="group" aria-label="Período">
          {opcoes.map((o) => (
            <button
              key={o.id}
              type="button"
              className={ativo === o.id ? "ativa" : ""}
              aria-pressed={ativo === o.id}
              onClick={() => {
                setDe(o.de);
                setAte(o.ate);
              }}
            >
              {o.rotulo}
            </button>
          ))}
        </div>
        <label className="campo campo-curto">
          <span>De</span>
          <input type="date" value={de} max={ate} onChange={(e) => setDe(e.target.value)} />
        </label>
        <label className="campo campo-curto">
          <span>Até</span>
          <input type="date" value={ate} min={de} onChange={(e) => setAte(e.target.value)} />
        </label>
      </div>

      {erro && (
        <div className="faixa faixa-erro" role="alert">
          {erro}
        </div>
      )}

      {rel && t && (
        <>
          <section className="numeros" aria-label="Totais do período">
            <div className="numero numero-principal">
              <span className="numero-valor">{reais(t.vendas_centavos)}</span>
              <span className="numero-rotulo">
                em {t.vendas_qtd} {t.vendas_qtd === 1 ? "venda" : "vendas"}, ticket médio {reais(t.ticket_medio_centavos)}
              </span>
            </div>
            <div className="numero">
              <span className="numero-valor">{t.novos_clientes}</span>
              <span className="numero-rotulo">novos clientes</span>
            </div>
            <div className="numero">
              <span className="numero-valor">{t.leads_trabalhados}</span>
              <span className="numero-rotulo">leads trabalhados</span>
            </div>
            <div className="numero">
              <span className="numero-valor">{t.contatos}</span>
              <span className="numero-rotulo">contatos feitos</span>
            </div>
            <div className="numero">
              <span className="numero-valor">{pct(t.respostas, t.leads_trabalhados)}</span>
              <span className="numero-rotulo">responderam ({t.respostas})</span>
            </div>
            <div className={`numero${t.tarefas_atrasadas ? " numero-alerta" : ""}`}>
              <span className="numero-valor">{t.tarefas_atrasadas}</span>
              <span className="numero-rotulo">passos atrasados agora</span>
            </div>
          </section>

          <section className="bloco">
            <h2>Vendas por semana</h2>
            <p className="nota">
              {data(rel.periodo.de)} a {data(rel.periodo.ate)}. Passe o mouse numa coluna para ver o valor e a quantidade.
            </p>
            {t.vendas_qtd ? <GraficoSemanas semanas={rel.vendas_por_semana} /> : <p className="mudo">Nenhuma venda no período.</p>}
          </section>

          <section className="bloco">
            <h2>Por pessoa</h2>
            {rel.por_pessoa.length === 0 ? (
              <p className="mudo">Ninguém registrou atividade no período.</p>
            ) : (
              <div className="tabela-rolagem">
                <table className="tabela tabela-compacta">
                  <thead>
                    <tr>
                      <th>Pessoa</th>
                      <th className="num">Contatos</th>
                      <th className="num">Leads trabalhados</th>
                      <th className="num">Responderam</th>
                      <th className="num">Novos clientes</th>
                      <th className="num">Vendas</th>
                      <th className="num">Valor vendido</th>
                      <th className="num">Passos feitos</th>
                      <th className="num">Atrasados</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rel.por_pessoa.map((p) => (
                      <tr key={p.usuario}>
                        <td>{p.nome_exibicao}</td>
                        <td className="num">{p.contatos}</td>
                        <td className="num">{p.leads_trabalhados}</td>
                        <td className="num">{p.respostas}</td>
                        <td className="num">{p.novos_clientes}</td>
                        <td className="num">{p.vendas_qtd}</td>
                        <td className="num">{reais(p.vendas_centavos)}</td>
                        <td className="num">{p.tarefas_concluidas}</td>
                        <td className={`num${p.tarefas_atrasadas ? " texto-alerta" : ""}`}>{p.tarefas_atrasadas}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="duas-colunas">
            <section className="bloco">
              <h2>Por canal</h2>
              <p className="nota">Viraram cliente: leads cujo primeiro contato foi por esse canal e que hoje são clientes.</p>
              <table className="tabela tabela-compacta">
                <thead>
                  <tr>
                    <th>Canal</th>
                    <th className="num">Contatos</th>
                    <th className="num">Leads</th>
                    <th className="num">Viraram cliente</th>
                  </tr>
                </thead>
                <tbody>
                  {rel.por_canal.map((c) => (
                    <tr key={c.canal}>
                      <td>{ROTULO_CANAL[c.canal as Canal] ?? c.canal}</td>
                      <td className="num">{c.contatos}</td>
                      <td className="num">{c.leads}</td>
                      <td className="num">
                        {c.viraram_cliente} <span className="mudo">({pct(c.viraram_cliente, c.leads)})</span>
                      </td>
                    </tr>
                  ))}
                  {!rel.por_canal.length && (
                    <tr>
                      <td colSpan={4} className="mudo">
                        Nenhum contato no período.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </section>

            <section className="bloco">
              <h2>Por perfil</h2>
              <table className="tabela tabela-compacta">
                <thead>
                  <tr>
                    <th>Perfil</th>
                    <th className="num">Leads trabalhados</th>
                    <th className="num">Novos clientes</th>
                    <th className="num">Valor vendido</th>
                  </tr>
                </thead>
                <tbody>
                  {rel.por_perfil.map((p) => (
                    <tr key={p.perfil_id}>
                      <td>{p.perfil_id === "manual" ? "Cadastrados pela equipe" : (perfis.find((x) => x.id === p.perfil_id)?.nome ?? p.perfil_id)}</td>
                      <td className="num">{p.leads_trabalhados}</td>
                      <td className="num">{p.novos_clientes}</td>
                      <td className="num">{reais(p.vendas_centavos)}</td>
                    </tr>
                  ))}
                  {!rel.por_perfil.length && (
                    <tr>
                      <td colSpan={4} className="mudo">
                        Nada no período.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </section>
          </div>

          <section className="bloco">
            <h2>Onde estão os leads trabalhados agora</h2>
            <p className="nota">Todos os leads em que a equipe já mexeu, independentemente do período.</p>
            <dl className="funil-atual">
              {ESTAGIOS.filter((e) => e !== "novo").map((e) => (
                <div key={e} style={{ ["--cor" as string]: `var(--e-${e})` }}>
                  <dt>{ROTULO_ESTAGIO[e]}</dt>
                  <dd>{rel.funil[e]}</dd>
                </div>
              ))}
            </dl>
          </section>
        </>
      )}
    </div>
  );
}
