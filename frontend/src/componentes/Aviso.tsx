import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type Tipo = "ok" | "erro";
interface Aviso {
  texto: string;
  tipo: Tipo;
}

const Contexto = createContext<(texto: string, tipo?: Tipo) => void>(() => undefined);

export function AvisoProvider({ children }: { children: ReactNode }) {
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const timer = useRef<number>();
  const mostrar = useCallback((texto: string, tipo: Tipo = "ok") => {
    window.clearTimeout(timer.current);
    setAviso({ texto, tipo });
    timer.current = window.setTimeout(() => setAviso(null), tipo === "erro" ? 7000 : 4000);
  }, []);
  return (
    <Contexto.Provider value={mostrar}>
      {children}
      <div className="aviso-area" role="status" aria-live="polite">
        {aviso && <div className={`aviso aviso-${aviso.tipo}`}>{aviso.texto}</div>}
      </div>
    </Contexto.Provider>
  );
}

export const useAviso = () => useContext(Contexto);
