"use client";

import { useRouter } from 'next/navigation';
import BatchNotification from '@/components/evaluation/batch-notification';
import AppShellHeader from '@/components/evaluation/app-shell-header';
import MediaPreview from '@/components/evaluation/media-preview';
import ModeToggle from '@/components/evaluation/mode-toggle';
import UploadDropzone from '@/components/evaluation/upload-dropzone';
import UploadQueue from '@/components/evaluation/upload-queue';
import WorkspaceSidebar from '@/components/evaluation/workspace-sidebar';
import S3UriInput from '@/components/evaluation/s3-uri-input';
import { useEvaluationFlow } from '@/components/evaluation/flow-provider';

export default function EvalReport() {
  const router = useRouter();
  const {
    state,
    addFiles,
    addS3Uri,
    removeAsset,
    requestNotificationPermission,
    resetFlow,
    selectAsset,
    setMode,
    setNotificationMessage,
    updatePipelineConfig,
  } = useEvaluationFlow();
  const selectedAsset = state.assets.find((asset) => asset.id === state.selectedAssetId) ?? null;
  const maxSnapshotsNum = Number.parseInt(state.pipelineConfig.maxSnapshots, 10);
  const canRun =
    state.mode === 'evaluation'
      ? true
      : state.assets.length > 0 &&
        maxSnapshotsNum > 0 &&
        (state.pipelineConfig.frameSelectionMethod !== 'native' ||
          state.pipelineConfig.nativeFps.trim().length > 0);

  return (
    <div className="min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top,#163b84_0%,#08142e_34%,#020814_100%)] text-white">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-x-0 top-0 h-[360px] bg-[linear-gradient(180deg,rgba(8,19,48,0.12)_0%,rgba(8,19,48,0)_100%)]" />
        <div className="absolute -left-16 top-12 h-56 w-56 rounded-full bg-cyan-400/18 blur-3xl" />
        <div className="absolute right-0 top-0 h-80 w-80 rounded-full bg-blue-500/20 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-72 w-72 rounded-full bg-indigo-500/16 blur-3xl" />
      </div>

      <main className="relative mx-auto flex min-h-screen max-w-7xl flex-col px-6 py-8 lg:px-10">
        <AppShellHeader />

        <div className="mt-8 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="font-[family:var(--font-ibm-plex-sans)] text-[1.75rem] font-semibold tracking-tight text-white md:text-[2.25rem]">
              Video Processing Pipeline
            </h1>
            <p className="mt-1 text-sm text-slate-400">
              {state.mode === 'evaluation'
                ? 'Run the benchmark evaluation against ground truth data.'
                : 'Upload videos, configure detection settings, and run the pipeline.'}
            </p>
          </div>
          <ModeToggle value={state.mode} onChange={setMode} />
        </div>

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

        <section className="mt-6 grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)] xl:items-start">
          <WorkspaceSidebar
            mode={state.mode}
            config={state.pipelineConfig}
            configConfirmed={state.configConfirmed}
            assetCount={state.assets.length}
            canRun={canRun}
            onChange={updatePipelineConfig}
            onRun={() => {
              if (state.mode !== 'evaluation' && state.assets.length === 0) {
                setNotificationMessage('Upload at least one file before starting the pipeline.');
                return;
              }

              if (
                state.mode !== 'evaluation' &&
                state.pipelineConfig.frameSelectionMethod === 'native' &&
                state.pipelineConfig.nativeFps.trim().length === 0
              ) {
                setNotificationMessage('Please provide the FPS value for native frame selection.');
                return;
              }

              if (state.mode !== 'evaluation' && !(maxSnapshotsNum > 0)) {
                setNotificationMessage('Please set "Number of frames to keep" to a positive integer.');
                return;
              }

              setNotificationMessage(null);
              router.push('/run');
            }}
            onReset={resetFlow}
          />

          <div className="space-y-6 xl:mx-auto xl:w-full xl:max-w-4xl">
            {state.mode !== 'evaluation' ? (
              <>
                <div>
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-[11px] font-bold text-cyan-300 ring-1 ring-cyan-400/30">1</span>
                    <h2 className="text-sm font-semibold text-white">Upload</h2>
                  </div>
                  <div className="mt-3">
                    <UploadDropzone
                      multiple
                      accept="video/*"
                      title="Upload videos for processing"
                      description="Upload one or multiple videos. Once at least one video is loaded, the preview section appears below and you can switch between videos from the queue bar."
                      onFilesSelected={addFiles}
                    />
                  </div>
                  <S3UriInput onAdd={addS3Uri} />
                </div>

                {state.assets.length > 0 ? (
                  <>
                    <UploadQueue
                      assets={state.assets}
                      selectedAssetId={state.selectedAssetId}
                      onSelect={selectAsset}
                      onRemove={removeAsset}
                    />
                    <MediaPreview asset={selectedAsset} />
                  </>
                ) : null}

                <div>
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-500/20 text-[11px] font-bold text-cyan-300 ring-1 ring-cyan-400/30">2</span>
                    <h2 className="text-sm font-semibold text-white">Configure</h2>
                  </div>
                  <p className="mt-1.5 pl-[34px] text-xs text-slate-400">
                    Set the pipeline options in the sidebar, then press Run.
                  </p>
                </div>
              </>
            ) : (
              <div className="rounded-2xl border border-cyan-500/20 bg-slate-950/78 p-6 shadow-[0_20px_50px_rgba(2,8,20,0.35)]">
                <h2 className="font-[family:var(--font-ibm-plex-sans)] text-lg font-semibold text-white">
                  GT Coverage Benchmark
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-300">
                  Run the built-in evaluation benchmark to review the GT coverage summary and the metric report table after
                  processing is complete.
                </p>
                <div className="mt-5 grid gap-3 sm:grid-cols-2">
                  {[
                    { label: 'Ground truth version', value: 'v1.2.0' },
                    { label: 'Benchmark videos', value: '147' },
                  ].map((item) => (
                    <div key={item.label} className="rounded-xl border border-white/5 bg-slate-900/80 px-4 py-3">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{item.label}</p>
                      <p className="mt-1.5 text-sm font-semibold text-white">{item.value}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
