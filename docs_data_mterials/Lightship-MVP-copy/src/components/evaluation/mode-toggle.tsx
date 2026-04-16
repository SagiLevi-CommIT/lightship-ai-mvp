'use client';

import type { ProcessingMode } from '@/components/evaluation/flow-types';

type ModeToggleProps = {
  value: ProcessingMode;
  onChange: (value: ProcessingMode) => void;
};

export default function ModeToggle({ value, onChange }: ModeToggleProps) {
  return (
    <div className="inline-flex rounded-full border border-cyan-500/20 bg-slate-950/70 p-[3px] shadow-[0_0_24px_rgba(15,23,42,0.3)]">
      {[
        { id: 'batch', label: 'Batch Mode' },
        { id: 'evaluation', label: 'Evaluation' },
      ].map((mode) => {
        const isActive = value === mode.id;

        return (
          <button
            key={mode.id}
            type="button"
            data-test-id={`mode-toggle-${mode.id}`}
            onClick={() => onChange(mode.id as ProcessingMode)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${
              isActive
                ? 'bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 text-white shadow-[0_0_18px_rgba(59,130,246,0.38)]'
                : 'text-slate-300 hover:text-white'
            }`}
          >
            {mode.label}
          </button>
        );
      })}
    </div>
  );
}
