'use client';

import { useEffect, useMemo, useState } from 'react';
import Image from 'next/image';
import type { AssetResult } from '@/components/evaluation/flow-types';
import type { FrameManifest, FrameManifestEntry, VideoClassInfo } from '@/lib/api';

type Props = {
  result: (AssetResult & { jobId?: string; frames?: FrameManifest | null; videoClass?: VideoClassInfo | null }) | null;
  onDownloadJson: (filename: string, payload: unknown) => void;
};

const downloadRemote = async (url: string, filename: string) => {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const obj = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = obj;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(obj);
  } catch (e) {
    console.error('Download failed', e);
  }
};

function FrameThumb({
  frame,
  isActive,
  onSelect,
}: {
  frame: FrameManifestEntry;
  isActive: boolean;
  onSelect: () => void;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`shrink-0 rounded-xl border p-2 transition ${
        isActive
          ? 'border-cyan-400 bg-cyan-500/10'
          : 'border-slate-700 bg-slate-900/70 hover:border-cyan-400/60'
      }`}
    >
      {frame.annotated_url && !failed ? (
        <div className="relative inline-block">
          <Image
            src={frame.annotated_url}
            alt={`Frame ${frame.frame_idx}`}
            width={160}
            height={90}
            unoptimized
            onError={() => setFailed(true)}
            className="h-20 w-32 rounded-md object-cover"
          />
          {frame.extraction_status === 'substituted' ? (
            <span className="absolute left-1 top-1 rounded bg-amber-600/95 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white shadow">
              Substituted
            </span>
          ) : null}
        </div>
      ) : (
        <div className="flex h-20 w-32 items-center justify-center rounded-md bg-slate-800 text-[10px] text-slate-400">
          {failed ? 'Failed to load' : 'No image'}
        </div>
      )}
      <p className="mt-1.5 text-[11px] font-semibold text-white">
        Frame {frame.frame_idx}
      </p>
      <p className="text-[10px] text-slate-400">
        t={Math.round(frame.timestamp_ms)}ms · {frame.num_objects} obj
      </p>
    </button>
  );
}

function ActiveFrameView({
  frame,
  assetName,
}: {
  frame: FrameManifestEntry | null;
  assetName: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [frame?.frame_idx, frame?.annotated_url]);

  if (!frame?.annotated_url) {
    return (
      <div className="flex h-80 items-center justify-center text-xs text-slate-400">
        No annotated image available for this frame
      </div>
    );
  }

  if (failed) {
    return (
      <div className="flex h-80 flex-col items-center justify-center gap-3 px-4 text-center text-sm text-amber-200">
        <p>Failed to load annotated frame.</p>
        <p className="text-xs text-amber-100">
          The S3 object may have expired or isn&apos;t accessible from
          your network yet.
        </p>
        {frame.raw_url ? (
          <a
            href={frame.raw_url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-full border border-cyan-400/40 bg-cyan-500/10 px-3 py-1 text-[11px] font-semibold text-cyan-200 hover:bg-cyan-500/20"
          >
            Open raw frame in new tab
          </a>
        ) : null}
      </div>
    );
  }

  return (
    <a
      href={frame.annotated_url}
      target="_blank"
      rel="noopener noreferrer"
      title="Open annotated frame full-size in a new tab"
      className="block"
    >
      <Image
        src={frame.annotated_url}
        alt={`Annotated frame ${frame.frame_idx} of ${assetName}`}
        width={1920}
        height={1080}
        unoptimized
        onError={() => setFailed(true)}
        className="h-auto max-h-[70vh] w-full rounded-xl object-contain transition hover:brightness-110"
      />
    </a>
  );
}

export default function BackendFrameGallery({ result, onDownloadJson }: Props) {
  const frames: FrameManifestEntry[] = useMemo(
    () => result?.frames?.frames ?? [],
    [result?.frames],
  );
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    setActiveIdx(0);
  }, [result?.jobId]);

  if (!result) return null;
  const activeFrame = frames[activeIdx] ?? null;

  const videoClass = result.videoClass?.display_label ?? '—';
  const jobId = result.jobId ?? '—';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-5">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-300">
            Video classification
          </p>
          <h3 className="mt-1 font-[family:var(--font-ibm-plex-sans)] text-xl font-semibold text-white">
            {videoClass}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            job_id: <span className="font-mono text-slate-200">{jobId}</span> ·{' '}
            {frames.length} annotated frame{frames.length === 1 ? '' : 's'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onDownloadJson(
              `${result.assetName.replace(/\.[^.]+$/, '')}.json`,
              result.rawJson,
            )}
            className="rounded-full border border-cyan-400/40 bg-cyan-500/10 px-4 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/20"
          >
            Download video JSON
          </button>
          {activeFrame?.json_url ? (
            <button
              type="button"
              onClick={() => downloadRemote(
                activeFrame.json_url ?? '',
                `${result.assetName.replace(/\.[^.]+$/, '')}_frame_${activeFrame.frame_idx}.json`,
              )}
              className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-white/10"
            >
              Download frame JSON
            </button>
          ) : null}
          {activeFrame?.annotated_url ? (
            <button
              type="button"
              onClick={() => downloadRemote(
                activeFrame.annotated_url ?? '',
                `${result.assetName.replace(/\.[^.]+$/, '')}_frame_${activeFrame.frame_idx}.png`,
              )}
              className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs font-semibold text-slate-200 hover:bg-white/10"
            >
              Download frame PNG
            </button>
          ) : null}
        </div>
      </div>

      {frames.length === 0 ? (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-5 text-sm text-amber-200">
          The backend has not persisted per-frame artefacts for this job yet.
          Re-run the pipeline or check the CloudWatch logs if this persists.
        </div>
      ) : (
        <>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {frames.map((f, idx) => (
              <FrameThumb
                key={f.frame_idx}
                frame={f}
                isActive={idx === activeIdx}
                onSelect={() => setActiveIdx(idx)}
              />
            ))}
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-4">
              <ActiveFrameView frame={activeFrame} assetName={result.assetName} />
              {activeFrame?.annotated_url ? (
                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                  <span>
                    Frame {activeFrame.frame_idx} · {Math.round(activeFrame.timestamp_ms)}ms ·{' '}
                    {activeFrame.num_objects} object{activeFrame.num_objects === 1 ? '' : 's'}
                    {activeFrame.width && activeFrame.height
                      ? ` · ${activeFrame.width}×${activeFrame.height}`
                      : ''}
                    {activeFrame.extraction_source === 'substituted'
                      ? ' · substituted'
                      : ''}
                  </span>
                  <a
                    href={activeFrame.annotated_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-full border border-cyan-400/40 bg-cyan-500/10 px-3 py-1 font-semibold text-cyan-200 hover:bg-cyan-500/20"
                  >
                    Open full size
                  </a>
                </div>
              ) : null}
            </div>

            <aside className="max-h-[70vh] overflow-y-auto rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-4">
              <h4 className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
                Frame {activeFrame?.frame_idx} JSON
              </h4>
              <FrameJsonViewer jsonUrl={activeFrame?.json_url ?? null} />
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

function FrameJsonViewer({ jsonUrl }: { jsonUrl: string | null }) {
  const [data, setData] = useState<unknown | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(null);
    if (!jsonUrl) return;
    (async () => {
      try {
        const res = await fetch(jsonUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const j = await res.json();
        if (!cancelled) setData(j);
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jsonUrl]);

  if (!jsonUrl) {
    return <p className="mt-3 text-xs text-slate-400">No per-frame JSON URL.</p>;
  }
  if (err) {
    return <p className="mt-3 text-xs text-rose-300">Failed to load JSON: {err}</p>;
  }
  if (data === null) {
    return <p className="mt-3 text-xs text-slate-400">Loading…</p>;
  }
  return (
    <pre className="mt-3 max-h-[60vh] overflow-auto rounded-lg bg-slate-900/70 p-3 text-[11px] leading-relaxed text-slate-200">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
