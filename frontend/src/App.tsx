import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";

import { api, ApiErro } from "./api";
import { AvisoProvider, useAviso } from "./componentes/Aviso";
import Agenda from "./paginas/Agenda";
import Agente from "./paginas/Agente";
import Auditoria from "./paginas/Auditoria";
import Clientes from "./paginas/Clientes";
import Conta, { TrocaObrigatoria } from "./paginas/Conta";
import Entrar from "./paginas/Entrar";
import Equipe from "./paginas/Equipe";
import Ficha from "./paginas/Ficha";
import Leads from "./paginas/Leads";
import Modelos from "./paginas/Modelos";
import NovoLead from "./paginas/NovoLead";
import Relatorios from "./paginas/Relatorios";
import Supressao from "./paginas/Supressao";
import { ROTULO_PAPEL, SessaoProvider, useSessao } from "./sessao";
import type { Usuario } from "./tipos";

export default function App() {
  const [usuario, setUsuario] = useState<Usuario | null | undefined>(undefined);

  useEffect(() => {
    api.eu().then(setUsuario, () => setUsuario(null));
    const expirou = () => setUsuario(null);
    window.addEventListener("crm:sessao-expirada", expirou);
    return () => window.removeEventListener("crm:sessao-expirada", expirou);
  }, []);

  const sair = useCallback(async () => {
    await api.sair().catch(() => undefined);
    setUsuario(null);
  }, []);

  if (usuario === undefined) return <div className="carregando-tela" aria-busy="true" />;
  if (usuario === null) return <Entrar aoEntrar={setUsuario} />;
  if (usuario.deve_trocar_senha) return <TrocaObrigatoria usuario={usuario} aoTrocar={() => api.eu().then(setUsuario)} aoSair={sair} />;

  return (
    <AvisoProvider>
      <SessaoProvider usuario={usuario} aoMudar={setUsuario}>
        <Moldura aoSair={sair} />
      </SessaoProvider>
    </AvisoProvider>
  );
}

/** Quantos próximos passos estão vencidos ou vencem hoje (atualiza sozinho). */
function useContagemAgenda(ativo: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!ativo) return;
    const atualizar = () => api.contagemTarefas().then((c) => setN(c.atrasadas + c.hoje), () => undefined);
    atualizar();
    const id = window.setInterval(atualizar, 120_000);
    window.addEventListener("crm:agenda-mudou", atualizar);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("crm:agenda-mudou", atualizar);
    };
  }, [ativo]);
  return n;
}

function Moldura({ aoSair }: { aoSair: () => void }) {
  const { usuario, eAdmin, eSuper } = useSessao();
  const semEmpresa = !usuario.empresa;
  const pendentes = useContagemAgenda(!semEmpresa);
  const inicio = semEmpresa ? "/clientes" : "/leads";

  return (
    <div className="moldura">
      <aside className="lateral">
        <div className="marca">
          <span className="marca-simbolo" aria-hidden="true">P</span>
          <span>Prospecção</span>
        </div>
        {eSuper ? <SeletorEmpresa /> : usuario.empresa && <p className="empresa-atual">{usuario.empresa.nome}</p>}
        <nav aria-label="Principal">
          {!semEmpresa && (
            <>
              <NavLink to="/leads">Leads</NavLink>
              <NavLink to="/agenda" className="com-contagem">
                Agenda
                {pendentes > 0 && (
                  <span className="contagem" aria-label={`${pendentes} para hoje ou atrasados`}>
                    {pendentes}
                  </span>
                )}
              </NavLink>
              <NavLink to="/agente">Perguntar ao agente</NavLink>
              <NavLink to="/modelos">Modelos de mensagem</NavLink>
              <NavLink to="/supressao">Não contatar</NavLink>
              {eAdmin && (
                <>
                  <span className="nav-divisor" role="separator" />
                  <NavLink to="/equipe">Equipe</NavLink>
                  <NavLink to="/relatorios">Relatórios</NavLink>
                </>
              )}
            </>
          )}
          {eAdmin && <NavLink to="/auditoria">Auditoria</NavLink>}
          {eSuper && <NavLink to="/clientes">Clientes</NavLink>}
        </nav>
        <div className="lateral-rodape">
          <NavLink to="/conta" className="quem" title="Minha conta">
            <span>{usuario.nome_exibicao}</span>
            <small>{ROTULO_PAPEL[usuario.papel]}</small>
          </NavLink>
          <button type="button" className="botao-texto" onClick={aoSair}>
            Sair
          </button>
        </div>
      </aside>
      <main className="conteudo" key={usuario.empresa?.id ?? "sem-empresa"}>
        <Routes>
          <Route path="/" element={<Navigate to={inicio} replace />} />
          {!semEmpresa && (
            <>
              <Route path="/leads" element={<Leads />} />
              <Route path="/leads/novo" element={<NovoLead />} />
              <Route path="/leads/:chave" element={<Ficha />} />
              <Route path="/agenda" element={<Agenda />} />
              <Route path="/agente" element={<Agente />} />
              <Route path="/modelos" element={<Modelos />} />
              <Route path="/supressao" element={<Supressao />} />
              {eAdmin && <Route path="/equipe" element={<Equipe />} />}
              {eAdmin && <Route path="/relatorios" element={<Relatorios />} />}
            </>
          )}
          {eAdmin && <Route path="/auditoria" element={<Auditoria />} />}
          {eSuper && <Route path="/clientes" element={<Clientes />} />}
          <Route path="/conta" element={<Conta />} />
          <Route path="*" element={<Navigate to={inicio} replace />} />
        </Routes>
      </main>
    </div>
  );
}

/** O superadmin trabalha dentro de uma empresa cliente por vez. */
function SeletorEmpresa() {
  const { usuario, atualizarUsuario } = useSessao();
  const aviso = useAviso();
  const navegar = useNavigate();
  const empresas = usuario.empresas ?? [];
  if (!empresas.length) return <p className="empresa-atual mudo">Nenhuma empresa cliente ainda</p>;

  async function trocar(id: number) {
    try {
      const u = await api.escolherEmpresa(id);
      atualizarUsuario(u);
      navegar("/leads");
      aviso(`Agora você está em ${u.empresa?.nome}.`);
    } catch (e) {
      aviso(e instanceof ApiErro ? e.message : "Não foi possível trocar de empresa.", "erro");
    }
  }

  return (
    <label className="campo seletor-empresa">
      <span>Trabalhando em</span>
      <select value={usuario.empresa?.id ?? ""} onChange={(e) => trocar(Number(e.target.value))}>
        {!usuario.empresa && <option value="">Escolha uma empresa</option>}
        {empresas.map((e) => (
          <option key={e.id} value={e.id}>
            {e.nome}
            {e.ativa ? "" : " (desativada)"}
          </option>
        ))}
      </select>
    </label>
  );
}
