import { useState, type FormEvent } from "react";

import { api, ApiErro } from "../api";
import { data, hojeIso, quandoVence, reais } from "../formato";
import { useSessao } from "../sessao";
import type { Ficha } from "../tipos";
import { useAviso } from "./Aviso";
import Dialogo from "./Dialogo";

/** Próximos passos do lead: o que fazer e quando. */
export function BlocoTarefas({ ficha, aoMudar }: { ficha: Ficha; aoMudar: () => void }) {
  const { eAdmin, pessoas, usuario, nomeDe } = useSessao();
  const aviso = useAviso();
  const [titulo, setTitulo] = useState("");
  const [vence, setVence] = useState(hojeIso(1));
  const [responsavel, setResponsavel] = useState(usuario.usuario);
  const [verConcluidas, setVerConcluidas] = useState(false);
  const abertas = ficha.tarefas.filter((t) => !t.concluida_em);
  const concluidas = ficha.tarefas.filter((t) => t.concluida_em);

  async function criar(e: FormEvent) {
    e.preventDefault();
    try {
      await api.criarTarefa(ficha.lead.chave, titulo.trim(), vence, eAdmin ? responsavel : undefined);
      setTitulo("");
      setVence(hojeIso(1));
      aviso("Próximo passo agendado.");
      aoMudar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível agendar.", "erro");
    }
  }

  async function concluir(id: number, concluida: boolean) {
    try {
      await api.alterarTarefa(id, { concluida });
      aoMudar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível alterar.", "erro");
    }
  }

  return (
    <section className="bloco">
      <h2>Próximos passos</h2>
      {abertas.length === 0 ? (
        <p className="mudo">Nada agendado. Combine o próximo contato para este lead não esfriar.</p>
      ) : (
        <ul className="tarefas">
          {abertas.map((t) => {
            const q = quandoVence(t.vence_em);
            return (
              <li key={t.id}>
                <label className="marcar">
                  <input type="checkbox" onChange={() => concluir(t.id, true)} aria-label={`Concluir: ${t.titulo}`} />
                  <span>{t.titulo}</span>
                </label>
                <span className={`vence${q.atrasada ? " atrasada" : q.hoje ? " hoje" : ""}`}>{q.texto}</span>
                {t.responsavel !== usuario.usuario && <span className="mudo pequeno">com {nomeDe(t.responsavel)}</span>}
              </li>
            );
          })}
        </ul>
      )}
      <form className="tarefa-form" onSubmit={criar}>
        <label className="campo campo-largo">
          <span>Novo passo</span>
          <input value={titulo} onChange={(e) => setTitulo(e.target.value)} placeholder="Ex.: ligar para a governança" minLength={2} required />
        </label>
        <label className="campo">
          <span>Quando</span>
          <input type="date" value={vence} min={hojeIso(-365)} onChange={(e) => setVence(e.target.value)} required />
        </label>
        {eAdmin && (
          <label className="campo">
            <span>Quem</span>
            <select value={responsavel} onChange={(e) => setResponsavel(e.target.value)}>
              {pessoas
                .filter((p) => p.ativo)
                .map((p) => (
                  <option key={p.usuario} value={p.usuario}>
                    {p.usuario === usuario.usuario ? "Eu" : p.nome_exibicao}
                  </option>
                ))}
            </select>
          </label>
        )}
        <button className="botao">Agendar</button>
      </form>
      {concluidas.length > 0 && (
        <>
          <button type="button" className="botao-texto" onClick={() => setVerConcluidas((v) => !v)}>
            {verConcluidas ? "Esconder concluídos" : `Ver concluídos (${concluidas.length})`}
          </button>
          {verConcluidas && (
            <ul className="tarefas concluidas">
              {concluidas.map((t) => (
                <li key={t.id}>
                  <label className="marcar">
                    <input type="checkbox" checked onChange={() => concluir(t.id, false)} aria-label={`Reabrir: ${t.titulo}`} />
                    <span>{t.titulo}</span>
                  </label>
                  <span className="mudo pequeno">
                    {data(t.concluida_em)} por {nomeDe(t.concluida_por)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

/** Vendas do lead. Estornar não apaga: o registro fica, riscado, com o motivo. */
export function BlocoVendas({ ficha, aoMudar, aoRegistrar }: { ficha: Ficha; aoMudar: () => void; aoRegistrar: () => void }) {
  const { eAdmin, nomeDe } = useSessao();
  const aviso = useAviso();

  async function estornar(id: number) {
    const motivo = window.prompt("Motivo do estorno (fica registrado):", "");
    if (!motivo || motivo.trim().length < 3) return;
    try {
      await api.estornarVenda(id, motivo.trim());
      aviso("Venda estornada.");
      aoMudar();
    } catch (err) {
      aviso(err instanceof ApiErro ? err.message : "Não foi possível estornar.", "erro");
    }
  }

  return (
    <section className="bloco">
      <div className="alvo-topo">
        <h2>Vendas</h2>
        {ficha.total_vendas_centavos > 0 && <strong className="total-vendas">{reais(ficha.total_vendas_centavos)}</strong>}
      </div>
      {ficha.vendas.length === 0 ? (
        <p className="mudo">Nenhuma venda registrada.</p>
      ) : (
        <table className="tabela tabela-compacta">
          <tbody>
            {ficha.vendas.map((v) => (
              <tr key={v.id} className={v.estornada_em ? "estornada" : ""}>
                <td className="tabular nowrap">{data(v.data)}</td>
                <td>
                  {v.descricao ?? <span className="mudo">sem descrição</span>}
                  <span className="empresa-meta">
                    {nomeDe(v.vendedor)}
                    {v.estornada_em && (
                      <span className="marcador marcador-alerta">
                        estornada por {nomeDe(v.estornada_por)}: {v.motivo_estorno}
                      </span>
                    )}
                  </span>
                </td>
                <td className="direita tabular nowrap">{reais(v.valor_centavos)}</td>
                {eAdmin && (
                  <td className="direita">
                    {!v.estornada_em && (
                      <button type="button" className="botao-texto perigo" onClick={() => estornar(v.id)}>
                        Estornar
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="acoes">
        <button type="button" className="botao" onClick={aoRegistrar}>
          Registrar venda
        </button>
      </div>
    </section>
  );
}

/** Registrar uma venda; também usado quando alguém muda o estágio para Cliente. */
export function DialogoVenda({
  aberto,
  ficha,
  vindoDoEstagio,
  aoFechar,
  aoConcluir,
}: {
  aberto: boolean;
  ficha: Ficha;
  vindoDoEstagio: boolean;
  aoFechar: () => void;
  aoConcluir: () => void;
}) {
  const { eAdmin, pessoas, usuario } = useSessao();
  const aviso = useAviso();
  const [valor, setValor] = useState("");
  const [quando, setQuando] = useState(hojeIso());
  const [descricao, setDescricao] = useState("");
  const [vendedor, setVendedor] = useState(ficha.crm.responsavel ?? usuario.usuario);
  const [erro, setErro] = useState<string | null>(null);

  async function registrar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    try {
      await api.registrarVenda(ficha.lead.chave, valor, quando, descricao || undefined, eAdmin ? vendedor : undefined);
      aviso("Venda registrada. O lead agora é cliente.");
      setValor("");
      setDescricao("");
      aoConcluir();
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível registrar.");
    }
  }

  async function soEstagio() {
    try {
      await api.mudarEstagio(ficha.lead.chave, "cliente");
      aviso("Estágio alterado para Cliente.");
      aoConcluir();
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "Não foi possível alterar.");
    }
  }

  return (
    <Dialogo aberto={aberto} titulo={vindoDoEstagio ? "Virou cliente: registrar a venda?" : "Registrar venda"} aoFechar={aoFechar}>
      <form onSubmit={registrar} className="form-venda">
        <div className="grade-form">
          <label className="campo">
            <span>Valor (R$)</span>
            <input autoFocus required inputMode="decimal" value={valor} onChange={(e) => setValor(e.target.value)} placeholder="1.500,00" />
          </label>
          <label className="campo">
            <span>Data</span>
            <input type="date" required max={hojeIso()} value={quando} onChange={(e) => setQuando(e.target.value)} />
          </label>
          {eAdmin && (
            <label className="campo">
              <span>Vendedor</span>
              <select value={vendedor} onChange={(e) => setVendedor(e.target.value)}>
                {pessoas
                  .filter((p) => p.ativo)
                  .map((p) => (
                    <option key={p.usuario} value={p.usuario}>
                      {p.nome_exibicao}
                    </option>
                  ))}
              </select>
            </label>
          )}
        </div>
        <label className="campo">
          <span>O que foi vendido (opcional)</span>
          <input value={descricao} onChange={(e) => setDescricao(e.target.value)} placeholder="Ex.: 500 kits de amenities" />
        </label>
        {erro && (
          <p className="erro-linha" role="alert">
            {erro}
          </p>
        )}
        <div className="acoes">
          <button className="botao botao-primario">Registrar venda</button>
          {vindoDoEstagio && (
            <button type="button" className="botao" onClick={soEstagio}>
              Só mudar o estágio
            </button>
          )}
          <button type="button" className="botao-texto" onClick={aoFechar}>
            Cancelar
          </button>
        </div>
      </form>
    </Dialogo>
  );
}
