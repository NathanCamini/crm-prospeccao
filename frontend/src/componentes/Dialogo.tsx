import { useEffect, useRef, type ReactNode } from "react";

/** Janela modal nativa (<dialog>): foco preso, Esc fecha, fundo escurecido. */
export default function Dialogo({ aberto, titulo, aoFechar, children }: { aberto: boolean; titulo: string; aoFechar: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (aberto && !d.open) d.showModal();
    if (!aberto && d.open) d.close();
  }, [aberto]);
  return (
    <dialog ref={ref} className="dialogo" onClose={aoFechar} aria-labelledby="dialogo-titulo">
      <h2 id="dialogo-titulo">{titulo}</h2>
      {aberto && children}
    </dialog>
  );
}
