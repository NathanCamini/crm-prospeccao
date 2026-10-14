import { ROTULO_ESTAGIO } from "../formato";
import { ESTAGIOS, type Estagio, type Resumo } from "../tipos";

interface Props {
  resumo: Resumo | null;
  ativo: Estagio | undefined;
  aoEscolher: (e: Estagio | undefined) => void;
}

/** Contagem por estágio; a barra embaixo é proporcional e cada coluna filtra a tabela. */
export default function Funil({ resumo, ativo, aoEscolher }: Props) {
  const total = resumo?.total ?? 0;
  return (
    <div className="funil" role="group" aria-label="Filtrar por estágio">
      <button
        type="button"
        className={`funil-col funil-todos${ativo ? "" : " ativo"}`}
        aria-pressed={!ativo}
        onClick={() => aoEscolher(undefined)}
      >
        <span className="funil-num">{resumo ? total : "–"}</span>
        <span className="funil-rot">Todas</span>
      </button>
      {ESTAGIOS.map((e) => {
        const n = resumo?.por_estagio[e] ?? 0;
        return (
          <button
            key={e}
            type="button"
            className={`funil-col${ativo === e ? " ativo" : ""}${n === 0 ? " vazio" : ""}`}
            aria-pressed={ativo === e}
            onClick={() => aoEscolher(ativo === e ? undefined : e)}
            style={{ ["--cor" as string]: `var(--e-${e})` }}
          >
            <span className="funil-num">{resumo ? n : "–"}</span>
            <span className="funil-rot">{ROTULO_ESTAGIO[e]}</span>
          </button>
        );
      })}
      <div className="funil-barra" aria-hidden="true">
        {total > 0 &&
          ESTAGIOS.map((e) => {
            const n = resumo?.por_estagio[e] ?? 0;
            return n ? (
              <span
                key={e}
                className={ativo && ativo !== e ? "apagado" : ""}
                style={{ flexGrow: n, background: `var(--e-${e})` }}
              />
            ) : null;
          })}
      </div>
    </div>
  );
}
