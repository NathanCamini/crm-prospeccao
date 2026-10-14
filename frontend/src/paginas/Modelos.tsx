import { useEffect, useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { ROTULO_CANAL } from "../formato";
import type { Canal, Modelo } from "../tipos";

type Rascunho = Omit<Modelo, "id" | "atualizado_em"> & { id?: number };
const VAZIO: Rascunho = { nome: "", canal: "email", perfil_id: null, assunto: "", corpo: "" };

export default function Modelos() {
  const aviso = useAviso();
  const [modelos, setModelos] = useState<Modelo[]>([]);
  const [variaveis, setVariaveis] = useState<string[]>([]);
  const [rascunho, setRascunho] = useState<Rascunho | null>(null);
  const [podeEditar, setPodeEditar] = useState(false);

  const carregar = () =>
    api.modelos().then(
      (r) => {
        setModelos(r.itens);
        setVariaveis(r.variaveis);
        setPodeEditar(r.pode_editar);
      },
      (e) => aviso(e instanceof ApiErro ? e.message : "Não foi possível carregar os modelos.", "erro"),
    );

  useEffect(() => {
    carregar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function salvar(e: FormEvent) {
    e.preventDefault();
    if (!rascunho) return;
    const { id, ...dados } = rascunho;
    try {
      await api.salvarModelo(dados, id);
      aviso("Modelo salvo.");
      setRascunho(null);
      carregar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível salvar.", "erro");
    }
  }

  async function apagar(m: Modelo) {
    if (!window.confirm(`Apagar o modelo "${m.nome}"?`)) return;
    await api.apagarModelo(m.id).catch(() => undefined);
    aviso("Modelo apagado.");
    if (rascunho?.id === m.id) setRascunho(null);
    carregar();
  }

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Modelos de mensagem</h1>
          <p className="sub">
            Textos usados no painel de contato. As variáveis entre chaves são trocadas pelos dados da empresa.
            {!podeEditar && " Só o administrador altera os modelos."}
          </p>
        </div>
        {podeEditar && (
          <button type="button" className="botao botao-primario" onClick={() => setRascunho({ ...VAZIO })}>
            Novo modelo
          </button>
        )}
      </header>

      <div className="duas-colunas">
        <table className="tabela">
          <thead>
            <tr>
              <th>Nome</th>
              <th>Canal</th>
              <th>Perfil</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {modelos.map((m) => (
              <tr key={m.id} className={rascunho?.id === m.id ? "selecionada" : ""} onClick={() => setRascunho({ ...m })}>
                <td>
                  <button type="button" className="link-linha" onClick={() => setRascunho({ ...m })}>
                    {m.nome}
                  </button>
                </td>
                <td>{ROTULO_CANAL[m.canal]}</td>
                <td className="mudo">{m.perfil_id ?? "qualquer"}</td>
                <td className="direita">
                  {podeEditar && (
                    <button
                      type="button"
                      className="botao-texto perigo"
                      onClick={(e) => {
                        e.stopPropagation();
                        apagar(m);
                      }}
                    >
                      Apagar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {rascunho && !podeEditar ? (
          <section className="bloco">
            <h2>{rascunho.nome}</h2>
            {rascunho.assunto && (
              <p>
                <strong>Assunto:</strong> {rascunho.assunto}
              </p>
            )}
            <p className="resposta">{rascunho.corpo}</p>
          </section>
        ) : rascunho ? (
          <form className="bloco" onSubmit={salvar}>
            <h2>{rascunho.id ? "Editar modelo" : "Novo modelo"}</h2>
            <label className="campo">
              <span>Nome</span>
              <input required value={rascunho.nome} onChange={(e) => setRascunho({ ...rascunho, nome: e.target.value })} />
            </label>
            <div className="linha-campos">
              <label className="campo">
                <span>Canal</span>
                <select value={rascunho.canal} onChange={(e) => setRascunho({ ...rascunho, canal: e.target.value as Canal })}>
                  {(Object.keys(ROTULO_CANAL) as Canal[]).map((c) => (
                    <option key={c} value={c}>
                      {ROTULO_CANAL[c]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="campo">
                <span>Perfil (opcional)</span>
                <input
                  value={rascunho.perfil_id ?? ""}
                  placeholder="qualquer"
                  onChange={(e) => setRascunho({ ...rascunho, perfil_id: e.target.value || null })}
                />
              </label>
            </div>
            {rascunho.canal === "email" && (
              <label className="campo">
                <span>Assunto</span>
                <input value={rascunho.assunto ?? ""} onChange={(e) => setRascunho({ ...rascunho, assunto: e.target.value })} />
              </label>
            )}
            <label className="campo">
              <span>Texto</span>
              <textarea required rows={14} value={rascunho.corpo} onChange={(e) => setRascunho({ ...rascunho, corpo: e.target.value })} />
            </label>
            <p className="nota">
              Variáveis: {variaveis.map((v) => `{${v}}`).join(" ")}. Mensagens de primeiro contato devem dizer de onde veio o contato e
              como pedir para não receber mais.
            </p>
            <div className="acoes">
              <button className="botao botao-primario">Salvar modelo</button>
              <button type="button" className="botao" onClick={() => setRascunho(null)}>
                Cancelar
              </button>
            </div>
          </form>
        ) : (
          <div className="bloco vazio-lateral">
            <p className="mudo">{podeEditar ? "Escolha um modelo para editar ou crie um novo." : "Escolha um modelo para ler."}</p>
          </div>
        )}
      </div>
    </div>
  );
}
