'use client';

type CoverageRow = {
  label: string;
  type: 'frame' | 'video';
  videos: number;
  frames: number;
  score: number;
  threshold: number;
};

const COVERAGE_ROWS: Array<CoverageRow> = [
  { label: 'Lane Detection IOU', type: 'frame', videos: 89, frames: 312, score: 0.71, threshold: 0.65 },
  { label: 'Motorcycle Recall', type: 'frame', videos: 31, frames: 58, score: 0.74, threshold: 0.75 },
  { label: 'Road Sign Precision', type: 'frame', videos: 67, frames: 134, score: 0.82, threshold: 0.75 },
  { label: 'Job Site Obj. Precision', type: 'video', videos: 33, frames: 41, score: 0.79, threshold: 0.75 },
  { label: 'Video Class Accuracy', type: 'video', videos: 147, frames: 147, score: 0.88, threshold: 0.8 },
  { label: 'Road Type Accuracy', type: 'video', videos: 114, frames: 114, score: 0.91, threshold: 0.85 },
];

const passMetric = (score: number, threshold: number) => score >= threshold;

const formatPercent = (value: number) => `${Math.round(value * 100)}%`;

export default function EvaluationReportResults() {
  const passingCount = COVERAGE_ROWS.filter((row) => passMetric(row.score, row.threshold)).length;
  const aggregateScore = Math.round((COVERAGE_ROWS.reduce((total, row) => total + row.score, 0) / COVERAGE_ROWS.length) * 100);

  return (
    <div className="space-y-6">
      <section className="rounded-[32px] border border-cyan-500/20 bg-slate-950/78 p-8 shadow-[0_24px_64px_rgba(2,8,20,0.35)] backdrop-blur">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Evaluation report</p>
            <h1 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-4xl font-semibold text-white">
              GT Coverage Summary
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-300">
              Review the final metrics table from the evaluation benchmark and compare the threshold coverage across
              the selected categories.
            </p>
          </div>

          <div className="flex flex-wrap gap-3">
            <div className="rounded-2xl bg-emerald-500/12 px-4 py-3 text-sm font-semibold text-emerald-300">
              {passingCount}/{COVERAGE_ROWS.length} passing
            </div>
            <div className="rounded-2xl bg-cyan-500/12 px-4 py-3 text-sm font-semibold text-cyan-300">
              {aggregateScore}% aggregate score
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-[32px] border border-cyan-500/20 bg-slate-950/78 p-6 shadow-[0_24px_64px_rgba(2,8,20,0.35)] backdrop-blur">
        <div className="flex flex-col gap-4 border-b border-white/10 pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Final metrics table</p>
            <h2 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-3xl font-semibold text-white">
              Coverage report
            </h2>
          </div>
          <div className="rounded-2xl bg-amber-500/12 px-4 py-3 text-sm font-semibold text-amber-300">Completed in 4m 22s</div>
        </div>

        <div className="mt-6 overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-y-3 text-left text-sm">
            <thead>
              <tr>
                {['Metric', 'Granularity', 'Videos in Pool', 'Frames in Pool', 'Threshold', 'Score', 'Delta', 'Status'].map((heading) => (
                  <th key={heading} className="px-4 pb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {COVERAGE_ROWS.map((row) => {
                const passed = passMetric(row.score, row.threshold);
                const delta = Math.round((row.score - row.threshold) * 100);

                return (
                  <tr key={row.label} className="rounded-2xl bg-slate-900/85 shadow-[0_10px_30px_rgba(2,8,20,0.25)]">
                    <td className="rounded-l-2xl px-4 py-4 font-semibold text-white">{row.label}</td>
                    <td className="px-4 py-4">
                      <span className="inline-flex rounded-full bg-white/10 px-3 py-1 text-xs font-medium capitalize text-slate-300">
                        {row.type}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-slate-300">{row.videos}</td>
                    <td className="px-4 py-4 text-slate-300">{row.frames}</td>
                    <td className="px-4 py-4 text-slate-400">{formatPercent(row.threshold)}</td>
                    <td className={`px-4 py-4 font-semibold ${passed ? 'text-emerald-300' : 'text-rose-300'}`}>
                      {formatPercent(row.score)}
                    </td>
                    <td className={`px-4 py-4 font-semibold ${passed ? 'text-emerald-300' : 'text-rose-300'}`}>
                      {delta >= 0 ? '+' : ''}
                      {delta}pp
                    </td>
                    <td className="rounded-r-2xl px-4 py-4">
                      <span
                        className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${
                          passed ? 'bg-emerald-500/12 text-emerald-300' : 'bg-rose-500/12 text-rose-300'
                        }`}
                      >
                        {passed ? 'Pass' : 'Fail'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
