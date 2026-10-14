import { useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { data, dataHora, reais, ROTULO_CANAL, ROTULO_ESTAGIO } from "../formato";
import { useSessao } from "../sessao";
import type { Atividade, Estagio } from "../tipos";
import { useAviso } from "./Aviso";

export function descrever(a: Atividade, nomeDe: (u: string | null) => string = (u) => u ?? "ninguém"): { titulo: string; corpo?: string | null } {
  switch (a.tipo) {
    case "estagio": {
      const [de, para] = (a.assunto ?? "").split("→") as [Estagio, Estagio];
      return {
        titulo: de === para ? "Observação sobre o estágio" : `Estágio: ${ROTULO_ESTAGIO[de] ?? de} para ${ROTULO_ESTAGIO[para] ?? para}`,
        corpo: a.texto,
      };
    }
    case "contato":
      return {
        titulo: `${a.canal ? ROTULO_CANAL[a.canal] : "Contato"} para ${a.destino ?? "—"}`,
        corpo: a.assunto ? `${a.assunto}\n\n${a.texto ?? ""}` : a.texto,
      };
    case "agente":
      return { titulo: `Pergunta ao agente: ${a.assunto ?? ""}`, corpo: a.texto };
    case "supressao":
      return { titulo: "Marcada para não contatar", corpo: a.texto };
    case "cadastro":
      return { titulo: "Lead cadastrado pela equipe", corpo: a.texto };
    case "dados":
      return { titulo: "Dados do cadastro alterados", corpo: a.texto ? `Campos: ${a.texto}` : null };
    case "tarefa": {
      const [quando, quem] = (a.assunto ?? "").split("|");
      return { titulo: `Próximo passo para ${data(quando)}${quem ? ` (${nomeDe(quem)})` : ""}`, corpo: a.texto };
    }
    case "tarefa_concluida":
      return { titulo: "Próximo passo concluído", corpo: a.texto };
    case "venda":
      return { titulo: `Venda registrada: ${reais(Number(a.assunto))}`, corpo: a.texto };
    case "venda_estornada":
      return { titulo: `Venda estornada: ${reais(Number(a.assunto))}`, corpo: a.texto ? `Motivo: ${a.texto}` : null };
    case "responsavel": {
      const [de, para] = (a.assunto ?? "").split("→").map((x) => (x === "—" ? null : x));
      if (!para) return { titulo: `Lead liberado (era de ${nomeDe(de)})` };
      if (!de) return { titulo: `${nomeDe(para)} assumiu o lead` };
      return { titulo: `Lead passado de ${nomeDe(de)} para ${nomeDe(para)}` };
    }
    default:
      return { titulo: "Nota", corpo: a.texto };
  }
}

export default function LinhaDoTempo({ chave, atividades, aoMudar }: { chave: string; atividades: Atividade[]; aoMudar: () => void }) {
  const aviso = useAviso();
  const { nomeDe } = useSessao();
  const [nota, setNota] = useState("");
  const [abertas, setAbertas] = useState<Set<number>>(new Set());

  async function salvar(e: FormEvent) {
    e.preventDefault();
    if (!nota.trim()) return;
    try {
      await api.nota(chave, nota.trim());
      setNota("");
      aoMudar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível salvar a nota.", "erro");
    }
  }

  return (
    <section className="bloco">
      <h2>Histórico</h2>
      <form className="nota-form" onSubmit={salvar}>
        <label className="oculto" htmlFor="nova-nota">
          Nova nota
        </label>
        <textarea
          id="nova-nota"
          rows={2}
          placeholder="Anote o que aconteceu: quem atendeu, o que pediram, próximo passo"
          value={nota}
          onChange={(e) => setNota(e.target.value)}
        />
        <button className="botao" disabled={!nota.trim()}>
          Salvar nota
        </button>
      </form>
      {atividades.length === 0 ? (
        <p className="mudo">Nenhuma atividade ainda. O primeiro contato aparece aqui.</p>
      ) : (
        <ol className="tempo">
          {atividades.map((a) => {
            const d = descrever(a, nomeDe);
            const longo = (d.corpo?.length ?? 0) > 220;
            const aberta = abertas.has(a.id);
            return (
              <li key={a.id} className={`tempo-item tempo-${a.tipo}`}>
                <div className="tempo-topo">
                  <strong>{d.titulo}</strong>
                  <span className="mudo">
                    {dataHora(a.criado_em)}, {nomeDe(a.criado_por)}
                  </span>
                </div>
                {d.corpo && <p className={`tempo-corpo${longo && !aberta ? " cortado" : ""}`}>{d.corpo}</p>}
                {longo && (
                  <button
                    type="button"
                    className="botao-texto"
                    onClick={() =>
                      setAbertas((s) => {
                        const n = new Set(s);
                        if (n.has(a.id)) n.delete(a.id);
                        else n.add(a.id);
                        return n;
                      })
                    }
                  >
                    {aberta ? "Mostrar menos" : "Mostrar tudo"}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
