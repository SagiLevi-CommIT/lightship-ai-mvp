'use client';

import type { AssetResult } from '@/components/evaluation/flow-types';

type ResultsPropertiesPanelProps = {
  result: AssetResult;
};

export default function ResultsPropertiesPanel({ result }: ResultsPropertiesPanelProps) {
  return (
    <div className="rounded-[32px] border border-cyan-500/20 bg-slate-950/78 p-6 shadow-[0_24px_64px_rgba(2,8,20,0.35)] backdrop-blur">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">Output properties</p>
        <h2 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">
          Structured result summary
        </h2>
        <p className="mt-2 text-sm leading-7 text-slate-300">
          Each generated JSON property is shown below with its extracted output value.
        </p>
      </div>

      <div className="mt-6 overflow-hidden rounded-[28px] border border-white/10 bg-slate-900/80">
        {result.propertyRows.map((row) => (
          <div
            key={row.label}
            className="grid gap-2 border-b border-white/5 px-5 py-4 last:border-b-0 md:grid-cols-[0.36fr_0.64fr] md:items-start"
          >
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">{row.label}</p>
            <p className="text-sm leading-6 text-white">{row.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
