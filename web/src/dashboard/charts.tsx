/* Greyscale SVG charts. No chart library: every chart is a few dozen lines
   and draws real numbers from the scan, in the two-tone bar / soft line /
   donut idiom of the reference design. */

export interface Point { label: string; value: number; hint?: string }

function ticks(max: number, n = 4): number[] {
  const raw = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag;
  return Array.from({ length: n + 1 }, (_, i) => i * step);
}

const short = (v: number) => (v >= 1000 ? `${Math.round(v / 100) / 10}k` : String(Math.round(v * 10) / 10));

/** Two-tone bars: alternating light and dark, rounded tops, y grid. */
export function Bars({ data, height = 200, format = short }: { data: Point[]; height?: number; format?: (v: number) => string }) {
  const W = 600, H = height, padL = 44, padB = 28, padT = 8;
  const max = Math.max(1, ...data.map((d) => d.value));
  const ys = ticks(max);
  const top = ys[ys.length - 1] || 1;
  const innerW = W - padL - 8, innerH = H - padB - padT;
  const slot = innerW / Math.max(1, data.length);
  const bw = Math.min(28, slot * 0.55);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="bar chart">
      {ys.map((y) => {
        const yy = padT + innerH - (y / top) * innerH;
        return (
          <g key={y}>
            <line x1={padL} x2={W - 8} y1={yy} y2={yy} stroke="var(--line)" />
            <text x={padL - 8} y={yy + 4} textAnchor="end" fontSize="11" fill="var(--faint)">{format(y)}</text>
          </g>
        );
      })}
      {data.map((d, i) => {
        const h = (d.value / top) * innerH;
        const x = padL + slot * i + (slot - bw) / 2;
        return (
          <g key={i}>
            <title>{d.hint || `${d.label}: ${d.value}`}</title>
            <rect x={x} y={padT + innerH - h} width={bw} height={Math.max(2, h)} rx={4} fill={i % 2 ? "var(--bar-dark)" : "var(--bar-light)"} />
            <text x={x + bw / 2} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--muted-ink)">
              {d.label.length > 9 ? d.label.slice(0, 8) + "…" : d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Soft line with a marker and tooltip on the highest point. */
export function Line({ data, height = 220, format = short }: { data: Point[]; height?: number; format?: (v: number) => string }) {
  const W = 640, H = height, padL = 44, padB = 40, padT = 12, padR = 16;
  if (!data.length) return null;
  const max = Math.max(1, ...data.map((d) => d.value));
  const ys = ticks(max);
  const top = ys[ys.length - 1] || 1;
  const innerW = W - padL - padR, innerH = H - padB - padT;
  const px = (i: number) => padL + (data.length === 1 ? innerW / 2 : (i / (data.length - 1)) * innerW);
  const py = (v: number) => padT + innerH - (v / top) * innerH;
  const pts = data.map((d, i) => [px(i), py(d.value)] as const);
  // Catmull-Rom → cubic Bézier for the soft curve of the reference
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C ${c1[0]} ${c1[1]}, ${c2[0]} ${c2[1]}, ${p2[0]} ${p2[1]}`;
  }
  const peak = data.reduce((m, x, i) => (x.value > data[m].value ? i : m), 0);
  const [mx, my] = pts[peak];
  const tipW = 132, tipX = Math.min(Math.max(mx - tipW / 2, padL), W - padR - tipW);
  const tipY = my - 70 > padT ? my - 70 : my + 18;   // above the marker when there is room
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="line chart">
      {ys.map((y) => (
        <g key={y}>
          <line x1={padL} x2={W - padR} y1={py(y)} y2={py(y)} stroke="var(--line)" />
          <text x={padL - 8} y={py(y) + 4} textAnchor="end" fontSize="11" fill="var(--faint)">{format(y)}</text>
        </g>
      ))}
      {data.map((_, i) => (
        <line key={i} x1={px(i)} x2={px(i)} y1={padT} y2={padT + innerH} stroke="var(--line)" strokeDasharray="2 4" />
      ))}
      <path d={d} fill="none" stroke="var(--bar-light)" strokeWidth={3} strokeLinecap="round" />
      {data.map((p, i) => (
        <g key={i}>
          <title>{p.hint || `${p.label}: ${p.value}`}</title>
          <circle cx={px(i)} cy={padT + innerH + 16} r={5} fill={i === peak ? "var(--bar-light)" : "none"} stroke="var(--muted-ink)" strokeWidth={1.5} />
          <text x={px(i)} y={H - 4} textAnchor="middle" fontSize="11" fill="var(--muted-ink)">{p.label}</text>
        </g>
      ))}
      <circle cx={mx} cy={my} r={7} fill="var(--card)" stroke="var(--bar-light)" strokeWidth={3} />
      <g>
        <rect x={tipX} y={tipY} width={tipW} height={52} rx={10} fill="#ffffff" />
        <text x={tipX + tipW / 2} y={tipY + 22} textAnchor="middle" fontSize="14" fontWeight="500" fill="#111">{format(data[peak].value)}</text>
        <text x={tipX + tipW / 2} y={tipY + 40} textAnchor="middle" fontSize="10.5" fill="#666">
          {(data[peak].hint || data[peak].label).slice(0, 24)}
        </text>
      </g>
    </svg>
  );
}

/** Donut with a percentage in the middle. */
export function Donut({ pct, label, size = 120 }: { pct: number | null; label: string; size?: number }) {
  const r = 42, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct ?? 0));
  return (
    <div className="flex flex-col items-center gap-3">
      <svg viewBox="0 0 120 120" width={size} height={size} role="img" aria-label={`${label} ${pct ?? "?"}%`}>
        <circle cx="60" cy="60" r={r} fill="none" stroke="var(--bar-dark)" strokeWidth="14" />
        <circle cx="60" cy="60" r={r} fill="none" stroke="var(--bar-light)" strokeWidth="14" strokeLinecap="round"
          strokeDasharray={`${(p / 100) * c} ${c}`} transform="rotate(-90 60 60)" />
        <text x="60" y="65" textAnchor="middle" fontSize="16" fontWeight="500" fill="var(--text)">{pct == null ? "—" : `${Math.round(pct)}%`}</text>
      </svg>
      <div className="text-center text-[13px] leading-tight text-ink">{label}</div>
    </div>
  );
}
