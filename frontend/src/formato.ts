import type { Canal, Estagio } from "./tipos";

export const ROTULO_ESTAGIO: Record<Estagio, string> = {
  novo: "Novo",
  em_contato: "Em contato",
  respondeu: "Respondeu",
  interessado: "Interessado",
  amostra_enviada: "Amostra enviada",
  cliente: "Cliente",
  sem_interesse: "Sem interesse",
  descartado: "Descartado",
};

export const ROTULO_CANAL: Record<Canal, string> = {
  email: "E-mail",
  whatsapp: "WhatsApp",
  formulario: "Formulário",
  telefone: "Ligação",
};

const MINUSCULAS = new Set(["de", "da", "do", "das", "dos", "e"]);

export function titulo(texto: string | null | undefined): string {
  if (!texto) return "";
  return texto
    .toLowerCase()
    .split(/\s+/)
    .map((p, i) => (i && MINUSCULAS.has(p) ? p : p.charAt(0).toUpperCase() + p.slice(1)))
    .join(" ");
}

export function nomeEmpresa(l: { nome_fantasia: string | null; razao_social: string }): string {
  return titulo(l.nome_fantasia || l.razao_social);
}

export function cnpj(v: string): string {
  if (v.length !== 14) return v;
  return `${v.slice(0, 2)}.${v.slice(2, 5)}.${v.slice(5, 8)}/${v.slice(8, 12)}-${v.slice(12)}`;
}

export const digitos = (v: string | null | undefined) => (v ?? "").replace(/\D/g, "");

export function telefone(ddd: string | null | undefined, numero: string | null | undefined): string {
  let d = digitos(`${ddd ?? ""}${numero ?? ""}`);
  if (d.startsWith("55") && d.length >= 12) d = d.slice(2);
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return d;
}

export function data(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}

export function dataHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function relativo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const dias = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (dias <= 0) return "hoje";
  if (dias === 1) return "ontem";
  if (dias < 30) return `há ${dias} dias`;
  return data(iso);
}

export function diasDesde(isoData: string | null | undefined): number | null {
  if (!isoData) return null;
  return Math.floor((Date.now() - new Date(`${isoData}T12:00:00`).getTime()) / 86_400_000);
}

export function reais(centavos: number | null | undefined): string {
  if (centavos == null) return "—";
  return (centavos / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

/** Data local (AAAA-MM-DD) de hoje, no fuso do navegador. */
export function hojeIso(deslocamentoDias = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + deslocamentoDias);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function quandoVence(iso: string): { texto: string; atrasada: boolean; hoje: boolean } {
  const dias = Math.round((new Date(`${iso}T12:00:00`).getTime() - new Date(`${hojeIso()}T12:00:00`).getTime()) / 86_400_000);
  if (dias < 0) return { texto: dias === -1 ? "venceu ontem" : `atrasada ${-dias} dias`, atrasada: true, hoje: false };
  if (dias === 0) return { texto: "hoje", atrasada: false, hoje: true };
  if (dias === 1) return { texto: "amanhã", atrasada: false, hoje: false };
  if (dias < 7) return { texto: `em ${dias} dias`, atrasada: false, hoje: false };
  return { texto: data(iso), atrasada: false, hoje: false };
}

export function dinheiro(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
}

export function mesReferencia(v: string | null | undefined): string {
  if (!v) return "—";
  const [a, m] = v.split("-");
  const nomes = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  return `${nomes[Number(m) - 1]} de ${a}`;
}

/** Mesmo cálculo do backend; montado no clique para o navegador não bloquear a janela. */
export function montarUrl(canal: Canal, destino: string, texto: string, assunto?: string): string | null {
  const intl = () => {
    let d = digitos(destino);
    if (!d.startsWith("55") || d.length <= 11) d = `55${d}`;
    return d;
  };
  if (canal === "whatsapp") return `https://wa.me/${intl()}?text=${encodeURIComponent(texto)}`;
  if (canal === "email") return `mailto:${destino}?subject=${encodeURIComponent(assunto ?? "")}&body=${encodeURIComponent(texto)}`;
  if (canal === "telefone") return `tel:+${intl()}`;
  if (canal === "formulario") return /^https?:\/\//.test(destino) ? destino : null;
  return null;
}
