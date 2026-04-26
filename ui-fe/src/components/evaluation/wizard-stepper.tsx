'use client';

type WizardStepperProps = {
  steps: Array<string>;
  currentStep: number;
};

export default function WizardStepper({ steps, currentStep }: WizardStepperProps) {
  return (
    <div className="flex flex-wrap gap-3">
      {steps.map((step, index) => {
        const isActive = index === currentStep;
        const isComplete = index < currentStep;

        return (
          <div
            key={step}
            className={`flex items-center gap-3 rounded-full px-4 py-2 text-sm font-semibold ${
              isActive
                ? 'bg-gradient-to-r from-fuchsia-600 to-cyan-500 text-white'
                : isComplete
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-slate-100 text-slate-500'
            }`}
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/80 text-xs text-slate-700">
              {index + 1}
            </span>
            {step}
          </div>
        );
      })}
    </div>
  );
}
