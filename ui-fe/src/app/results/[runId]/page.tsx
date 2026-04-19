'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import BatchNotification from '@/components/evaluation/batch-notification';
import EvaluationReportResults from '@/components/evaluation/evaluation-report-results';
import BackendFrameGallery from '@/components/evaluation/backend-frame-gallery';
import ResultsPropertiesPanel from '@/components/evaluation/results-properties-panel';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';
import type { FrameManifest, VideoClassInfo } from '@/lib/api';
import type { AssetResult } from '@/components/evaluation/flow-types';

export default function ResultsPage() {
  const params = useParams<{ runId: string }>();
  const router = useRouter();
  const { requestNotificationPermission, setNotificationMessage, state } = useEvaluationFlow();
  const historicalRun = state.historicalRuns.find((run) => run.runId === params.runId) ?? null;
  const isCurrentRun = state.currentRunId === params.runId;
  const activeMode = isCurrentRun ? state.mode : historicalRun?.mode ?? null;
  const resultEntries = useMemo(
    () => Object.values(isCurrentRun ? state.resultsByAssetId : historicalRun?.resultsByAssetId ?? {}),
    [historicalRun?.resultsByAssetId, isCurrentRun, state.resultsByAssetId],
  );
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(resultEntries[0]?.assetId ?? null);
  const [showBatchCompletionPopup, setShowBatchCompletionPopup] = useState<boolean>(activeMode === 'batch' && isCurrentRun);
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

  useEffect(() => {
    if (!activeMode || (activeMode !== 'evaluation' && resultEntries.length === 0)) {
      router.replace('/');
    }
  }, [activeMode, resultEntries.length, router]);

  if (activeMode !== 'evaluation' && !selectedResult) {
    return null;
  }

  return (
    <div className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,#163b84_0%,#08142e_34%,#020814_100%)] px-6 py-8 text-white lg:px-10">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-16 top-12 h-56 w-56 rounded-full bg-cyan-400/18 blur-3xl" />
        <div className="absolute right-0 top-0 h-80 w-80 rounded-full bg-blue-500/20 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-7xl">
        {activeMode === 'batch' && showBatchCompletionPopup ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 px-6">
            <div className="w-full max-w-lg rounded-2xl border border-cyan-500/20 bg-slate-950/90 p-8 shadow-xl">
              <h2 className="font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">
                Batch processing complete
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-slate-300">
                Your batch results are ready. You can now review all annotated selected frames and inspect the JSON
                properties for each processed video.
              </p>
              <div className="mt-6 flex justify-end">
                <button
                  type="button"
                  onClick={() => setShowBatchCompletionPopup(false)}
                  className="rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 px-6 py-2.5 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)] transition hover:scale-[1.01]"
                >
                  Review results
                </button>
              </div>
            </div>
          </div>
        ) : null}

        <AppShellHeader
          rightContent={
            <button
              type="button"
              onClick={() => router.back()}
              className="rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
            >
              Back
            </button>
          }
        />

        {state.notificationMessage ? (
          <div className="mt-4">
            <BatchNotification
              message={state.notificationMessage}
              permission={state.notificationPermission}
              onRequestPermission={requestNotificationPermission}
              onDismiss={() => setNotificationMessage(null)}
            />
          </div>
        ) : null}

        <div className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-[family:var(--font-ibm-plex-sans)] text-[1.75rem] font-semibold tracking-tight text-white md:text-[2.25rem]">
              {activeMode === 'evaluation' ? 'Evaluation Results' : 'Detection Results'}
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {activeMode === 'evaluation'
                ? 'Benchmark metrics and GT coverage summary.'
                : 'Review annotated frames and inspect the structured JSON output.'}
            </p>
          </div>

          {activeMode === 'batch' ? (
            <button
              type="button"
              onClick={handleDownloadAllJson}
              className="rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)] transition hover:scale-[1.01]"
            >
              Download all JSON
            </button>
          ) : null}
        </div>

        {activeMode === 'evaluation' ? (
          <div className="mt-6">
            <EvaluationReportResults />
          </div>
        ) : (
          <div className="mt-6 grid gap-6 2xl:grid-cols-[0.26fr_0.74fr]">
            <aside className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-5 backdrop-blur">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-white">
                  {activeMode === 'batch' ? 'Batch results' : 'Processed file'}
                </h2>
                <span className="rounded-md bg-white/10 px-2 py-0.5 text-[11px] font-semibold text-slate-300">
                  {resultEntries.length}
                </span>
              </div>

              <div className="mt-4 space-y-2">
                {resultEntries.map((result) => {
                  const isSelected = result.assetId === selectedResult?.assetId;

                  return (
                    <button
                      key={result.assetId}
                      type="button"
                      onClick={() => setSelectedAssetId(result.assetId)}
                      className={`w-full rounded-xl border p-3.5 text-left transition ${
                        isSelected
                          ? 'border-cyan-400/50 bg-cyan-500/10'
                          : 'border-slate-700/60 bg-slate-900/50 hover:border-cyan-400/40 hover:bg-slate-900'
                      }`}
                    >
                      <p className="truncate text-sm font-medium text-white">{result.assetName}</p>
                      <p className="mt-0.5 text-[11px] uppercase tracking-wider text-slate-500">{result.kind}</p>
                      <p className="mt-2 text-xs text-slate-400">
                        {result.rawJson.frames.length} frame{result.rawJson.frames.length === 1 ? '' : 's'} ·{' '}
                        {result.rawJson.hazards.length} hazard{result.rawJson.hazards.length === 1 ? '' : 's'}
                      </p>
                    </button>
                  );
                })}
              </div>
            </aside>

            <div className="space-y-6">
              {activeMode === 'batch' ? (
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm font-medium text-emerald-200">
                  Batch processing finished successfully. Select any processed file from the left to inspect its frames and JSON output.
                </div>
              ) : null}
              <BackendFrameGallery
                result={selectedResult as AssetResult & {
                  jobId?: string;
                  frames?: FrameManifest | null;
                  videoClass?: VideoClassInfo | null;
                }}
                onDownloadJson={handleDownloadJson}
              />
              <div className="grid gap-6">
                <ResultsPropertiesPanel result={selectedResult} />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
