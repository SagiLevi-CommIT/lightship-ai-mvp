'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import BatchNotification from '@/components/evaluation/batch-notification';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';
import { createResultsByAssetId, createRunId } from '@/components/evaluation/mock-results';
import RunProgress from '@/components/evaluation/run-progress';

const wait = (durationMs: number) =>
  new Promise<void>((resolve) => {
    window.setTimeout(resolve, durationMs);
  });

export default function RunPage() {
  const router = useRouter();
  const [showBatchInfo, setShowBatchInfo] = useState<boolean>(true);
  const {
    completeRun,
    requestNotificationPermission,
    setAssetStatus,
    setRunProgress,
    state,
  } = useEvaluationFlow();
  const runConfigRef = useRef<{
    mode: typeof state.mode;
    assets: typeof state.assets;
    pipelineConfig: typeof state.pipelineConfig;
  } | null>(null);

  if (runConfigRef.current === null) {
    runConfigRef.current = {
      mode: state.mode,
      assets: state.assets,
      pipelineConfig: state.pipelineConfig,
    };
  }

  useEffect(() => {
    const runConfig = runConfigRef.current;

    if (!runConfig) {
      router.replace('/');
      return;
    }

    if (runConfig.mode !== 'evaluation' && runConfig.assets.length === 0) {
      router.replace('/');
      return;
    }

    let isCancelled = false;

    const run = async () => {
      const stages = ['Preparing upload package', 'Selecting frames', 'Running annotations', 'Building JSON output'];
      const totalAssets = runConfig.mode === 'evaluation' ? 1 : runConfig.assets.length;
      const totalStageCount = totalAssets * stages.length;
      const startedAt = Date.now();
      let completedStages = 0;

      setRunProgress({
        phase: 'queued',
        percent: 0,
        currentStage: 'Queueing files for the pipeline',
        activeAssetId: null,
        totalAssets,
        completedAssets: 0,
        startedAt,
        completedAt: null,
      });

      if (runConfig.mode === 'evaluation') {
        for (const stage of stages) {
          if (isCancelled) {
            return;
          }

          completedStages += 1;
          setRunProgress({
            phase: 'running',
            percent: (completedStages / totalStageCount) * 100,
            currentStage: `${stage} · evaluation benchmark`,
            activeAssetId: null,
            totalAssets,
            completedAssets: 0,
            startedAt,
            completedAt: null,
          });
          await wait(900);
        }
      } else {
        runConfig.assets.forEach((asset) => {
          setAssetStatus(asset.id, 'queued');
        });

        for (const asset of runConfig.assets) {
          if (isCancelled) {
            return;
          }

          setAssetStatus(asset.id, 'running');

          for (const stage of stages) {
            if (isCancelled) {
              return;
            }

            completedStages += 1;
            setRunProgress({
              phase: 'running',
              percent: (completedStages / totalStageCount) * 100,
              currentStage: `${stage} · ${asset.name}`,
              activeAssetId: asset.id,
              totalAssets,
              completedAssets: Math.max(0, completedStages / stages.length - 1),
              startedAt,
              completedAt: null,
            });
            await wait(700);
          }

          setAssetStatus(asset.id, 'completed');
        }
      }

      const runId = createRunId();
      const resultsByAssetId =
        runConfig.mode === 'evaluation' ? {} : createResultsByAssetId(runConfig.assets, runConfig.pipelineConfig);
      const completionMessage =
        runConfig.mode === 'evaluation'
          ? 'Evaluation is complete. The final metrics table is ready to review.'
          : runConfig.mode === 'batch'
          ? 'Batch processing is complete. The results are ready to review.'
          : 'Pipeline run is complete. Your results are ready to review.';

      if (isCancelled) {
        return;
      }

      setRunProgress({
        phase: 'completed',
        percent: 100,
        currentStage: 'Results are ready',
        activeAssetId: null,
        totalAssets,
        completedAssets: totalAssets,
        startedAt,
        completedAt: Date.now(),
      });
      completeRun(runId, resultsByAssetId, completionMessage, {
        mode: runConfig.mode,
        createdAt: startedAt,
        completedAt: Date.now(),
      });
      router.push(`/results/${runId}`);
    };

    void run();

    return () => {
      isCancelled = true;
    };
  }, [completeRun, router, setAssetStatus, setRunProgress]);

  if (state.mode !== 'evaluation' && state.assets.length === 0) {
    return null;
  }

  return (
    <div className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,#163b84_0%,#08142e_34%,#020814_100%)] px-6 py-8 text-white lg:px-10">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -left-16 top-12 h-56 w-56 rounded-full bg-cyan-400/18 blur-3xl" />
        <div className="absolute right-0 top-0 h-80 w-80 rounded-full bg-blue-500/20 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-7xl">
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

        <div className="mt-8 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-[family:var(--font-ibm-plex-sans)] text-[1.75rem] font-semibold tracking-tight text-white md:text-[2.25rem]">
              Running Pipeline
            </h1>
            <p className="mt-1 text-sm text-slate-400">{state.runProgress.currentStage}</p>
          </div>
          <div className="rounded-lg bg-white/10 px-3 py-1.5 text-sm font-semibold text-slate-200">
            {state.runProgress.completedAssets}/{state.runProgress.totalAssets} completed
          </div>
        </div>

        <div className="mt-6 max-w-5xl">
          {state.mode === 'batch' && showBatchInfo ? (
            <BatchNotification
              message={
                state.notificationPermission === 'granted'
                  ? 'Browser notifications are enabled. You will also get an in-app confirmation once the batch results are ready.'
                  : 'Batch mode can show an in-app completion message and an optional browser notification while this page stays open.'
              }
              permission={state.notificationPermission}
              onRequestPermission={requestNotificationPermission}
              onDismiss={() => setShowBatchInfo(false)}
            />
          ) : null}

          <div className="mt-4">
            <RunProgress progress={state.runProgress} assets={state.mode === 'evaluation' ? [] : state.assets} />
          </div>
        </div>
      </div>
    </div>
  );
}
