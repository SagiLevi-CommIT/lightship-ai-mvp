'use client';

import type { UploadedAsset } from '@/components/evaluation/flow-types';

type UploadQueueProps = {
  assets: Array<UploadedAsset>;
  selectedAssetId: string | null;
  onSelect: (assetId: string) => void;
  onRemove: (assetId: string) => void;
};

const formatBytes = (bytes: number) => {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export default function UploadQueue({
  assets,
  selectedAssetId,
  onSelect,
  onRemove,
}: UploadQueueProps) {
  return (
    <div className="rounded-[24px] border border-cyan-500/20 bg-slate-950/78 p-4 shadow-[0_20px_50px_rgba(2,8,20,0.35)]">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-[family:var(--font-ibm-plex-sans)] text-lg font-semibold text-white">Upload queue</h3>
        <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-slate-300">
          {assets.length} file{assets.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="mt-4 flex gap-3 overflow-x-auto pb-1">
        {assets.map((asset) => {
          const isSelected = asset.id === selectedAssetId;

          return (
            <button
              key={asset.id}
              type="button"
              data-test-id={`queue-item-${asset.id}`}
              onClick={() => onSelect(asset.id)}
              className={`group flex min-w-[240px] max-w-[240px] items-center justify-between rounded-2xl border px-4 py-3 text-left transition ${
                isSelected
                  ? 'border-cyan-400 bg-cyan-500/10 shadow-[0_0_16px_rgba(34,211,238,0.12)]'
                  : 'border-slate-700 bg-slate-900/70 hover:border-cyan-400/70 hover:bg-slate-900'
              }`}
            >
              <div className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-semibold text-white">{asset.name}</p>
                <p className="mt-1 truncate text-xs text-slate-400">
                  {formatBytes(asset.size)}
                  {asset.durationSec ? ` · ${asset.durationSec}s` : ''}
                </p>
              </div>

              <span
                role="button"
                tabIndex={0}
                onClick={(event) => {
                  event.stopPropagation();
                  onRemove(asset.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    event.stopPropagation();
                    onRemove(asset.id);
                  }
                }}
                className="ml-3 inline-flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-sm font-semibold text-slate-300 hover:bg-rose-500/20 hover:text-rose-200"
              >
                ×
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
