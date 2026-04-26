'use client';

import type { RunProgress, UploadedAsset } from '@/components/evaluation/flow-types';

type RunProgressProps = {
  progress: RunProgress;
  assets: Array<UploadedAsset>;
};

export default function RunProgress({ progress, assets }: RunProgressProps) {
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-5 backdrop-blur">
        <div className="h-2.5 rounded-full bg-slate-900/90">
          <div
            className="h-2.5 rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 transition-all"
            style={{ width: `${progress.percent}%` }}
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs font-medium text-slate-400">
          <span className="capitalize">{progress.phase}</span>
          <span>{Math.round(progress.percent)}%</span>
        </div>
      </div>

      <div className="grid gap-2">
        {assets.length === 0 ? (
          <div className="rounded-xl border border-slate-700/60 bg-slate-900/50 px-4 py-3">
            <p className="text-sm font-medium text-white">Evaluation benchmark</p>
            <p className="mt-0.5 text-xs text-slate-400">Running the built-in metrics benchmark and GT coverage summary.</p>
          </div>
        ) : null}

        {assets.map((asset) => {
          const isActive = asset.id === progress.activeAssetId;

          return (
            <div
              key={asset.id}
              className={`flex items-center justify-between rounded-xl border px-4 py-3 ${
                isActive ? 'border-cyan-400/50 bg-cyan-500/5' : 'border-slate-700/60 bg-slate-900/50'
              }`}
            >
              <div>
                <p className="text-sm font-medium text-white">{asset.name}</p>
                <p className="mt-0.5 text-xs text-slate-500">{asset.kind}</p>
              </div>

              <span
                className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ${
                  asset.status === 'completed'
                    ? 'bg-emerald-500/12 text-emerald-300'
                    : asset.status === 'running'
                      ? 'bg-cyan-500/12 text-cyan-300'
                      : asset.status === 'queued'
                        ? 'bg-amber-500/12 text-amber-300'
                        : 'bg-white/5 text-slate-400'
                }`}
              >
                {asset.status}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
