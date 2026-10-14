import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import Estagio from "../componentes/Estagio";
import { data, hojeIso, quandoVence, titulo } from "../formato";
import { useSessao } from "../sessao";
import type { Tarefa } from "../tipos";

const GRUPOS: { id: string; rotulo: string; teste: (t: Tarefa, hoje: string, semana: string) => boolean }[] = [
  { id: "atrasados", rotulo: "Atrasados", teste: (t, h) => t.vence_em < h },
  { id: "hoje", rotulo: "Hoje", teste: (t, h) => t.vence_em === h },
  { id: "semana", rotulo: "Próximos 7 dias", teste: (t, h, s) => t.vence_em > h && t.vence_em <= s },
  { id: "depois", rotulo: "Mais adiante", teste: (t, _h, s) => t.vence_em > s },
];

export default function Agenda() {
  const { eAdmin, pessoas, usuario, nomeDe } = useSessao();
  const aviso = useAviso();
  const [quem, setQuem] = useState("eu");
  const [tarefas, setTarefas] = useState<Tarefa[]>([]);
  const [concluidas, setConcluidas] = useState<Tarefa[] | null>(null);
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(() => {
    setCarregando(true);
    api
      .tarefas(quem)
      .then(setTarefas, (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível carregar a agenda.", "erro"))
      .finally(() => setCarregando(false));
    setConcluidas(null);
  }, [quem, aviso]);

  useEffect(carregar, [carregar]);

  async function alternar(t: Tarefa, concluida: boolean) {
    try {
      await api.alterarTarefa(t.id, { concluida });
      aviso(concluida ? "Feito. Bom trabalho." : "Próximo passo reaberto.");
      carregar();
      window.dispatchEvent(new Event("crm:agenda-mudou"));
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível alterar.", "erro");
    }
  }

  async function adiar(t: Tarefa) {
    try {
      await api.alterarTarefa(t.id, { vence_em: hojeIso(1) });
      aviso("Passado para amanhã.");
      carregar();
      window.dispatchEvent(new Event("crm:agenda-mudou"));
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível alterar.", "erro");
    }
  }

  const hoje = hojeIso();
  const semana = hojeIso(7);
  const daEquipe = quem !== "eu";

  return (
    <div className="pagina pagina-media">
      <header className="cabecalho">
        <div>
          <h1>Agenda</h1>
          <p className="sub">
            {daEquipe
              ? `Próximos passos ${quem === "todos" ? "de toda a equipe" : `de ${nomeDe(quem)}`}.`
              : "Os próximos passos que estão com você. Marque quando fizer; o histórico do lead registra."}
          </p>
        </div>
        {eAdmin && (
          <label className="campo campo-inline">
            <span>Ver</span>
            <select value={quem} onChange={(e) => setQuem(e.target.value)}>
              <option value="eu">A minha agenda</option>
              <option value="todos">Toda a equipe</option>
              {pessoas
                .filter((p) => p.usuario !== usuario.usuario)
                .map((p) => (
                  <option key={p.usuario} value={p.usuario}>
                    {p.nome_exibicao}
                  </option>
                ))}
            </select>
          </label>
        )}
      </header>

      {!carregando && tarefas.length === 0 && (
        <div className="bloco vazio-lateral">
          <p>
            Nada pendente. Para agendar, abra um lead e use <strong>Próximos passos</strong>.{" "}
            <Link to="/leads?ordenar=atividade">Ver leads com atividade recente</Link>
          </p>
        </div>
      )}

      {GRUPOS.map((g) => {
        const itens = tarefas.filter((t) => g.teste(t, hoje, semana));
        if (!itens.length) return null;
        return (
          <section key={g.id} className={`grupo-agenda grupo-${g.id}`}>
            <h2>
              {g.rotulo} <span className="mudo">{itens.length}</span>
            </h2>
            <ul className="agenda">
              {itens.map((t) => {
                const q = quandoVence(t.vence_em);
                return (
                  <li key={t.id}>
                    <input
                      type="checkbox"
                      onChange={() => alternar(t, true)}
                      aria-label={`Concluir: ${t.titulo}`}
                      disabled={t.responsavel !== usuario.usuario && !eAdmin}
                    />
                    <div className="agenda-corpo">
                      <strong>{t.titulo}</strong>
                      <span className="empresa-meta">
                        <Link to={`/leads/${t.chave}`}>{titulo(t.lead_nome)}</Link>
                        {t.municipio && (
                          <span>
                            {titulo(t.municipio)}
                            {t.uf && `/${t.uf}`}
                          </span>
                        )}
                        {t.estagio && <Estagio valor={t.estagio} />}
                        {daEquipe && <span>com {nomeDe(t.responsavel)}</span>}
                      </span>
                    </div>
                    <span className={`vence${q.atrasada ? " atrasada" : q.hoje ? " hoje" : ""}`}>{q.texto}</span>
                    {(g.id === "atrasados" || g.id === "hoje") && (
                      <button type="button" className="botao-texto" onClick={() => adiar(t)}>
                        Amanhã
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}

      <div className="acoes">
        {concluidas === null ? (
          <button type="button" className="botao-texto" onClick={() => api.tarefas(quem, "concluidas").then(setConcluidas)}>
            Ver concluídos
          </button>
        ) : (
          <section className="grupo-agenda">
            <h2>
              Concluídos <span className="mudo">{concluidas.length}</span>
            </h2>
            <ul className="agenda concluidas">
              {concluidas.slice(0, 50).map((t) => (
                <li key={t.id}>
                  <input type="checkbox" checked onChange={() => alternar(t, false)} aria-label={`Reabrir: ${t.titulo}`} />
                  <div className="agenda-corpo">
                    <strong>{t.titulo}</strong>
                    <span className="empresa-meta">
                      <Link to={`/leads/${t.chave}`}>{titulo(t.lead_nome)}</Link>
                      <span>
                        feito em {data(t.concluida_em)} por {nomeDe(t.concluida_por)}
                      </span>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
