import { useEffect, useMemo, useState } from "react";

import { api, ApiErro } from "../api";
import { digitos, montarUrl, ROTULO_CANAL, telefone } from "../formato";
import type { Canal, Ficha, Modelo } from "../tipos";
import { useAviso } from "./Aviso";

interface Opcao {
  valor: string;
  rotulo: string;
}

export interface Escolha {
  canal: Canal;
  destino: string;
}

/** Destinos possíveis por canal, a partir do que o módulo 3 entregou. */
export function opcoesPorCanal(f: Ficha): Record<Canal, Opcao[]> {
  const { lead } = f;
  const r = lead.contato_receita;
  const ops: Record<Canal, Opcao[]> = { email: [], whatsapp: [], formulario: [], telefone: [] };
  const add = (canal: Canal, valor: string, rotulo: string) => {
    if (valor && !ops[canal].some((o) => o.valor === valor)) ops[canal].push({ valor, rotulo });
  };
  for (const c of lead.contatos) {
    const obs = c.observacao ? `, ${c.observacao}` : "";
    if (c.tipo === "email") add("email", c.valor, `${c.valor} (${c.fonte}${obs})`);
    if (c.tipo === "whatsapp") {
      add("whatsapp", digitos(c.valor), `${telefone(null, c.valor)} (WhatsApp publicado no site)`);
      add("telefone", digitos(c.valor), `${telefone(null, c.valor)} (WhatsApp do site)`);
    }
    if (c.tipo === "telefone") add("telefone", digitos(c.valor), `${telefone(null, c.valor)} (${c.fonte})`);
    if (c.tipo === "formulario") add("formulario", c.valor, c.valor);
  }
  if (r.email) add("email", r.email.trim(), `${r.email.trim()} (Receita)`);
  for (const [ddd, num] of [
    [r.ddd_1, r.telefone_1],
    [r.ddd_2, r.telefone_2],
  ]) {
    const d = digitos(`${ddd ?? ""}${num ?? ""}`);
    if (d.length < 10) continue;
    add("telefone", d, `${telefone(ddd, num)} (Receita)`);
    if (d.length === 11) add("whatsapp", d, `${telefone(ddd, num)} (celular da Receita, WhatsApp não confirmado)`);
  }
  return ops;
}

interface Props {
  ficha: Ficha;
  escolhaExterna: Escolha | null;
  aoRegistrar: () => void;
}

export default function PainelContato({ ficha, escolhaExterna, aoRegistrar }: Props) {
  const aviso = useAviso();
  const opcoes = useMemo(() => opcoesPorCanal(ficha), [ficha]);
  const canais = (Object.keys(opcoes) as Canal[]).filter((c) => opcoes[c].length > 0);
  const inicial: Canal | undefined =
    ficha.lead.canal_sugerido && opcoes[ficha.lead.canal_sugerido].length ? ficha.lead.canal_sugerido : canais[0];

  const [modelos, setModelos] = useState<Modelo[]>([]);
  const [canal, setCanal] = useState<Canal | undefined>(inicial);
  const [destino, setDestino] = useState(inicial ? opcoes[inicial][0].valor : "");
  const [modeloId, setModeloId] = useState<number | null>(null);
  const [assunto, setAssunto] = useState("");
  const [texto, setTexto] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const bloqueada = ficha.supressoes.length > 0;

  useEffect(() => {
    api.modelos().then((m) => setModelos(m.itens), () => undefined);
  }, []);

  useEffect(() => {
    if (escolhaExterna) {
      setCanal(escolhaExterna.canal);
      setDestino(escolhaExterna.destino);
      setModeloId(null);
    }
  }, [escolhaExterna]);

  // Prepara a mensagem sempre que canal, destino ou modelo mudam.
  useEffect(() => {
    if (!canal || !destino || bloqueada) return;
    let vivo = true;
    setErro(null);
    api
      .preparar(ficha.lead.chave, canal, destino, modeloId)
      .then((m) => {
        if (!vivo) return;
        setAssunto(m.assunto);
        setTexto(m.texto);
        if (modeloId === null && m.modelo_id !== null) setModeloId(m.modelo_id);
      })
      .catch((e) => vivo && setErro(e instanceof ApiErro ? e.message : "Não foi possível preparar a mensagem."));
    return () => {
      vivo = false;
    };
  }, [canal, destino, modeloId, ficha.lead.chave, bloqueada]);

  if (bloqueada) {
    return (
      <section className="bloco">
        <h2>Contatar</h2>
        <p className="mudo">Esta empresa está na lista de não contatar. Nenhuma mensagem pode ser preparada.</p>
      </section>
    );
  }
  if (!canal) {
    return (
      <section className="bloco">
        <h2>Contatar</h2>
        <p className="mudo">
          O módulo 3 não encontrou nenhum contato para esta empresa. Quando houver telefone, e-mail ou formulário, ele
          aparece aqui.
        </p>
      </section>
    );
  }

  const daVez = modelos.filter((m) => m.canal === canal);
  const acao: Record<Canal, string> = {
    whatsapp: "Abrir no WhatsApp",
    email: "Abrir no e-mail",
    formulario: "Copiar texto e abrir formulário",
    telefone: "Registrar ligação",
  };

  async function executar() {
    if (!canal) return;
    const url = montarUrl(canal, destino, texto, assunto);
    if (!ficha.simulado) {
      if (canal === "formulario") await navigator.clipboard?.writeText(texto).catch(() => undefined);
      if (url && canal !== "telefone") window.open(url, "_blank", "noopener");
    }
    if (canal !== "telefone") api.evento("ui.abrir_link_contato", ficha.lead.chave, { canal, destino, aberto: String(!ficha.simulado) });
    setOcupado(true);
    try {
      await api.registrar(ficha.lead.chave, canal, destino, texto, canal === "email" ? assunto : undefined);
      aviso(
        ficha.simulado
          ? "Modo simulado: o link não foi aberto, mas o contato ficou registrado."
          : canal === "telefone"
            ? "Ligação registrada."
            : "Contato registrado. Revise e envie a mensagem na janela que abriu.",
      );
      aoRegistrar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível registrar o contato.", "erro");
    } finally {
      setOcupado(false);
    }
  }

  return (
    <section className="bloco">
      <h2>Contatar</h2>
      <div className="abas" role="tablist" aria-label="Canal">
        {(["email", "whatsapp", "formulario", "telefone"] as Canal[]).map((c) => (
          <button
            key={c}
            type="button"
            role="tab"
            aria-selected={canal === c}
            disabled={!opcoes[c].length}
            className={canal === c ? "ativa" : ""}
            onClick={() => {
              setCanal(c);
              setDestino(opcoes[c][0]?.valor ?? "");
              setModeloId(null);
            }}
          >
            {ROTULO_CANAL[c]}
          </button>
        ))}
      </div>

      <label className="campo">
        <span>Para</span>
        <select value={destino} onChange={(e) => setDestino(e.target.value)}>
          {opcoes[canal].map((o) => (
            <option key={o.valor} value={o.valor}>
              {o.rotulo}
            </option>
          ))}
        </select>
      </label>

      {daVez.length > 1 && (
        <label className="campo">
          <span>Modelo</span>
          <select value={modeloId ?? ""} onChange={(e) => setModeloId(Number(e.target.value))}>
            {daVez.map((m) => (
              <option key={m.id} value={m.id}>
                {m.nome}
              </option>
            ))}
          </select>
        </label>
      )}

      {canal === "email" && (
        <label className="campo">
          <span>Assunto</span>
          <input value={assunto} onChange={(e) => setAssunto(e.target.value)} />
        </label>
      )}
      <label className="campo">
        <span>{canal === "telefone" ? "Roteiro e anotações da ligação" : "Mensagem"}</span>
        <textarea rows={canal === "email" ? 12 : 7} value={texto} onChange={(e) => setTexto(e.target.value)} />
      </label>

      {erro && <p className="erro-linha">{erro}</p>}

      <div className="acoes">
        <button type="button" className="botao botao-primario" disabled={ocupado || !texto.trim()} onClick={executar}>
          {acao[canal]}
        </button>
        {canal === "telefone" ? (
          <a className="botao" href={`tel:+55${destino}`} onClick={() => api.evento("ui.ligar", ficha.lead.chave, { destino })}>
            Ligar para {telefone(null, destino)}
          </a>
        ) : (
          <button
            type="button"
            className="botao"
            onClick={() =>
              navigator.clipboard?.writeText(texto).then(
                () => {
                  aviso("Texto copiado.");
                  api.evento("ui.copiar_mensagem", ficha.lead.chave, { canal });
                },
                () => aviso("Não foi possível copiar.", "erro"),
              )
            }
          >
            Copiar texto
          </button>
        )}
      </div>
      <p className="nota">
        {canal === "whatsapp" && "O WhatsApp abre no seu aparelho com o texto pronto. A mensagem só sai quando você enviar."}
        {canal === "email" && "O e-mail abre no seu programa de e-mail. Revise antes de enviar."}
        {canal === "formulario" && "O texto vai para a área de transferência; cole no formulário e envie."}
        {canal === "telefone" && "Use o texto como roteiro e anote o que foi combinado antes de registrar."}
      </p>
    </section>
  );
}
