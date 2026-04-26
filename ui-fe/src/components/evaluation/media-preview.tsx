'use client';

import Image from 'next/image';
import type { UploadedAsset } from '@/components/evaluation/flow-types';

type MediaPreviewProps = {
  asset: UploadedAsset | null;
};

const formatBytes = (bytes: number) => {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export default function MediaPreview({ asset }: MediaPreviewProps) {
  if (!asset) {
    return (
      <div className="rounded-[28px] border border-cyan-500/20 bg-slate-950/78 p-8 shadow-[0_20px_50px_rgba(2,8,20,0.35)]">
        <div className="flex min-h-[320px] items-center justify-center rounded-[24px] border border-dashed border-cyan-500/20 bg-slate-900/70 text-center">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-300">Preview</p>
            <p className="mt-3 text-sm text-slate-300">Upload a file to see a preview before running the pipeline.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-[28px] border border-cyan-500/20 bg-slate-950/78 p-5 shadow-[0_20px_50px_rgba(2,8,20,0.35)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">Preview</p>
          <h3 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">
            {asset.name}
          </h3>
        </div>
        <div className="rounded-2xl bg-white/10 px-4 py-3 text-xs font-semibold text-slate-300">
          {asset.kind} · {formatBytes(asset.size)}
        </div>
      </div>

      <div className="mt-5 overflow-hidden rounded-[24px] bg-slate-950">
        {!asset.previewUrl ? (
          <div className="flex h-[200px] items-center justify-center text-center">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-300">S3 source</p>
              <p className="mt-2 font-mono text-sm text-slate-300">{asset.s3Uri}</p>
              <p className="mt-3 text-xs text-slate-500">
                Browser preview is unavailable for S3 objects. The backend will download and process this URI.
              </p>
            </div>
          </div>
        ) : asset.kind === 'image' ? (
          <Image
            src={asset.previewUrl}
            alt={asset.name}
            width={asset.width ?? 1280}
            height={asset.height ?? 720}
            unoptimized
            className="h-[360px] w-full object-contain"
          />
        ) : (
          <video
            src={asset.previewUrl}
            controls
            className="h-[360px] w-full bg-black object-contain"
          />
        )}
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-white/5 bg-slate-900/80 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Type</p>
          <p className="mt-2 text-sm font-semibold text-white">{asset.kind}</p>
        </div>
        <div className="rounded-2xl border border-white/5 bg-slate-900/80 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Resolution</p>
          <p className="mt-2 text-sm font-semibold text-white">
            {asset.width && asset.height ? `${asset.width} x ${asset.height}` : 'Not available'}
          </p>
        </div>
        <div className="rounded-2xl border border-white/5 bg-slate-900/80 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Duration</p>
          <p className="mt-2 text-sm font-semibold text-white">
            {asset.durationSec ? `${asset.durationSec}s` : asset.kind === 'image' ? 'Static image' : 'Not available'}
          </p>
        </div>
        <div className="rounded-2xl border border-white/5 bg-slate-900/80 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">MIME type</p>
          <p className="mt-2 truncate text-sm font-semibold text-white">{asset.type}</p>
        </div>
      </div>
    </div>
  );
}
