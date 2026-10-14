import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "./api";
import type { Pessoa, Usuario } from "./tipos";

interface Sessao {
  usuario: Usuario;
  eAdmin: boolean;
  eSuper: boolean;
  /** Pessoas da empresa ativa, para mostrar nomes e filtrar por responsável. */
  pessoas: Pessoa[];
  nomeDe: (usuario: string | null | undefined) => string;
  atualizarUsuario: (u: Usuario) => void;
}

const Contexto = createContext<Sessao | null>(null);

export function SessaoProvider({ usuario, aoMudar, children }: { usuario: Usuario; aoMudar: (u: Usuario) => void; children: ReactNode }) {
  const [pessoas, setPessoas] = useState<Pessoa[]>([]);
  const empresaId = usuario.empresa?.id;

  useEffect(() => {
    if (!empresaId) {
      setPessoas([]);
      return;
    }
    api.nomes().then(setPessoas, () => setPessoas([]));
  }, [empresaId]);

  const valor = useMemo<Sessao>(() => {
    const mapa = new Map(pessoas.map((p) => [p.usuario, p.nome_exibicao]));
    return {
      usuario,
      eAdmin: usuario.papel !== "vendedor",
      eSuper: usuario.papel === "superadmin",
      pessoas,
      nomeDe: (u) => (u ? (mapa.get(u) ?? u) : "Ninguém"),
      atualizarUsuario: aoMudar,
    };
  }, [usuario, pessoas, aoMudar]);

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useSessao(): Sessao {
  const s = useContext(Contexto);
  if (!s) throw new Error("useSessao fora do SessaoProvider");
  return s;
}

export const ROTULO_PAPEL = { superadmin: "Administrador do sistema", admin: "Administrador", vendedor: "Vendedor" } as const;
