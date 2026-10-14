import { useEffect, useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { cnpj, data, telefone } from "../formato";
import type { ItemSupressao } from "../tipos";

const ROTULO: Record<ItemSupressao["tipo"], string> = {
  email: "E-mail",
  telefone: "Telefone",
  dominio: "Domínio",
  cnpj: "CNPJ",
};

function exibir(i: ItemSupressao) {
  if (i.tipo === "cnpj") return cnpj(i.valor);
  if (i.tipo === "telefone") return telefone(null, i.valor);
  return i.valor;
}

export default function Supressao() {
  const aviso = useAviso();
  const [itens, setItens] = useState<ItemSupressao[]>([]);
  const [tipo, setTipo] = useState<ItemSupressao["tipo"]>("email");
  const [valor, setValor] = useState("");
  const [motivo, setMotivo] = useState("");
  const [filtro, setFiltro] = useState("");
  const [global, setGlobal] = useState(false);
  const [poderes, setPoderes] = useState({ liberar: false, global: false });

  const carregar = () =>
    api.supressao().then(
      (r) => {
        setItens(r.itens);
        setPoderes({ liberar: r.pode_liberar, global: r.pode_global });
      },
      () => aviso("Não foi possível carregar a lista."),
    );
  useEffect(() => {
    carregar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function adicionar(e: FormEvent) {
    e.preventDefault();
    try {
      await api.suprimirContato(tipo, valor, motivo || undefined, global);
      aviso(global ? `${ROTULO[tipo]} bloqueado para todas as empresas.` : `${ROTULO[tipo]} adicionado à lista.`);
      setValor("");
      setMotivo("");
      carregar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível adicionar.", "erro");
    }
  }

  async function remover(i: ItemSupressao) {
    if (!window.confirm(`Liberar ${exibir(i)} para contato de novo? Faça isso só se a pessoa pediu.`)) return;
    await api.removerSupressao(i.id).catch(() => undefined);
    aviso("Removido da lista.");
    carregar();
  }

  const visiveis = itens.filter((i) => !filtro || i.valor.includes(filtro.toLowerCase().replace(/[^\w@.-]/g, "")));

  return (
    <div className="pagina">
      <header className="cabecalho">
        <div>
          <h1>Não contatar</h1>
          <p className="sub">
            Ninguém desta lista recebe mensagem da sua empresa. Pedidos de descadastro entram aqui assim que chegam; qualquer pessoa da
            equipe pode incluir{poderes.liberar ? ", e só administradores liberam" : ". Para liberar, fale com o administrador"}.
          </p>
        </div>
      </header>

      <form className="filtros" onSubmit={adicionar}>
        <label className="campo campo-curto">
          <span>Tipo</span>
          <select value={tipo} onChange={(e) => setTipo(e.target.value as ItemSupressao["tipo"])}>
            {Object.entries(ROTULO).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="campo">
          <span>{ROTULO[tipo]}</span>
          <input required value={valor} onChange={(e) => setValor(e.target.value)} />
        </label>
        <label className="campo campo-largo">
          <span>Motivo</span>
          <input value={motivo} placeholder="Ex.: respondeu pedindo remoção" onChange={(e) => setMotivo(e.target.value)} />
        </label>
        {poderes.global && (
          <label className="marcar">
            <input type="checkbox" checked={global} onChange={(e) => setGlobal(e.target.checked)} />
            <span>Vale para todas as empresas clientes</span>
          </label>
        )}
        <button className="botao botao-primario">Adicionar à lista</button>
      </form>

      <div className="tabela-area">
        <table className="tabela">
          <thead>
            <tr>
              <th>Tipo</th>
              <th>
                <label>
                  <span className="oculto">Procurar na lista</span>
                  <input className="th-busca" placeholder="Procurar" value={filtro} onChange={(e) => setFiltro(e.target.value)} />
                </label>
              </th>
              <th>Motivo</th>
              <th>Incluído em</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visiveis.map((i) => (
              <tr key={i.id}>
                <td>
                  {ROTULO[i.tipo]}
                  {i.global && <span className="marcador marcador-papel">todas as empresas</span>}
                </td>
                <td className="tabular">{exibir(i)}</td>
                <td className="mudo">{i.motivo ?? "—"}</td>
                <td className="mudo nowrap">
                  {data(i.criado_em)}, {i.criado_por}
                </td>
                <td className="direita">
                  {poderes.liberar && (!i.global || poderes.global) && (
                    <button type="button" className="botao-texto" onClick={() => remover(i)}>
                      Liberar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {itens.length === 0 && <div className="vazio-tabela">A lista está vazia.</div>}
      </div>
    </div>
  );
}
