'use client';

import { useMemo, useState } from 'react';
import WizardStepper from '@/components/evaluation/wizard-stepper';
import type { PipelineConfig, ProcessingMode } from '@/components/evaluation/flow-types';

type PipelineConfigFormProps = {
  config: PipelineConfig;
  mode: ProcessingMode;
  onBack: () => void;
  onChange: (patch: Partial<PipelineConfig>) => void;
  onSubmit: () => void;
};

const WIZARD_STEPS = ['Frame selection', 'S3 destination'];

export default function PipelineConfigForm({
  config,
  mode,
  onBack,
  onChange,
  onSubmit,
}: PipelineConfigFormProps) {
  const [currentStep, setCurrentStep] = useState<number>(0);

  const canContinue = useMemo(() => {
    if (currentStep === 0) {
      if (config.frameSelectionMethod !== 'native') {
        return config.maxSnapshots.trim().length > 0;
      }
      return config.nativeSamplingMode === 'fps'
        ? config.nativeFps.trim().length > 0
        : config.maxSnapshots.trim().length > 0;
    }

    if (currentStep === 1) {
      return config.s3BucketPath.trim().length > 0;
    }

    return true;
  }, [
    config.frameSelectionMethod,
    config.maxSnapshots,
    config.nativeFps,
    config.nativeSamplingMode,
    config.s3BucketPath,
    currentStep,
  ]);

  return (
    <div className="rounded-[32px] border border-white/80 bg-white/80 p-6 shadow-[0_24px_64px_rgba(15,23,42,0.08)] backdrop-blur md:p-8">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-500">Pipeline wizard</p>
          <h1 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-3xl font-semibold text-slate-950">
            Configure the detection pipeline
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            {mode === 'batch'
              ? 'Apply one shared configuration to the full batch.'
              : 'Configure the evaluation benchmark run.'}
          </p>
        </div>
        <WizardStepper steps={WIZARD_STEPS} currentStep={currentStep} />
      </div>

      <div className="mt-8">
        {currentStep === 0 ? (
          <div className="space-y-4">
            <p className="text-sm text-slate-500">Choose how the pipeline should select frames before inference.</p>
            <div className="grid gap-4 md:grid-cols-2">
              {[
                { id: 'native', label: 'Native', description: 'Extract frames directly from the source media.' },
                { id: 'scene-change', label: 'Scene change', description: 'Select frames around scene changes.' },
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
                    className={`rounded-[24px] border p-5 text-left transition ${
                      isSelected
                        ? 'border-fuchsia-300 bg-fuchsia-50 shadow-sm'
                        : 'border-slate-200 bg-white hover:border-cyan-300 hover:bg-cyan-50/70'
                    }`}
                  >
                    <p className="font-[family:var(--font-ibm-plex-sans)] text-xl font-semibold text-slate-950">
                      {method.label}
                    </p>
                    <p className="mt-3 text-sm leading-6 text-slate-500">{method.description}</p>
                  </button>
                );
              })}
            </div>

            {config.frameSelectionMethod === 'native' ? (
              <div className="space-y-4 rounded-[24px] bg-slate-50 p-5">
                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    { id: 'count', label: 'Number of frames' },
                    { id: 'fps', label: 'Frames per second' },
                  ].map((option) => {
                    const isSelected = config.nativeSamplingMode === option.id;

                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() =>
                          onChange({
                            nativeSamplingMode: option.id as PipelineConfig['nativeSamplingMode'],
                          })
                        }
                        className={`rounded-2xl border px-4 py-3 text-left text-sm font-semibold transition ${
                          isSelected
                            ? 'border-fuchsia-300 bg-fuchsia-50 text-slate-950'
                            : 'border-slate-200 bg-white text-slate-500 hover:border-cyan-300'
                        }`}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>

                {config.nativeSamplingMode === 'fps' ? (
                  <>
                    <label className="block text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                      Frames per second
                    </label>
                    <input
                      data-test-id="native-fps-input"
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={config.nativeFps}
                      onChange={(event) => onChange({ nativeFps: event.target.value })}
                      className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none focus:border-fuchsia-400"
                      placeholder="Enter FPS"
                    />
                  </>
                ) : (
                  <>
                    <label className="block text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                      Number of frames
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="30"
                      step="1"
                      value={config.maxSnapshots}
                      onChange={(event) => onChange({ maxSnapshots: event.target.value })}
                      className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none focus:border-fuchsia-400"
                      placeholder="Enter frame count"
                    />
                  </>
                )}
              </div>
            ) : null}

            {config.frameSelectionMethod === 'scene-change' ? (
              <div className="rounded-[24px] bg-slate-50 p-5">
                <label className="block text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                  Scene-change frames
                </label>
                <input
                  type="number"
                  min="1"
                  max="30"
                  step="1"
                  value={config.maxSnapshots}
                  onChange={(event) => onChange({ maxSnapshots: event.target.value })}
                  className="mt-3 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none focus:border-fuchsia-400"
                  placeholder="Enter frame count"
                />
              </div>
            ) : null}
          </div>
        ) : null}

        {currentStep === 1 ? (
          <div className="space-y-4">
            <p className="text-sm text-slate-500">Enter the S3 location where the pipeline should store results.</p>
            <div className="rounded-[24px] bg-slate-50 p-5">
              <label className="block text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                S3 bucket path
              </label>
              <input
                data-test-id="s3-path-input"
                type="text"
                value={config.s3BucketPath}
                onChange={(event) => onChange({ s3BucketPath: event.target.value })}
                className="mt-3 w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none focus:border-fuchsia-400"
                placeholder="s3://bucket/path/to/results"
              />
              <p className="mt-3 text-sm text-slate-500">
                This remains front-end only for now, but the entered path will be reflected in the generated result JSON.
              </p>
            </div>
          </div>
        ) : null}

      </div>

      <div className="mt-8 flex flex-col gap-3 border-t border-slate-200 pt-6 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={() => {
            if (currentStep === 0) {
              onBack();
              return;
            }

            setCurrentStep((step) => step - 1);
          }}
          className="rounded-full bg-slate-100 px-5 py-3 text-sm font-semibold text-slate-600 transition hover:bg-slate-200"
        >
          {currentStep === 0 ? 'Back to preview' : 'Previous step'}
        </button>

        {currentStep < WIZARD_STEPS.length - 1 ? (
          <button
            type="button"
            disabled={!canContinue}
            onClick={() => setCurrentStep((step) => step + 1)}
            className="rounded-full bg-gradient-to-r from-fuchsia-600 via-violet-600 to-cyan-500 px-6 py-3 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(109,40,217,0.2)] transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next step
          </button>
        ) : (
          <button
            type="button"
            data-test-id="run-detection-pipeline-button"
            onClick={onSubmit}
            className="rounded-full bg-gradient-to-r from-fuchsia-600 via-violet-600 to-cyan-500 px-6 py-3 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(109,40,217,0.2)] transition hover:scale-[1.01]"
          >
            Run Detection Pipeline
          </button>
        )}
      </div>
    </div>
  );
}
