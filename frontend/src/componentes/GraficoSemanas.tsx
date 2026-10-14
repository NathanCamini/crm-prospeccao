import { useEffect, useRef, useState } from "react";

import { reais } from "../formato";

interface Semana {
  semana: string;
  qtd: number;
  centavos: number;
}

const ALTURA = 200;
const MARGEM = { topo: 16, dir: 8, base: 28, esq: 72 };

function compacto(centavos: number): string {
  const v = centavos / 100;
  if (v >= 1_000_000) return `R$ ${(v / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (v >= 1000) return `R$ ${(v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil`;
  return `R$ ${v.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

/** Topo da escala arredondado para um número "limpo" (1, 2 ou 5 × 10^n). */
function topoLimpo(max: number): number {
  if (max <= 0) return 100_00;
  const ordem = 10 ** Math.floor(Math.log10(max));
  for (const m of [1, 2, 5, 10]) if (m * ordem >= max) return m * ordem;
  return 10 * ordem;
}

/** Vendas por semana: uma série, colunas finas, valor no topo da maior, detalhe ao passar o mouse. */
export default function GraficoSemanas({ semanas }: { semanas: Semana[] }) {
  const [foco, setFoco] = useState<number | null>(null);
  const caixa = useRef<HTMLDivElement>(null);
  const [disponivel, setDisponivel] = useState(640);
  useEffect(() => {
    const el = caixa.current;
    if (!el) return;
    const obs = new ResizeObserver(([e]) => setDisponivel(Math.round(e.contentRect.width)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  // O desenho tem a largura real do bloco: textos e colunas não são esticados.
  const largura = Math.max(280, disponivel);
  const topo = topoLimpo(Math.max(...semanas.map((s) => s.centavos), 0));
  const passo = (largura - MARGEM.esq - MARGEM.dir) / Math.max(semanas.length, 1);
  const barra = Math.min(24, passo * 0.6);
  const altUtil = ALTURA - MARGEM.topo - MARGEM.base;
  const y = (c: number) => MARGEM.topo + altUtil - (c / topo) * altUtil;
  const ticks = [0, topo / 2, topo];
  const maior = semanas.reduce((m, s, i) => (s.centavos > (semanas[m]?.centavos ?? 0) ? i : m), 0);
  const cadaRotulo = Math.max(1, Math.ceil((semanas.length * 52) / Math.max(1, largura - MARGEM.esq)));
  const rotuloData = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
  const s = foco !== null ? semanas[foco] : null;

  return (
    <div className="grafico" ref={caixa} onMouseLeave={() => setFoco(null)}>
      <svg viewBox={`0 0 ${largura} ${ALTURA}`} width={largura} height={ALTURA} role="img" aria-label="Vendas por semana no período">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={MARGEM.esq} x2={largura - MARGEM.dir} y1={y(t)} y2={y(t)} className="grade" />
            <text x={MARGEM.esq - 8} y={y(t) + 4} textAnchor="end" className="eixo">
              {compacto(t)}
            </text>
          </g>
        ))}
        {semanas.map((sem, i) => {
          const cx = MARGEM.esq + passo * i + passo / 2;
          const h = Math.max(0, MARGEM.topo + altUtil - y(sem.centavos));
          const r = Math.min(4, h);
          const x0 = cx - barra / 2;
          const base = MARGEM.topo + altUtil;
          // coluna com topo arredondado e base reta
          const caminho = h
            ? `M${x0},${base}V${base - h + r}Q${x0},${base - h} ${x0 + r},${base - h}H${x0 + barra - r}Q${x0 + barra},${base - h} ${x0 + barra},${base - h + r}V${base}Z`
            : "";
          return (
            <g key={sem.semana} onMouseEnter={() => setFoco(i)} onFocus={() => setFoco(i)} tabIndex={0} aria-label={`Semana de ${rotuloData(sem.semana)}: ${reais(sem.centavos)}, ${sem.qtd} vendas`}>
              <rect x={cx - passo / 2} y={MARGEM.topo} width={passo} height={altUtil} className="alvo" />
              {caminho && <path d={caminho} className={`coluna${foco === i ? " em-foco" : ""}`} />}
              {i % cadaRotulo === 0 && (
                <text x={cx} y={ALTURA - 8} textAnchor="middle" className="eixo">
                  {rotuloData(sem.semana)}
                </text>
              )}
              {i === maior && sem.centavos > 0 && foco === null && (
                <text x={cx} y={y(sem.centavos) - 6} textAnchor="middle" className="valor">
                  {compacto(sem.centavos)}
                </text>
              )}
            </g>
          );
        })}
        <line x1={MARGEM.esq} x2={largura - MARGEM.dir} y1={MARGEM.topo + altUtil} y2={MARGEM.topo + altUtil} className="base" />
      </svg>
      {s && foco !== null && (
        <div className="dica" style={{ left: `${((MARGEM.esq + passo * foco + passo / 2) / largura) * 100}%` }} role="status">
          <strong>{reais(s.centavos)}</strong>
          <span>
            {s.qtd} {s.qtd === 1 ? "venda" : "vendas"} na semana de {rotuloData(s.semana)}
          </span>
        </div>
      )}
    </div>
  );
}
