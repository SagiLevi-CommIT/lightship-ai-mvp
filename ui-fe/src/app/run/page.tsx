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
  pollBatchToTerminal,
  presign,
  retry404,
  startS3VideoJob,
  startVideoJob,
  uploadToS3,
  type BatchStatusRow,
} from '@/lib/api';
import { buildAssetResultFromBackend } from '@/lib/map-backend-to-template';
import type { AssetResult, UploadedAsset } from '@/components/evaluation/flow-types';

const STAGE_LABELS: Record<string, string> = {
  init: 'Initializing pipeline',
  queued: 'Queued',
  loading_video: 'Loading video',
  sampling_frames: 'Sampling frames',
  extracting_frames: 'Extracting frames',
  detecting_objects: 'Detecting objects',
  selecting_frames: 'Selecting key frames',
  rekognition: 'Running AWS Rekognition',
  refining_frames: 'Refining frames with LLM',
  annotating_frames: 'Annotating frames',
  assessing_hazards: 'Assessing hazards',
  writing_output: 'Writing output JSON',
  finalizing: 'Finalizing results',
  processing: 'Processing',
  completed: 'Completed',
  error: 'Failed',
};

const PARALLEL_UPLOAD_CONCURRENCY = 3;

async function runWithConcurrency<T, R>(
  items: Array<T>,
  limit: number,
  fn: (item: T) => Promise<R>,
): Promise<Array<PromiseSettledResult<R>>> {
  const results: Array<PromiseSettledResult<R>> = new Array(items.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const index = cursor++;
      if (index >= items.length) return;
      try {
        const value = await fn(items[index]);
        results[index] = { status: 'fulfilled', value };
      } catch (reason) {
        results[index] = { status: 'rejected', reason };
      }
    }
  });
  await Promise.all(workers);
  return results;
}

async function startJobForAsset(
  asset: UploadedAsset,
  options: {
    max_snapshots: number;
    snapshot_strategy: string;
    native_fps?: number;
    native_sampling_mode?: 'count' | 'fps';
    detector_backend: 'florence2' | 'yolo' | 'detectron2';
    lane_backend?: string;
  },
): Promise<string> {
  if (asset.source === 's3' && asset.s3Uri) {
    const resp = await startS3VideoJob(asset.s3Uri, options);
    return resp.job_id;
  }
  if (asset.file) {
    // Upload via presign and start; mirror the old presignUploadAndStart
    // flow but keep it inlined so status transitions stay colocated.
    const { presign_url, s3_key, required_headers } = await presign(
      asset.file.name,
      asset.file.type || asset.type || 'video/mp4',
    );
    await uploadToS3(presign_url, asset.file, required_headers);
    const { job_id } = await startVideoJob(s3_key, options);
    return job_id;
  }
  throw new Error(`Asset ${asset.name} has no file or s3Uri`);
}

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

      const parsedMax = Number.parseInt(runConfig.pipelineConfig.maxSnapshots, 10);
      const maxSnapshots = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : 5;
      const strategy =
        runConfig.pipelineConfig.frameSelectionMethod === 'scene-change'
          ? 'scene_change'
          : 'naive';
      const parsedFps = Number.parseFloat(runConfig.pipelineConfig.nativeFps);
      const nativeFps =
        runConfig.pipelineConfig.frameSelectionMethod === 'native' &&
        runConfig.pipelineConfig.nativeSamplingMode === 'fps' &&
        Number.isFinite(parsedFps) &&
        parsedFps > 0
          ? parsedFps
          : undefined;

      setRunProgress({
        phase: 'queued',
        percent: 0,
        currentStage: `Queueing ${totalAssets} file${totalAssets === 1 ? '' : 's'}`,
        activeAssetId: null,
        totalAssets,
        completedAssets: 0,
        startedAt,
        completedAt: null,
      });

      runConfig.assets.forEach((asset) => setAssetStatus(asset.id, 'queued'));

      // ─── Step 1: launch upload + submit in parallel (bounded) ─────────────
      const jobIdByAssetId = new Map<string, string>();
      const launchResults = await runWithConcurrency(
        runConfig.assets,
        PARALLEL_UPLOAD_CONCURRENCY,
        async (asset) => {
          if (isCancelled) throw new Error('cancelled');
          setAssetStatus(asset.id, 'running');
          const jobId = await startJobForAsset(asset, {
            max_snapshots: maxSnapshots,
            snapshot_strategy: strategy,
            native_fps: nativeFps,
            native_sampling_mode:
              runConfig.pipelineConfig.frameSelectionMethod === 'native'
                ? runConfig.pipelineConfig.nativeSamplingMode
                : 'count',
            detector_backend: runConfig.pipelineConfig.detectorBackend ?? 'florence2',
            lane_backend: 'ufldv2',
          });
          setAssetJobId(asset.id, jobId);
          jobIdByAssetId.set(asset.id, jobId);
          return jobId;
        },
      );

      if (isCancelled) return;

      // Mark assets whose submit itself failed (no job_id ever assigned).
      for (let i = 0; i < runConfig.assets.length; i += 1) {
        const asset = runConfig.assets[i];
        const result = launchResults[i];
        if (result && result.status === 'rejected') {
          console.error('submit failed for', asset.name, result.reason);
          setAssetStatus(asset.id, 'failed');
        }
      }

      const jobIds = Array.from(jobIdByAssetId.values());
      if (jobIds.length === 0) {
        setRunProgress({
          phase: 'failed',
          percent: 100,
          currentStage: 'All submissions failed',
          activeAssetId: null,
          totalAssets,
          completedAssets: 0,
          startedAt,
          completedAt: Date.now(),
        });
        return;
      }

      setRunProgress({
        phase: 'running',
        percent: 5,
        currentStage: `Running pipeline on ${jobIds.length} job${jobIds.length === 1 ? '' : 's'}`,
        activeAssetId: null,
        totalAssets,
        completedAssets: 0,
        startedAt,
        completedAt: null,
      });

      // ─── Step 2: single-round-trip batch polling ───────────────────────────
      await pollBatchToTerminal(jobIds, (statusMap) => {
        if (isCancelled) return;

        // Compute aggregate progress as sum of per-job progress / total.
        let aggregate = 0;
        let completed = 0;
        for (const asset of runConfig.assets) {
          const jid = jobIdByAssetId.get(asset.id);
          if (!jid) continue;
          const row = statusMap.get(jid);
          if (!row) continue;
          const prog = Math.max(0, Math.min(1, row.progress ?? 0));
          aggregate += prog;
          const status = (row.status || '').toUpperCase();
          if (status === 'COMPLETED') {
            completed += 1;
            setAssetStatus(asset.id, 'completed');
          } else if (status === 'FAILED' || status === 'NOT_FOUND') {
            setAssetStatus(asset.id, 'failed');
          }
        }
        const percent = Math.min(99, Math.round((aggregate / runConfig.assets.length) * 100));

        // Pick one "active" asset — whichever has the latest partial
        // progress — so the banner isn't blank when things are in flight.
        let activeAssetId: string | null = null;
        let activeRow: BatchStatusRow | null = null;
        let bestProgress = -1;
        for (const asset of runConfig.assets) {
          const jid = jobIdByAssetId.get(asset.id);
          if (!jid) continue;
          const row = statusMap.get(jid);
          if (!row) continue;
          const status = (row.status || '').toUpperCase();
          if (status === 'COMPLETED' || status === 'FAILED') continue;
          const prog = row.progress ?? 0;
          if (prog > bestProgress) {
            bestProgress = prog;
            activeAssetId = asset.id;
            activeRow = row;
          }
        }

        const stepLabel = STAGE_LABELS[activeRow?.current_step ?? ''] ?? null;
        const humanStage = stepLabel ?? activeRow?.message ?? 'Processing';
        const currentStage = activeRow
          ? `${humanStage}${activeAssetId ? ` · ${runConfig.assets.find((a) => a.id === activeAssetId)?.name ?? ''}` : ''}`
          : `${completed}/${runConfig.assets.length} completed`;

        setRunProgress({
          phase: 'running',
          percent,
          currentStage,
          activeAssetId,
          totalAssets,
          completedAssets: completed,
          startedAt,
          completedAt: null,
        });
      });

      if (isCancelled) return;

      // ─── Step 3: fetch results for every successful job in parallel ───────
      const resultsByAssetId: Record<string, AssetResult> = {};

      await runWithConcurrency(
        runConfig.assets.filter((a) => jobIdByAssetId.has(a.id)),
        PARALLEL_UPLOAD_CONCURRENCY,
        async (asset) => {
          if (isCancelled) return;
          const jobId = jobIdByAssetId.get(asset.id);
          if (!jobId) return;

          try {
            // The worker persists output.json + frames manifest to S3 at
            // different points; after the job flips to COMPLETED the
            // objects may not be immediately readable on a cold Lambda,
            // so retry 404s a handful of times before declaring failure.
            const [bv, configs, frames, videoClass] = await Promise.all([
              retry404(() => getOutputJson(jobId)),
              retry404(() => getClientConfigs(jobId)).catch(() => null),
              retry404(() => getFrames(jobId)).catch(() => null),
              retry404(() => getVideoClass(jobId)).catch(() => null),
            ]);

            const result = buildAssetResultFromBackend(asset, bv, configs);
            (result as AssetResult & {
              jobId?: string;
              frames?: typeof frames;
              videoClass?: typeof videoClass;
            }).jobId = jobId;
            (result as AssetResult & { frames?: typeof frames }).frames = frames;
            (result as AssetResult & {
              videoClass?: typeof videoClass;
            }).videoClass = videoClass;
            resultsByAssetId[asset.id] = result;
          } catch (err) {
            console.error('result fetch failed for', asset.name, err);
            setAssetStatus(asset.id, 'failed');
          }
        },
      );

      if (isCancelled) return;

      const completed = Object.keys(resultsByAssetId).length;
      const failed = totalAssets - completed;

      // For a single-asset run we use the backend job_id directly as the
      // runId so the /results/<id> URL is bookmarkable and survives a
      // full page reload. For multi-asset runs we keep the original
      // synthetic id and rely on persisted historicalRuns.
      const onlyAsset = runConfig.assets.length === 1 ? runConfig.assets[0] : null;
      const onlyJobId = onlyAsset ? jobIdByAssetId.get(onlyAsset.id) : undefined;
      const runId =
        onlyJobId && resultsByAssetId[onlyAsset!.id] ? onlyJobId : createRunId();
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

      if (completed === 0) {
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
