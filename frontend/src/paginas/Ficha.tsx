import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiErro } from "../api";
import { useAviso } from "../componentes/Aviso";
import { BlocoTarefas, BlocoVendas, DialogoVenda } from "../componentes/BlocosFicha";
import { Pontuacao } from "../componentes/Estagio";
import FormLeadManual from "../componentes/FormLeadManual";
import LinhaDoTempo from "../componentes/LinhaDoTempo";
import PainelContato, { type Escolha } from "../componentes/PainelContato";
import {
  cnpj as mascara,
  data,
  dataHora,
  diasDesde,
  digitos,
  dinheiro,
  nomeEmpresa,
  ROTULO_ESTAGIO,
  telefone,
  titulo,
} from "../formato";
import { useSessao } from "../sessao";
import {
  ESTAGIOS,
  type Canal,
  type Contato,
  type DadosManuais,
  type Estagio as E,
  type Ficha as TFicha,
  type Lead,
  type Perfil,
} from "../tipos";

const ROTULO_TIPO: Record<Contato["tipo"], string> = {
  email: "E-mail",
  telefone: "Telefone",
  whatsapp: "WhatsApp",
  site: "Site",
  formulario: "Formulário",
  rede_social: "Rede social",
};
const ROTULO_CONFIANCA = { alta: "Alta", media: "Média", baixa: "Baixa" };

function canalDoContato(c: Contato): Canal | null {
  return c.tipo === "email" ? "email" : c.tipo === "whatsapp" ? "whatsapp" : c.tipo === "formulario" ? "formulario" : c.tipo === "telefone" ? "telefone" : null;
}

function paraFormulario(lead: Lead): DadosManuais {
  const de = (tipo: Contato["tipo"]) => lead.contatos.find((c) => c.tipo === tipo)?.valor ?? "";
  return {
    nome: lead.nome_fantasia ?? lead.razao_social,
    cnpj: lead.cnpj ?? "",
    razao_social: lead.razao_social !== lead.nome_fantasia ? lead.razao_social : "",
    contato_nome: lead.contato_nome ?? "",
    telefone: de("telefone"),
    whatsapp: de("whatsapp"),
    email: de("email"),
    site: de("site"),
    logradouro: lead.logradouro ?? "",
    bairro: lead.bairro ?? "",
    municipio: titulo(lead.municipio),
    uf: lead.uf ?? "",
    perfil_id: lead.perfil_id ?? "",
  };
}

export default function Ficha() {
  const { chave = "" } = useParams();
  const aviso = useAviso();
  const navegar = useNavigate();
  const { eAdmin, pessoas, usuario, nomeDe } = useSessao();
  const [ficha, setFicha] = useState<TFicha | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [escolha, setEscolha] = useState<Escolha | null>(null);
  const [venda, setVenda] = useState<"fechada" | "botao" | "estagio">("fechada");
  const [editando, setEditando] = useState(false);
  const [perfis, setPerfis] = useState<Perfil[]>([]);

  const carregar = useCallback(() => {
    api.ficha(chave).then(
      (f) => {
        setFicha(f);
        setErro(null);
      },
      (e) => setErro(e instanceof ApiErro ? e.message : "Não foi possível abrir o lead."),
    );
  }, [chave]);

  useEffect(() => {
    setFicha(null);
    setEditando(false);
    carregar();
  }, [carregar]);

  useEffect(() => {
    if (!perfis.length) api.perfis().then(setPerfis, () => undefined);
  }, [editando, perfis.length]);

  if (erro)
    return (
      <div className="pagina">
        <Link to="/leads" className="voltar">
          Voltar para leads
        </Link>
        <div className="faixa faixa-erro" role="alert">
          {erro}
        </div>
      </div>
    );
  if (!ficha) return <div className="pagina carregando-tela" aria-busy="true" />;

  const { lead, crm } = ficha;
  const c = lead.classificacao;
  const r = lead.contato_receita;
  const manual = lead.origem === "manual";
  const dias = diasDesde(lead.data_inicio_atividade);

  async function mudar(estagio: E) {
    if (estagio === "cliente" && crm.estagio !== "cliente") {
      setVenda("estagio");
      return;
    }
    try {
      await api.mudarEstagio(lead.chave, estagio);
      aviso(`Estágio alterado para ${ROTULO_ESTAGIO[estagio]}.`);
      carregar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível alterar o estágio.", "erro");
    }
  }

  async function passarPara(responsavel: string | null) {
    try {
      await api.mudarResponsavel(lead.chave, responsavel);
      aviso(
        responsavel === usuario.usuario
          ? "O lead agora é seu."
          : responsavel
            ? `Lead passado para ${nomeDe(responsavel)}.`
            : "Lead liberado para a equipe.",
      );
      carregar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível mudar o responsável.", "erro");
    }
  }

  async function naoContatar() {
    const motivo = window.prompt(
      "A empresa, os e-mails e os telefones dela vão para a lista de não contatar.\nMotivo:",
      "pediu para não receber contato",
    );
    if (motivo === null) return;
    try {
      const res = await api.suprimir(lead.chave, motivo || "pediu para não receber contato");
      aviso(`${res.suprimidos} contatos bloqueados. O lead foi descartado.`);
      carregar();
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível bloquear.", "erro");
    }
  }

  const usar = (canal: Canal, destino: string) => {
    setEscolha({ canal, destino });
    document.getElementById("painel-contato")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const telefonesReceita = [
    [r.ddd_1, r.telefone_1],
    [r.ddd_2, r.telefone_2],
  ].filter(([, n]) => n);

  return (
    <div className="pagina">
      <div className="ficha-topo">
        <Link to="/leads" className="voltar">
          Voltar para leads
        </Link>
        {eAdmin && (
          <Link to={`/auditoria?entidade_id=${lead.chave}`} className="voltar">
            Ver na auditoria
          </Link>
        )}
      </div>

      <header className="ficha-cabecalho">
        <div className="ficha-titulo">
          <h1>{nomeEmpresa(lead)}</h1>
          <p className="sub">
            {lead.cnpj && <span className="tabular">{mascara(lead.cnpj)}</span>}
            {lead.municipio && (
              <span>
                {titulo(lead.municipio)}
                {lead.uf && `/${lead.uf}`}
              </span>
            )}
            {manual ? (
              <span className="marcador marcador-papel">cadastrado pela equipe</span>
            ) : (
              dias !== null && <span>aberta {dias === 0 ? "hoje" : `há ${dias} dias`}</span>
            )}
          </p>
        </div>
        <div className="ficha-estagio">
          {eAdmin ? (
            <label className="campo">
              <span>Responsável</span>
              <select value={crm.responsavel ?? ""} onChange={(e) => passarPara(e.target.value || null)}>
                <option value="">Livre (ninguém)</option>
                {pessoas
                  .filter((p) => p.ativo || p.usuario === crm.responsavel)
                  .map((p) => (
                    <option key={p.usuario} value={p.usuario}>
                      {p.usuario === usuario.usuario ? `${p.nome_exibicao} (você)` : p.nome_exibicao}
                    </option>
                  ))}
              </select>
            </label>
          ) : (
            <div className="campo responsavel-vendedor">
              <span>Responsável</span>
              {crm.responsavel ? (
                <p>
                  Você{" "}
                  <button type="button" className="botao-texto" onClick={() => passarPara(null)}>
                    Liberar
                  </button>
                </p>
              ) : (
                <button type="button" className="botao" onClick={() => passarPara(usuario.usuario)}>
                  Assumir este lead
                </button>
              )}
            </div>
          )}
          <label className="campo">
            <span>Estágio</span>
            <select
              value={crm.estagio}
              onChange={(e) => mudar(e.target.value as E)}
              style={{ ["--cor" as string]: `var(--e-${crm.estagio})` }}
              className="select-estagio"
            >
              {ESTAGIOS.map((e) => (
                <option key={e} value={e}>
                  {ROTULO_ESTAGIO[e]}
                </option>
              ))}
            </select>
          </label>
          {ficha.supressoes.length === 0 && (
            <button type="button" className="botao botao-perigo" onClick={naoContatar}>
              Não contatar mais
            </button>
          )}
        </div>
      </header>

      {ficha.supressoes.length > 0 && (
        <div className="faixa faixa-alerta" role="note">
          Na lista de não contatar: {ficha.supressoes.map((s) => s.valor).join(", ")}
          {ficha.supressoes[0].motivo ? ` (${ficha.supressoes[0].motivo})` : ""}.
        </div>
      )}
      {lead.ausente_desde && (
        <div className="faixa faixa-simulado">
          Este lead saiu da base do agente em {data(lead.ausente_desde)}. Ele continua aqui porque a equipe já trabalhou nele; os dados são
          os da última versão recebida.
        </div>
      )}
      {ficha.simulado && (
        <div className="faixa faixa-simulado">
          Dados simulados: as ações registram o contato, mas não abrem WhatsApp, e-mail nem formulário.
        </div>
      )}

      <div className="ficha-grade">
        <div className="coluna">
          {c ? (
            <section className="bloco bloco-destaque">
              <div className="alvo-topo">
                <h2>Por que é alvo</h2>
                <Pontuacao valor={c.score} />
              </div>
              <p className="justificativa">{c.justificativa || "O agente não registrou justificativa."}</p>
              <dl className="dados dados-2">
                <dt>Produto sugerido</dt>
                <dd>{c.produto_sugerido ?? "—"}</dd>
                <dt>Padrão</dt>
                <dd>{c.padrao ? titulo(c.padrao) : "—"}</dd>
                <dt>Rede ou franquia</dt>
                <dd>{c.e_franquia ? (c.marca ?? "Sim") : c.e_franquia === false ? "Independente" : "Não informado"}</dd>
                <dt>Perfil</dt>
                <dd>
                  {perfis.find((p) => p.id === c.perfil_id)?.nome ?? c.perfil_id}, versão {c.perfil_versao}
                </dd>
              </dl>
              {c.e_franquia && (
                <p className="nota">Unidades de rede costumam comprar de fornecedores definidos pela rede. Confirme quem decide antes de oferecer.</p>
              )}
            </section>
          ) : (
            <section className="bloco bloco-destaque">
              <div className="alvo-topo">
                <h2>Cadastro da equipe</h2>
                {!editando && (
                  <button type="button" className="botao-texto" onClick={() => setEditando(true)}>
                    Editar dados
                  </button>
                )}
              </div>
              {editando ? (
                <FormLeadManual
                  inicial={paraFormulario(lead)}
                  perfis={perfis}
                  rotulo="Salvar alterações"
                  aoCancelar={() => setEditando(false)}
                  aoDuplicado={(k) => navegar(`/leads/${k}`)}
                  aoEnviar={async (d) => {
                    const res = await api.editarManual(lead.chave, d);
                    aviso(res.alterados.length ? "Dados atualizados." : "Nada mudou.");
                    setEditando(false);
                    carregar();
                  }}
                />
              ) : (
                <dl className="dados dados-2">
                  {lead.contato_nome && (
                    <>
                      <dt>Pessoa de contato</dt>
                      <dd>{lead.contato_nome}</dd>
                    </>
                  )}
                  <dt>Cadastrado por</dt>
                  <dd>
                    {nomeDe(ficha.cadastro?.criado_por)} em {dataHora(ficha.cadastro?.criado_em)}
                  </dd>
                  <dt>Perfil</dt>
                  <dd>{lead.perfil_id ?? "sem perfil"}</dd>
                  {lead.razao_social !== lead.nome_fantasia && (
                    <>
                      <dt>Razão social</dt>
                      <dd>{lead.razao_social}</dd>
                    </>
                  )}
                  {(lead.logradouro || lead.bairro) && (
                    <>
                      <dt>Endereço</dt>
                      <dd>{[lead.logradouro, lead.bairro].filter(Boolean).join(", ")}</dd>
                    </>
                  )}
                </dl>
              )}
            </section>
          )}

          <section className="bloco">
            <h2>Contatos</h2>
            {!manual && (
              <>
                <h3>Contato original da Receita Federal</h3>
                <table className="tabela tabela-compacta">
                  <tbody>
                    {telefonesReceita.map(([ddd, num], i) => {
                      const d = digitos(`${ddd ?? ""}${num ?? ""}`);
                      return (
                        <tr key={`t${i}`}>
                          <td className="rotulo-linha">Telefone {i + 1}</td>
                          <td className="tabular">{telefone(ddd, num)}</td>
                          <td className="direita">
                            <button type="button" className="botao-texto" onClick={() => usar("telefone", d)}>
                              Ligar
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    <tr>
                      <td className="rotulo-linha">E-mail</td>
                      <td>{r.email ?? <span className="mudo">não informado</span>}</td>
                      <td className="direita">
                        {r.email && (
                          <button type="button" className="botao-texto" onClick={() => usar("email", r.email!.trim())}>
                            Usar
                          </button>
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <h3>Encontrados pelo módulo 3</h3>
              </>
            )}
            {lead.contatos.length === 0 ? (
              <p className="mudo">Nenhum contato {manual ? "cadastrado" : "complementar"}.</p>
            ) : (
              <table className="tabela tabela-compacta">
                <thead>
                  <tr>
                    <th>Tipo</th>
                    <th>Contato</th>
                    <th>Confiança</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {lead.contatos.map((ct, i) => {
                    const canal = canalDoContato(ct);
                    const exibir = ct.tipo === "whatsapp" || ct.tipo === "telefone" ? telefone(null, ct.valor) : ct.valor;
                    return (
                      <tr key={i}>
                        <td className="rotulo-linha">{ROTULO_TIPO[ct.tipo]}</td>
                        <td>
                          {ct.tipo === "site" || ct.tipo === "rede_social" ? (
                            <a
                              href={ficha.simulado ? undefined : ct.valor}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={() => api.evento("ui.abrir_site", lead.chave, { endereco: ct.valor })}
                            >
                              {ct.valor.replace(/^https?:\/\//, "")}
                            </a>
                          ) : (
                            <span className="quebra">{exibir}</span>
                          )}
                          <span className="empresa-meta">
                            {ct.fonte}
                            {ct.observacao && <span className="marcador marcador-alerta">{ct.observacao}</span>}
                          </span>
                        </td>
                        <td>
                          <span className={`confianca confianca-${ct.confianca}`}>{ROTULO_CONFIANCA[ct.confianca]}</span>
                        </td>
                        <td className="direita">
                          {canal && (
                            <button
                              type="button"
                              className="botao-texto"
                              onClick={() => usar(canal, ct.tipo === "whatsapp" || ct.tipo === "telefone" ? digitos(ct.valor) : ct.valor)}
                            >
                              Usar
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>

          {!manual && (
            <section className="bloco">
              <h2>Dados da Receita</h2>
              <dl className="dados">
                <dt>Razão social</dt>
                <dd>{lead.razao_social}</dd>
                <dt>Atividade principal</dt>
                <dd>
                  {lead.cnae_descricao ?? "—"} <span className="mudo tabular">{lead.cnae_principal}</span>
                </dd>
                <dt>Abertura</dt>
                <dd>{data(lead.data_inicio_atividade)}</dd>
                <dt>Situação</dt>
                <dd>{titulo(lead.situacao_cadastral) || "—"}</dd>
                <dt>Porte</dt>
                <dd>{lead.porte ?? "—"}</dd>
                <dt>Capital social</dt>
                <dd className="tabular">{dinheiro(lead.capital_social)}</dd>
                <dt>Estabelecimento</dt>
                <dd>{titulo(lead.matriz_filial) || "—"}</dd>
                <dt>Endereço</dt>
                <dd>
                  {titulo([lead.logradouro, lead.numero].filter(Boolean).join(", "))}
                  {lead.bairro && `, ${titulo(lead.bairro)}`}
                  <br />
                  {titulo(lead.municipio)}/{lead.uf}
                  {lead.cep && `, CEP ${lead.cep.slice(0, 5)}-${lead.cep.slice(5)}`}
                </dd>
              </dl>
            </section>
          )}
        </div>

        <div className="coluna">
          <BlocoTarefas ficha={ficha} aoMudar={carregar} />
          <div id="painel-contato">
            <PainelContato ficha={ficha} escolhaExterna={escolha} aoRegistrar={carregar} />
          </div>
          <BlocoVendas ficha={ficha} aoMudar={carregar} aoRegistrar={() => setVenda("botao")} />
          <PerguntaEmpresa chave={lead.chave} aoResponder={carregar} />
          <LinhaDoTempo chave={lead.chave} atividades={ficha.atividades} aoMudar={carregar} />
        </div>
      </div>

      <DialogoVenda
        aberto={venda !== "fechada"}
        ficha={ficha}
        vindoDoEstagio={venda === "estagio"}
        aoFechar={() => setVenda("fechada")}
        aoConcluir={() => {
          setVenda("fechada");
          carregar();
        }}
      />
    </div>
  );
}

function PerguntaEmpresa({ chave, aoResponder }: { chave: string; aoResponder: () => void }) {
  const [pergunta, setPergunta] = useState("");
  const [resposta, setResposta] = useState<string | null>(null);
  const [esperando, setEsperando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!pergunta.trim()) return;
    setEsperando(true);
    setErro(null);
    try {
      const r = await api.perguntar(pergunta.trim(), chave);
      setResposta(r.resposta);
      setPergunta("");
      aoResponder();
    } catch (err) {
      setErro(err instanceof ApiErro ? err.message : "O agente não respondeu.");
    } finally {
      setEsperando(false);
    }
  }

  return (
    <section className="bloco">
      <h2>Perguntar ao agente</h2>
      <form className="pergunta-form" onSubmit={enviar}>
        <label className="oculto" htmlFor="pergunta-empresa">
          Pergunta sobre este lead
        </label>
        <input
          id="pergunta-empresa"
          placeholder="Ex.: quem costuma decidir compras numa pousada desse porte?"
          value={pergunta}
          onChange={(e) => setPergunta(e.target.value)}
          disabled={esperando}
        />
        <button className="botao" disabled={esperando || !pergunta.trim()}>
          {esperando ? "Aguardando…" : "Perguntar"}
        </button>
      </form>
      {erro && <p className="erro-linha">{erro}</p>}
      {resposta && <p className="resposta">{resposta}</p>}
      <p className="nota">A pergunta vai para o agente com os dados deste lead e fica registrada no histórico.</p>
    </section>
  );
}
