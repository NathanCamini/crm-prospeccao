import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { useAviso } from "../componentes/Aviso";
import FormLeadManual from "../componentes/FormLeadManual";
import type { Perfil } from "../tipos";

export default function NovoLead() {
  const navegar = useNavigate();
  const aviso = useAviso();
  const [perfis, setPerfis] = useState<Perfil[]>([]);
  useEffect(() => {
    api.perfis().then(setPerfis, () => setPerfis([]));
  }, []);

  return (
    <div className="pagina pagina-estreita">
      <Link to="/leads" className="voltar">
        Voltar para leads
      </Link>
      <header className="cabecalho">
        <div>
          <h1>Cadastrar lead</h1>
          <p className="sub">
            Para empresas que não vieram do agente: uma indicação, um contato de feira, um cliente antigo. Só a sua empresa vê este
            cadastro, e ele fica com você como responsável.
          </p>
        </div>
      </header>
      <section className="bloco">
        <FormLeadManual
          perfis={perfis}
          rotulo="Cadastrar lead"
          aoCancelar={() => navegar("/leads")}
          aoDuplicado={(chave) => navegar(`/leads/${chave}`)}
          aoEnviar={async (d) => {
            const r = await api.criarManual(d);
            aviso("Lead cadastrado.");
            navegar(`/leads/${r.chave}`);
          }}
        />
      </section>
    </div>
  );
}
