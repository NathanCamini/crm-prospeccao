import { useState, type FormEvent } from "react";

import { ApiErro } from "../api";
import type { DadosManuais, Perfil } from "../tipos";

const UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split(" ");

interface Props {
  inicial?: DadosManuais;
  perfis: Perfil[];
  rotulo: string;
  aoEnviar: (d: DadosManuais) => Promise<void>;
  aoCancelar: () => void;
  /** Quando o CNPJ já existe, o formulário mostra o link para o lead existente. */
  aoDuplicado?: (chave: string) => void;
}

export default function FormLeadManual({ inicial, perfis, rotulo, aoEnviar, aoCancelar, aoDuplicado }: Props) {
  const [d, setD] = useState<DadosManuais>(inicial ?? { nome: "" });
  const [erro, setErro] = useState<string | null>(null);
  const [duplicado, setDuplicado] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const campo = (k: keyof DadosManuais) => ({
    value: (d[k] as string | null | undefined) ?? "",
    onChange: (e: { target: { value: string } }) => setD({ ...d, [k]: e.target.value }),
  });

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setDuplicado(null);
    setEnviando(true);
    try {
      await aoEnviar(d);
    } catch (err) {
      if (err instanceof ApiErro) {
        setErro(err.message);
        if (err.status === 409 && typeof err.extra?.chave === "string") setDuplicado(err.extra.chave);
      } else setErro("Não foi possível salvar.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="form-lead" onSubmit={enviar}>
      <fieldset>
        <legend>Empresa</legend>
        <div className="grade-form">
          <label className="campo campo-duplo">
            <span>Nome (como a empresa é conhecida)</span>
            <input required minLength={2} autoFocus {...campo("nome")} placeholder="Ex.: Pousada da Serra" />
          </label>
          <label className="campo">
            <span>CNPJ (opcional)</span>
            <input {...campo("cnpj")} inputMode="text" placeholder="00.000.000/0000-00" />
          </label>
          <label className="campo">
            <span>Razão social (opcional)</span>
            <input {...campo("razao_social")} />
          </label>
          {perfis.length > 0 && (
            <label className="campo">
              <span>Perfil</span>
              <select {...campo("perfil_id")}>
                <option value="">Sem perfil</option>
                {perfis.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.nome}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </fieldset>

      <fieldset>
        <legend>Contato (pelo menos um)</legend>
        <div className="grade-form">
          <label className="campo">
            <span>Pessoa de contato</span>
            <input {...campo("contato_nome")} placeholder="Ex.: Marta, gerente" />
          </label>
          <label className="campo">
            <span>Telefone</span>
            <input {...campo("telefone")} inputMode="tel" placeholder="(54) 3282-1000" />
          </label>
          <label className="campo">
            <span>WhatsApp</span>
            <input {...campo("whatsapp")} inputMode="tel" placeholder="(54) 99999-0000" />
          </label>
          <label className="campo">
            <span>E-mail</span>
            <input {...campo("email")} type="email" />
          </label>
          <label className="campo">
            <span>Site</span>
            <input {...campo("site")} placeholder="www.empresa.com.br" />
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Endereço</legend>
        <div className="grade-form">
          <label className="campo campo-duplo">
            <span>Logradouro e número</span>
            <input {...campo("logradouro")} />
          </label>
          <label className="campo">
            <span>Bairro</span>
            <input {...campo("bairro")} />
          </label>
          <label className="campo">
            <span>Município</span>
            <input {...campo("municipio")} />
          </label>
          <label className="campo campo-uf">
            <span>UF</span>
            <select {...campo("uf")}>
              <option value="">—</option>
              {UFS.map((u) => (
                <option key={u}>{u}</option>
              ))}
            </select>
          </label>
        </div>
      </fieldset>

      {!inicial && (
        <label className="campo">
          <span>Como chegou até esse lead (vai para o histórico)</span>
          <textarea rows={2} {...campo("observacao")} placeholder="Ex.: indicação do hotel vizinho, feira de turismo…" />
        </label>
      )}

      {erro && (
        <p className="erro-linha" role="alert">
          {erro}{" "}
          {duplicado && aoDuplicado && (
            <button type="button" className="botao-texto" onClick={() => aoDuplicado(duplicado)}>
              Abrir o lead existente
            </button>
          )}
        </p>
      )}
      <div className="acoes">
        <button className="botao botao-primario" disabled={enviando}>
          {enviando ? "Salvando…" : rotulo}
        </button>
        <button type="button" className="botao" onClick={aoCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
