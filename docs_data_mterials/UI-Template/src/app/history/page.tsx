'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import EvaluationReportResults from '@/components/evaluation/evaluation-report-results';
import ResultsFrameGallery from '@/components/evaluation/results-frame-gallery';
import ResultsPropertiesPanel from '@/components/evaluation/results-properties-panel';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';

const formatRunTime = (timestamp: number) =>
  new Date(timestamp).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });

export default function HistoryPage() {
  const { state } = useEvaluationFlow();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(state.historicalRuns[0]?.runId ?? null);
  const selectedRun = useMemo(
    () => state.historicalRuns.find((run) => run.runId === selectedRunId) ?? state.historicalRuns[0] ?? null,
    [selectedRunId, state.historicalRuns],
  );
  const resultEntries = useMemo(() => Object.values(selectedRun?.resultsByAssetId ?? {}), [selectedRun]);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(resultEntries[0]?.assetId ?? null);
  const activeAssetId = resultEntries.some((result) => result.assetId === selectedAssetId)
    ? selectedAssetId
    : resultEntries[0]?.assetId ?? null;
  const selectedResult = resultEntries.find((result) => result.assetId === activeAssetId) ?? resultEntries[0] ?? null;

  const handleDownloadJson = (filename: string, payload: unknown) => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(objectUrl);
  };

  const handleDownloadAllJson = () => {
    resultEntries.forEach((result) => {
      handleDownloadJson(result.assetName.replace(/\.[^.]+$/, '.json'), result.rawJson);
    });
  };

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
              Historical Runs
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              Review previous pipeline executions and inspect saved results.
            </p>
          </div>
          <span className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-slate-200">
            {state.historicalRuns.length} run{state.historicalRuns.length === 1 ? '' : 's'}
          </span>
        </div>

        {state.historicalRuns.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-8 text-center">
            <p className="text-lg font-semibold text-white">No historical runs yet</p>
            <p className="mt-2 text-sm text-slate-400">Run a pipeline first, then it will appear here automatically.</p>
            <Link
              href="/"
              className="mt-5 inline-flex rounded-full border border-cyan-400/30 bg-cyan-500/10 px-5 py-2.5 text-sm font-semibold text-cyan-200 transition hover:bg-cyan-500/20"
            >
              New Pipeline
            </Link>
          </div>
        ) : (
          <section className="mt-6 grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)] xl:items-start">
            <aside className="rounded-2xl border border-cyan-500/20 bg-slate-950/82 p-5 backdrop-blur xl:sticky xl:top-6">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">Past Runs</h2>
                <span className="rounded-md bg-white/10 px-2 py-0.5 text-[11px] font-semibold text-slate-300">
                  {state.historicalRuns.length}
                </span>
              </div>

              <div className="mt-4 grid gap-2">
                {state.historicalRuns.map((run, index) => {
                  const isSelected = run.runId === selectedRun?.runId;

                  return (
                    <button
                      key={run.runId}
                      type="button"
                      onClick={() => setSelectedRunId(run.runId)}
                      className={`flex items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition ${
                        isSelected
                          ? 'border-cyan-400/50 bg-cyan-500/10'
                          : 'border-slate-700/60 bg-slate-900/50 hover:border-cyan-400/40 hover:bg-slate-900'
                      }`}
                    >
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-cyan-500/15 text-[11px] font-semibold text-cyan-200">
                        {index + 1}
                      </span>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-white">Run {run.runId.slice(0, 8)}</p>
                        <p className="mt-0.5 text-[11px] text-slate-500">
                          {run.mode === 'evaluation' ? 'Evaluation' : `${run.assetCount} video${run.assetCount === 1 ? '' : 's'}`}
                          {' · '}
                          {formatRunTime(run.completedAt)}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            </aside>

            {selectedRun ? (
              <div className="space-y-6">
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-cyan-500/20 bg-slate-950/78 px-5 py-4 backdrop-blur">
                  <p className="text-sm text-slate-300">
                    Completed {formatRunTime(selectedRun.completedAt)}
                  </p>

                  {selectedRun.mode === 'batch' ? (
                    <button
                      type="button"
                      onClick={handleDownloadAllJson}
                      className="rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 px-5 py-2 text-sm font-semibold text-white transition hover:scale-[1.01]"
                    >
                      Download all JSON
                    </button>
                  ) : null}
                </div>

                {selectedRun.mode === 'evaluation' ? (
                  <EvaluationReportResults />
                ) : (
                  <>
                    <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-sm font-semibold text-white">Run videos</h3>
                        <span className="rounded-md bg-white/10 px-2 py-0.5 text-[11px] font-semibold text-slate-300">
                          {resultEntries.length} file{resultEntries.length === 1 ? '' : 's'}
                        </span>
                      </div>

                      <div className="mt-3 flex gap-2.5 overflow-x-auto pb-1">
                        {resultEntries.map((result) => {
                          const isSelected = result.assetId === selectedResult?.assetId;

                          return (
                            <button
                              key={result.assetId}
                              type="button"
                              onClick={() => setSelectedAssetId(result.assetId)}
                              className={`min-w-[240px] max-w-[240px] rounded-xl border px-3.5 py-3 text-left transition ${
                                isSelected
                                  ? 'border-cyan-400/50 bg-cyan-500/10'
                                  : 'border-slate-700/60 bg-slate-900/50 hover:border-cyan-400/40 hover:bg-slate-900'
                              }`}
                            >
                              <p className="truncate text-sm font-medium text-white">{result.assetName}</p>
                              <p className="mt-0.5 text-[11px] uppercase tracking-wider text-slate-500">{result.kind}</p>
                              <p className="mt-1.5 text-xs text-slate-400">
                                {result.rawJson.frames.length} frame{result.rawJson.frames.length === 1 ? '' : 's'} ·{' '}
                                {result.rawJson.hazards.length} hazard{result.rawJson.hazards.length === 1 ? '' : 's'}
                              </p>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {selectedResult ? (
                      <div className="space-y-6">
                        <ResultsFrameGallery result={selectedResult} />
                        <ResultsPropertiesPanel result={selectedResult} />
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            ) : null}
          </section>
        )}
      </div>
    </div>
  );
}
