import { useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import type { Usuario } from "../tipos";

export default function Entrar({ aoEntrar }: { aoEntrar: (u: Usuario) => void }) {
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
  const [lembrar, setLembrar] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      aoEntrar(await api.entrar(usuario, senha, lembrar));
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível entrar.");
      setSenha("");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="entrar">
      <form className="entrar-caixa" onSubmit={enviar}>
        <div className="marca marca-grande">
          <span className="marca-simbolo" aria-hidden="true">P</span>
          <span>Prospecção</span>
        </div>
        <p className="entrar-sub">Empresas recém-abertas, prontas para o primeiro contato.</p>
        <label className="campo">
          <span>Usuário</span>
          <input autoFocus autoComplete="username" value={usuario} onChange={(e) => setUsuario(e.target.value)} required />
        </label>
        <label className="campo">
          <span>Senha</span>
          <input type="password" autoComplete="current-password" value={senha} onChange={(e) => setSenha(e.target.value)} required />
        </label>
        <label className="marcar">
          <input type="checkbox" checked={lembrar} onChange={(e) => setLembrar(e.target.checked)} />
          <span>Manter conectado neste computador</span>
        </label>
        {erro && (
          <p className="erro-linha" role="alert">
            {erro}
          </p>
        )}
        <button className="botao botao-primario largo" disabled={enviando}>
          {enviando ? "Entrando…" : "Entrar"}
        </button>
        <p className="entrar-nota">
          Sem acesso? Peça ao administrador da sua empresa.
        </p>
      </form>
    </div>
  );
}
