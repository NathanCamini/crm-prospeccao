import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";

interface Mensagem {
  autor: "voce" | "agente" | "erro";
  texto: string;
}

const SUGESTOES = [
  "Quais pousadas de Gramado abriram neste mês com capital acima de 200 mil?",
  "Quais empresas da base são unidades de rede?",
  "Resuma os alvos de Santa Catarina por município.",
];

const CHAVE = "crm:conversa-agente";

function carregarConversa(): Mensagem[] {
  try {
    return JSON.parse(sessionStorage.getItem(CHAVE) ?? "[]");
  } catch {
    return [];
  }
}

export default function Agente() {
  const [conversa, setConversa] = useState<Mensagem[]>(carregarConversa);
  const [pergunta, setPergunta] = useState("");
  const [esperando, setEsperando] = useState(false);
  const fim = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(CHAVE, JSON.stringify(conversa.slice(-40)));
    } catch {
      /* armazenamento indisponível: a conversa só não sobrevive ao recarregar */
    }
    fim.current?.scrollIntoView({ block: "end" });
  }, [conversa]);

  async function enviar(texto: string, e?: FormEvent) {
    e?.preventDefault();
    const p = texto.trim();
    if (!p || esperando) return;
    setConversa((c) => [...c, { autor: "voce", texto: p }]);
    setPergunta("");
    setEsperando(true);
    try {
      const r = await api.perguntar(p);
      setConversa((c) => [...c, { autor: "agente", texto: r.resposta }]);
    } catch (err) {
      setConversa((c) => [...c, { autor: "erro", texto: err instanceof ApiErro ? err.message : "O agente não respondeu." }]);
    } finally {
      setEsperando(false);
    }
  }

  return (
    <div className="pagina pagina-estreita">
      <header className="cabecalho">
        <div>
          <h1>Perguntar ao agente</h1>
          <p className="sub">A pergunta passa pelo módulo 3 até o agente. O CRM não guarda nem processa a base.</p>
        </div>
        {conversa.length > 0 && (
          <button type="button" className="botao-texto" onClick={() => setConversa([])}>
            Limpar conversa
          </button>
        )}
      </header>

      <div className="conversa" aria-live="polite">
        {conversa.length === 0 && (
          <div className="sugestoes">
            <p className="mudo">Experimente começar por uma destas:</p>
            {SUGESTOES.map((s) => (
              <button key={s} type="button" className="sugestao" onClick={() => enviar(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {conversa.map((m, i) => (
          <div key={i} className={`balao balao-${m.autor}`}>
            {m.texto}
          </div>
        ))}
        {esperando && <div className="balao balao-agente pensando">O agente está analisando…</div>}
        <div ref={fim} />
      </div>

      <form className="conversa-form" onSubmit={(e) => enviar(pergunta, e)}>
        <label className="oculto" htmlFor="pergunta">
          Sua pergunta
        </label>
        <textarea
          id="pergunta"
          rows={2}
          value={pergunta}
          placeholder="Escreva sua pergunta"
          onChange={(e) => setPergunta(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) enviar(pergunta, e);
          }}
        />
        <button className="botao botao-primario" disabled={esperando || !pergunta.trim()}>
          Perguntar
        </button>
      </form>
    </div>
  );
}
