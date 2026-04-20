'use client';

import { useEffect, useMemo, useState } from 'react';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';
import {
  getClientConfigs,
  getFrames,
  getOutputJson,
  getVideoClass,
  listBackendJobs,
  type BackendJobRow,
  type BackendVideoOutput,
  type ClientConfigsBundle,
  type FrameManifest,
  type VideoClassInfo,
} from '@/lib/api';
import BackendFrameGallery from '@/components/evaluation/backend-frame-gallery';
import { backendToPipelineResultJson } from '@/lib/map-backend-to-template';
import type { AssetResult } from '@/components/evaluation/flow-types';

type RunDetail = {
  bv: BackendVideoOutput;
  configs: ClientConfigsBundle | null;
  frames: FrameManifest | null;
  videoClass: VideoClassInfo | null;
};

const formatRunTime = (iso?: string) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

const statusClass = (s: string) => {
  const u = (s || '').toUpperCase();
  if (u === 'COMPLETED') return 'bg-emerald-500/20 text-emerald-200';
  if (u === 'FAILED') return 'bg-rose-500/20 text-rose-200';
  if (u === 'PROCESSING' || u === 'QUEUED') return 'bg-cyan-500/20 text-cyan-200';
  return 'bg-slate-700 text-slate-300';
};

export default function HistoryPage() {
  const { clearHistory, state } = useEvaluationFlow();
  const [backendJobs, setBackendJobs] = useState<BackendJobRow[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoadingJobs(true);
      try {
        const jobs = await listBackendJobs(100);
        if (!cancelled) {
          setBackendJobs(jobs);
          if (!selectedJobId && jobs.length > 0) {
            setSelectedJobId(jobs[0].job_id);
          }
        }
      } catch (e) {
        console.error('Failed to list jobs', e);
      } finally {
        if (!cancelled) setLoadingJobs(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    if (!selectedJobId) return;
    const job = backendJobs.find((j) => j.job_id === selectedJobId);
    if (!job) return;
    if ((job.status || '').toUpperCase() !== 'COMPLETED') return;
    setDetailLoading(true);
    (async () => {
      try {
        const [bv, configs, frames, videoClass] = await Promise.all([
          getOutputJson(selectedJobId),
          getClientConfigs(selectedJobId).catch(() => null),
          getFrames(selectedJobId).catch(() => null),
          getVideoClass(selectedJobId).catch(() => null),
        ]);
        if (!cancelled) setDetail({ bv, configs, frames, videoClass });
      } catch (e) {
        if (!cancelled) setDetailError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backendJobs, selectedJobId]);

  const selectedJob = useMemo(
    () => backendJobs.find((j) => j.job_id === selectedJobId) ?? null,
    [backendJobs, selectedJobId],
  );

  const downloadJson = (filename: string, payload: unknown) => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const obj = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = obj;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(obj);
  };

  const syntheticResult: (AssetResult & {
    jobId?: string;
    frames?: FrameManifest | null;
    videoClass?: VideoClassInfo | null;
  }) | null = detail
    ? (() => {
        const rawJson = backendToPipelineResultJson(detail.bv, detail.configs ?? null);
        return {
          assetId: selectedJob?.job_id ?? 'unknown',
          assetName: detail.bv.filename,
          previewUrl: '',
          kind: 'video',
          rawJson,
          propertyRows: [],
          jobId: selectedJob?.job_id,
          frames: detail.frames,
          videoClass: detail.videoClass,
        };
      })()
    : null;

  return (
    <div className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,#163b84_0%,#08142e_34%,#020814_100%)] px-6 py-8 text-white lg:px-10">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-16 top-12 h-56 w-56 rounded-full bg-cyan-400/18 blur-3xl" />
        <div className="absolute right-0 top-0 h-80 w-80 rounded-full bg-blue-500/20 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-7xl">
        <AppShellHeader />

        <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-[family:var(--font-ibm-plex-sans)] text-[1.75rem] font-semibold tracking-tight text-white md:text-[2.25rem]">
              Run history
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              All jobs tracked in DynamoDB. Select a completed job to review its
              annotated frames and structured output.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-slate-200">
              {backendJobs.length} job{backendJobs.length === 1 ? '' : 's'}
            </span>
            {state.historicalRuns.length > 0 ? (
              <button
                type="button"
                onClick={() => {
                  if (typeof window === 'undefined') return;
                  const ok = window.confirm(
                    'Clear local run history?\n\n' +
                      'This removes session history stored in your browser only. ' +
                      'Backend job records in DynamoDB are preserved.',
                  );
                  if (!ok) return;
                  clearHistory();
                  setSelectedJobId(null);
                }}
                className="rounded-full border border-rose-400/30 bg-rose-500/10 px-5 py-2 text-sm font-semibold text-rose-200 transition hover:bg-rose-500/20"
              >
                Clear local history
              </button>
            ) : null}
          </div>
        </div>

        {loadingJobs ? (
          <div className="mt-8 rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-8 text-center text-sm text-slate-300">
            Loading jobs from DynamoDB…
          </div>
        ) : backendJobs.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-8 text-center">
            <p className="text-lg font-semibold text-white">No jobs yet</p>
            <p className="mt-2 text-sm text-slate-400">
              Run a pipeline from the Upload page and it will appear here.
            </p>
          </div>
        ) : (
          <section className="mt-6 grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)] xl:items-start">
            <aside className="rounded-2xl border border-cyan-500/20 bg-slate-950/82 p-5 backdrop-blur xl:sticky xl:top-6">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">Past jobs</h2>
                <span className="rounded-md bg-white/10 px-2 py-0.5 text-[11px] font-semibold text-slate-300">
                  {backendJobs.length}
                </span>
              </div>
              <div className="mt-4 grid max-h-[72vh] gap-2 overflow-y-auto pr-1">
                {backendJobs.map((job) => {
                  const isSelected = job.job_id === selectedJobId;
                  return (
                    <button
                      key={job.job_id}
                      type="button"
                      onClick={() => setSelectedJobId(job.job_id)}
                      className={`flex items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition ${
                        isSelected
                          ? 'border-cyan-400/50 bg-cyan-500/10'
                          : 'border-slate-700/60 bg-slate-900/50 hover:border-cyan-400/40 hover:bg-slate-900'
                      }`}
                    >
                      <span
                        className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${statusClass(job.status)}`}
                      >
                        {job.status}
                      </span>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-white">{job.filename ?? job.job_id.slice(0, 8)}</p>
                        <p className="mt-0.5 truncate text-[11px] text-slate-500">
                          {job.job_id.slice(0, 8)} · {formatRunTime(job.created_at)}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
              {state.historicalRuns.length > 0 ? (
                <div className="mt-5 border-t border-white/10 pt-4 text-[11px] text-slate-500">
                  Session runs: {state.historicalRuns.length}
                </div>
              ) : null}
            </aside>

            <div className="space-y-6">
              {!selectedJob ? (
                <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-8 text-center text-sm text-slate-400">
                  Select a job from the list to review results.
                </div>
              ) : selectedJob.status !== 'COMPLETED' ? (
                <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-6 text-sm text-amber-200">
                  This job is <b>{selectedJob.status}</b>. Results are only available
                  after the pipeline completes.
                  {selectedJob.error_message ? (
                    <p className="mt-2 font-mono text-xs text-amber-100">
                      {selectedJob.error_message}
                    </p>
                  ) : null}
                </div>
              ) : detailLoading ? (
                <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-6 text-sm text-slate-300">
                  Loading results from S3…
                </div>
              ) : detailError ? (
                <div className="rounded-2xl border border-rose-500/30 bg-rose-500/5 p-6 text-sm text-rose-200">
                  Failed to load this run: {detailError}
                </div>
              ) : syntheticResult ? (
                <BackendFrameGallery
                  result={syntheticResult}
                  onDownloadJson={downloadJson}
                />
              ) : null}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
