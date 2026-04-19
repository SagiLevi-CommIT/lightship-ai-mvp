'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import BatchNotification from '@/components/evaluation/batch-notification';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';
import { createRunId } from '@/components/evaluation/mock-results';
import RunProgress from '@/components/evaluation/run-progress';
import {
  getClientConfigs,
  getFrames,
  getOutputJson,
  getVideoClass,
  pollJobToTerminal,
  presignUploadAndStart,
  startS3VideoJob,
  type JobStatusResponse,
} from '@/lib/api';
import { buildAssetResultFromBackend } from '@/lib/map-backend-to-template';
import type { AssetResult } from '@/components/evaluation/flow-types';

const STAGE_LABELS: Record<string, string> = {
  init: 'Initializing pipeline',
  processing: 'Running detection',
  finalize: 'Finalizing results',
  completed: 'Completed',
  error: 'Failed',
};

const humanStage = (status: JobStatusResponse, fallback: string) => {
  if (status.message) return status.message;
  if (status.current_step) return STAGE_LABELS[status.current_step] ?? status.current_step;
  return fallback;
};

export default function RunPage() {
  const router = useRouter();
  const [showBatchInfo, setShowBatchInfo] = useState<boolean>(true);
  const {
    completeRun,
    requestNotificationPermission,
    setAssetStatus,
    setAssetJobId,
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
    if (runConfig.assets.length === 0) {
      router.replace('/');
      return;
    }

    let isCancelled = false;

    const run = async () => {
      const totalAssets = runConfig.assets.length;
      const startedAt = Date.now();

      // Decode user config. The "Number of frames to keep" is the single
      // source of truth for max_snapshots; snapshot_strategy is derived
      // from the Native / Scene change toggle.
      const parsedMax = Number.parseInt(runConfig.pipelineConfig.maxSnapshots, 10);
      const maxSnapshots = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : 5;
      const strategy =
        runConfig.pipelineConfig.frameSelectionMethod === 'scene-change'
          ? 'scene_change'
          : 'naive';

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

      runConfig.assets.forEach((asset) => setAssetStatus(asset.id, 'queued'));

      const resultsByAssetId: Record<string, AssetResult> = {};
      let completed = 0;
      let failed = 0;

      for (const asset of runConfig.assets) {
        if (isCancelled) return;

        setAssetStatus(asset.id, 'running');
        setRunProgress({
          phase: 'running',
          percent: (completed / totalAssets) * 100,
          currentStage:
            asset.source === 's3'
              ? `Copying S3 video: ${asset.name}`
              : `Uploading ${asset.name}`,
          activeAssetId: asset.id,
          totalAssets,
          completedAssets: completed,
          startedAt,
          completedAt: null,
        });

        try {
          let jobId: string;
          if (asset.source === 's3' && asset.s3Uri) {
            const resp = await startS3VideoJob(asset.s3Uri, {
              max_snapshots: maxSnapshots,
              snapshot_strategy: strategy,
            });
            jobId = resp.job_id;
          } else if (asset.file) {
            jobId = await presignUploadAndStart(asset.file, {
              max_snapshots: maxSnapshots,
              snapshot_strategy: strategy,
            });
          } else {
            throw new Error(`Asset ${asset.name} has no file or s3Uri`);
          }
          setAssetJobId(asset.id, jobId);

          await pollJobToTerminal(
            jobId,
            (status) => {
              if (isCancelled) return;
              const progress = Math.max(0, Math.min(1, status.progress ?? 0));
              const pct = ((completed + progress) / totalAssets) * 100;
              setRunProgress({
                phase: 'running',
                percent: pct,
                currentStage: `${humanStage(status, 'Processing')} · ${asset.name}`,
                activeAssetId: asset.id,
                totalAssets,
                completedAssets: completed,
                startedAt,
                completedAt: null,
              });
            },
            3000,
          );

          const [bv, configs, frames, videoClass] = await Promise.all([
            getOutputJson(jobId),
            getClientConfigs(jobId).catch(() => null),
            getFrames(jobId).catch(() => null),
            getVideoClass(jobId).catch(() => null),
          ]);

          const result = buildAssetResultFromBackend(asset, bv, configs);
          // Attach the real frame manifest + video class as extras on the
          // result so the results page can render per-frame S3 images.
          (result as AssetResult & {
            jobId?: string;
            frames?: typeof frames;
            videoClass?: typeof videoClass;
          }).jobId = jobId;
          (result as AssetResult & {
            frames?: typeof frames;
          }).frames = frames;
          (result as AssetResult & {
            videoClass?: typeof videoClass;
          }).videoClass = videoClass;
          resultsByAssetId[asset.id] = result;

          setAssetStatus(asset.id, 'completed');
          completed += 1;
        } catch (err) {
          console.error('Pipeline failed for asset', asset.name, err);
          setAssetStatus(asset.id, 'failed');
          failed += 1;
        }
      }

      if (isCancelled) return;

      const runId = createRunId();
      setRunProgress({
        phase: failed > 0 && completed === 0 ? 'failed' : 'completed',
        percent: 100,
        currentStage:
          failed === 0
            ? 'Results are ready'
            : completed === 0
            ? 'All assets failed'
            : `Completed with ${failed} failed asset(s)`,
        activeAssetId: null,
        totalAssets,
        completedAssets: completed,
        startedAt,
        completedAt: Date.now(),
      });

      const completionMessage =
        failed === 0
          ? 'Pipeline run is complete. Your results are ready to review.'
          : completed === 0
          ? 'Pipeline run finished with failures. Check backend logs.'
          : `Pipeline finished with ${failed} failed asset(s). Partial results available.`;

      if (Object.keys(resultsByAssetId).length === 0) {
        return;
      }

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
  }, [completeRun, router, setAssetJobId, setAssetStatus, setRunProgress]);

  if (state.assets.length === 0) {
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
            <RunProgress progress={state.runProgress} assets={state.assets} />
          </div>
        </div>
      </div>
    </div>
  );
}
