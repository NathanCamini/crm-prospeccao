import { ROTULO_ESTAGIO } from "../formato";
import type { Estagio as E } from "../tipos";

export default function Estagio({ valor }: { valor: E }) {
  return (
    <span className="estagio" style={{ ["--cor" as string]: `var(--e-${valor})` }}>
      {ROTULO_ESTAGIO[valor]}
    </span>
  );
}

export function Pontuacao({ valor }: { valor: number }) {
  const nivel = valor >= 85 ? "alto" : valor >= 70 ? "medio" : "baixo";
  return (
    <span className={`pontuacao pontuacao-${nivel}`} title={`Pontuação do agente: ${valor} de 100`}>
      {valor}
    </span>
  );
}
