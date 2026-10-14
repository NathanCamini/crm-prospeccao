import { useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { ROTULO_PAPEL, useSessao } from "../sessao";
import type { Usuario } from "../tipos";

function FormSenha({ aoTrocar, rotulo }: { aoTrocar: () => void; rotulo: string }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [repetir, setRepetir] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    if (nova !== repetir) {
      setErro("A senha nova e a repetição não conferem.");
      return;
    }
    setEnviando(true);
    try {
      await api.trocarSenha(atual, nova);
      setAtual("");
      setNova("");
      setRepetir("");
      aoTrocar();
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível trocar a senha.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="form-senha" onSubmit={enviar}>
      <label className="campo">
        <span>Senha atual</span>
        <input type="password" autoComplete="current-password" required value={atual} onChange={(e) => setAtual(e.target.value)} />
      </label>
      <label className="campo">
        <span>Senha nova (mínimo 8 caracteres)</span>
        <input type="password" autoComplete="new-password" minLength={8} required value={nova} onChange={(e) => setNova(e.target.value)} />
      </label>
      <label className="campo">
        <span>Repita a senha nova</span>
        <input type="password" autoComplete="new-password" required value={repetir} onChange={(e) => setRepetir(e.target.value)} />
      </label>
      {erro && (
        <p className="erro-linha" role="alert">
          {erro}
        </p>
      )}
      <button className="botao botao-primario" disabled={enviando}>
        {enviando ? "Salvando…" : rotulo}
      </button>
    </form>
  );
}

/** Tela única para quem entrou com senha provisória. */
export function TrocaObrigatoria({ usuario, aoTrocar, aoSair }: { usuario: Usuario; aoTrocar: () => void; aoSair: () => void }) {
  return (
    <div className="entrar">
      <div className="entrar-caixa">
        <div className="marca marca-grande">
          <span className="marca-simbolo" aria-hidden="true">P</span>
          <span>Prospecção</span>
        </div>
        <p>
          Olá, {usuario.nome_exibicao}. Você entrou com uma senha provisória
          {usuario.empresa ? ` da ${usuario.empresa.nome}` : ""}. Crie a sua senha para continuar.
        </p>
        <FormSenha aoTrocar={aoTrocar} rotulo="Salvar senha e entrar" />
        <button type="button" className="botao-texto" onClick={aoSair}>
          Sair
        </button>
      </div>
    </div>
  );
}

export default function Conta() {
  const { usuario } = useSessao();
  const aviso = useAviso();
  return (
    <div className="pagina pagina-estreita">
      <header className="cabecalho">
        <div>
          <h1>Minha conta</h1>
          <p className="sub">
            {usuario.nome_exibicao}, {ROTULO_PAPEL[usuario.papel].toLowerCase()}
            {usuario.papel !== "superadmin" && usuario.empresa ? ` na ${usuario.empresa.nome}` : ""}. Usuário de acesso:{" "}
            <strong>{usuario.usuario}</strong>.
          </p>
        </div>
      </header>
      <section className="bloco">
        <h2>Trocar senha</h2>
        <p className="nota">Ao trocar, os outros computadores onde você estava conectado saem automaticamente.</p>
        <FormSenha aoTrocar={() => aviso("Senha trocada.")} rotulo="Trocar senha" />
      </section>
    </div>
  );
}
