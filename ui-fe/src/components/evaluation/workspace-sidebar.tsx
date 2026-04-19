'use client';

import { useState } from 'react';
import type { PipelineConfig, ProcessingMode } from '@/components/evaluation/flow-types';

type WorkspaceSidebarProps = {
  mode: ProcessingMode;
  config: PipelineConfig;
  configConfirmed: boolean;
  assetCount: number;
  canRun: boolean;
  onChange: (patch: Partial<PipelineConfig>) => void;
  onRun: () => void;
  onReset: () => void;
};

const tooltips: Record<string, string> = {
  'frame-selection':
    'Native extracts frames at a fixed FPS rate. Scene change detects cuts and transitions to select key frames automatically.',
  's3-bucket':
    'The S3 path where pipeline output (annotated frames and JSON) will be written. Must be a valid s3:// URI you have write access to.',
  'hazard-severity':
    'Filter which annotated frames are included in the output. "All frames" keeps everything; severity levels keep only frames with hazards at or above that level.',
};

const steps = [
  { label: 'Upload', description: 'Add video files' },
  { label: 'Configure', description: 'Set pipeline options' },
  { label: 'Run', description: 'Start processing' },
  { label: 'Results', description: 'Review output' },
];

function getActiveStep(mode: ProcessingMode, assetCount: number, configConfirmed: boolean): number {
  if (mode === 'evaluation') return 2;
  if (assetCount === 0) return 0;
  if (!configConfirmed) return 1;
  return 2;
}

function Tooltip({ id }: { id: string }) {
  const [visible, setVisible] = useState(false);
  const text = tooltips[id];
  if (!text) return null;

  return (
    <span className="relative ml-1 inline-flex">
      <button
        type="button"
        aria-label="More info"
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        onFocus={() => setVisible(true)}
        onBlur={() => setVisible(false)}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-white/10 text-[9px] font-bold text-slate-400 hover:bg-white/20 hover:text-slate-200"
      >
        ?
      </button>
      {visible ? (
        <span className="absolute bottom-full left-1/2 z-50 mb-2 w-56 -translate-x-1/2 rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-[11px] leading-relaxed font-normal normal-case tracking-normal text-slate-300 shadow-xl">
          {text}
          <span className="absolute -bottom-1 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 border-b border-r border-white/10 bg-slate-900" />
        </span>
      ) : null}
    </span>
  );
}

export default function WorkspaceSidebar({
  mode,
  config,
  configConfirmed,
  assetCount,
  canRun,
  onChange,
  onRun,
  onReset,
}: WorkspaceSidebarProps) {
  const runLabel = mode === 'evaluation' ? 'Run Evaluation' : 'Run Detection Pipeline';
  const activeStep = getActiveStep(mode, assetCount, configConfirmed);

  return (
    <aside className="rounded-2xl border border-cyan-500/20 bg-slate-950/82 p-5 shadow-[0_24px_64px_rgba(2,8,20,0.45)] backdrop-blur xl:sticky xl:top-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Pipeline Steps</h2>
        <span className="rounded-full bg-white/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase text-slate-400">{mode}</span>
      </div>

      <div className="mt-4 grid gap-1">
        {steps.map((step, index) => {
          const isActive = index === activeStep;
          const isCompleted = index < activeStep;

          return (
            <div
              key={step.label}
              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 transition ${
                isActive ? 'bg-cyan-500/10' : ''
              }`}
            >
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                  isCompleted
                    ? 'bg-cyan-500 text-white'
                    : isActive
                      ? 'bg-cyan-500/20 text-cyan-300 ring-1 ring-cyan-400/50'
                      : 'bg-white/5 text-slate-500'
                }`}
              >
                {isCompleted ? '✓' : index + 1}
              </span>
              <div className="min-w-0">
                <p className={`text-sm font-medium ${isActive ? 'text-white' : isCompleted ? 'text-slate-200' : 'text-slate-500'}`}>
                  {step.label}
                </p>
                {isActive ? (
                  <p className="text-[11px] text-slate-400">
                    {step.label === 'Upload' && mode !== 'evaluation'
                      ? `${assetCount} file${assetCount === 1 ? '' : 's'} ready`
                      : step.description}
                  </p>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>

      {mode !== 'evaluation' ? (
        <div className="mt-5 space-y-4 border-t border-white/10 pt-5">
          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Frame selection
              <Tooltip id="frame-selection" />
            </label>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {[
                { id: 'native', label: 'Native' },
                { id: 'scene-change', label: 'Scene change' },
              ].map((method) => {
                const isSelected = config.frameSelectionMethod === method.id;

                return (
                  <button
                    key={method.id}
                    type="button"
                    onClick={() =>
                      onChange({
                        frameSelectionMethod: method.id as PipelineConfig['frameSelectionMethod'],
                      })
                    }
                    className={`rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                      isSelected
                        ? 'border-cyan-400 bg-cyan-500/10 text-cyan-200'
                        : 'border-slate-700 bg-slate-950/70 text-slate-400 hover:border-cyan-400/70 hover:text-slate-200'
                    }`}
                  >
                    {method.label}
                  </button>
                );
              })}
            </div>

            {config.frameSelectionMethod === 'native' ? (
              <div className="mt-2 flex items-center rounded-lg border border-slate-700 bg-slate-950/75 pr-3 focus-within:border-cyan-400">
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={config.nativeFps}
                  onChange={(event) => onChange({ nativeFps: event.target.value })}
                  className="w-full bg-transparent px-3 py-2 text-sm text-white outline-none"
                  placeholder="2"
                />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">FPS</span>
              </div>
            ) : null}
          </div>

          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              S3 bucket path
              <Tooltip id="s3-bucket" />
            </label>
            <input
              type="text"
              value={config.s3BucketPath}
              onChange={(event) => onChange({ s3BucketPath: event.target.value })}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950/75 px-3 py-2 text-sm text-white outline-none focus:border-cyan-400"
              placeholder="s3://bucket/path/to/results"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
              Hazard severity filter
              <Tooltip id="hazard-severity" />
            </label>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {[
                { id: 'high', label: 'High' },
                { id: 'medium', label: 'Medium' },
                { id: 'low', label: 'Low' },
                { id: 'all-frames', label: 'All frames' },
              ].map((option) => {
                const isSelected = config.outputCategory === option.id;

                return (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => onChange({ outputCategory: option.id as PipelineConfig['outputCategory'] })}
                    className={`rounded-lg border px-3 py-2 text-xs font-semibold transition ${
                      isSelected
                        ? 'border-cyan-400 bg-cyan-500/10 text-cyan-200'
                        : 'border-slate-700 bg-slate-950/70 text-slate-400 hover:border-cyan-400/70 hover:text-slate-200'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-5 border-t border-white/10 pt-5">
          <p className="text-xs leading-relaxed text-slate-400">
            Uses the built-in benchmark dataset. Click <span className="text-cyan-300">Run Evaluation</span> to start.
          </p>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-2 border-t border-white/10 pt-5">
        <button
          type="button"
          data-test-id="workspace-run-button"
          disabled={!canRun}
          onClick={onRun}
          className="rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 px-6 py-2.5 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {runLabel}
        </button>
        <button
          type="button"
          onClick={onReset}
          className="rounded-full border border-white/10 bg-white/5 px-6 py-2.5 text-sm font-semibold text-slate-400 transition hover:bg-white/10 hover:text-slate-200"
        >
          Reset
        </button>
      </div>
    </aside>
  );
}
