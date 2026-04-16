'use client';

import Image from 'next/image';
import { useMemo, useState } from 'react';
import { getFrameCanvasSize } from '@/components/evaluation/mock-results';
import type { AssetResult } from '@/components/evaluation/flow-types';

type ResultsFrameGalleryProps = {
  result: AssetResult;
};

const toPercent = (value: number, total: number) => `${(value / total) * 100}%`;

const polygonToString = (points: Array<[number, number]>, width: number, height: number) => {
  return points
    .map(([x, y]) => `${(x / width) * 100},${(y / height) * 100}`)
    .join(' ');
};

export default function ResultsFrameGallery({ result }: ResultsFrameGalleryProps) {
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const dimensions = useMemo(() => getFrameCanvasSize(), []);
  const selectedFrame = result.rawJson.frames[selectedIndex] ?? result.rawJson.frames[0];

  return (
    <div className="rounded-[32px] border border-cyan-500/20 bg-slate-950/78 p-6 shadow-[0_24px_64px_rgba(2,8,20,0.35)] backdrop-blur">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">Annotated frames</p>
          <h2 className="mt-2 font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">
            Selected frames preview
          </h2>
        </div>
        <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-slate-300">
          {result.rawJson.frames.length} frame{result.rawJson.frames.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="mt-6 overflow-hidden rounded-[28px] bg-slate-950">
        <div className="relative aspect-video overflow-hidden">
          {result.kind === 'image' ? (
            <Image
              src={result.previewUrl}
              alt={result.assetName}
              fill
              unoptimized
              className="object-cover opacity-85"
            />
          ) : (
            <div className="absolute inset-0 bg-[linear-gradient(135deg,#0f172a_0%,#1d4ed8_45%,#7c3aed_100%)]" />
          )}

          <div className="absolute inset-0">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
              {selectedFrame.lanes.map((lane) => (
                <polygon
                  key={lane.lane_id}
                  points={polygonToString(lane.polygon, dimensions.width, dimensions.height)}
                  fill={lane.type === 'ego_lane' ? 'rgba(34,197,94,0.18)' : 'rgba(56,189,248,0.12)'}
                  stroke={lane.type === 'ego_lane' ? '#22c55e' : '#38bdf8'}
                  strokeWidth="0.3"
                />
              ))}
            </svg>

            {selectedFrame.objects.map((object, index) => (
              <div
                key={`${object.class}-${index}`}
                className="absolute rounded-xl border-2 border-fuchsia-400 bg-fuchsia-400/12"
                style={{
                  left: toPercent(object.bbox.x_min, dimensions.width),
                  top: toPercent(object.bbox.y_min, dimensions.height),
                  width: toPercent(object.bbox.width, dimensions.width),
                  height: toPercent(object.bbox.height, dimensions.height),
                }}
              >
                <span className="absolute -top-7 left-0 rounded-full bg-fuchsia-500 px-2 py-1 text-[10px] font-semibold text-white">
                  {object.class}
                </span>
              </div>
            ))}

            {selectedFrame.road_signs.map((sign, index) => (
              <div
                key={`${sign.label}-${index}`}
                className="absolute rounded-lg border-2 border-amber-300"
                style={{
                  left: toPercent(sign.bbox.x_min, dimensions.width),
                  top: toPercent(sign.bbox.y_min, dimensions.height),
                  width: toPercent(sign.bbox.width, dimensions.width),
                  height: toPercent(sign.bbox.height, dimensions.height),
                }}
              />
            ))}

            {selectedFrame.traffic_signals.map((signal, index) => (
              <div
                key={`${signal.label}-${index}`}
                className="absolute rounded-lg border-2 border-emerald-300"
                style={{
                  left: toPercent(signal.bbox.x_min, dimensions.width),
                  top: toPercent(signal.bbox.y_min, dimensions.height),
                  width: toPercent(signal.bbox.width, dimensions.width),
                  height: toPercent(signal.bbox.height, dimensions.height),
                }}
              />
            ))}
          </div>

          <div className="absolute bottom-4 left-4 rounded-2xl bg-slate-950/70 px-4 py-3 text-white backdrop-blur">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-200">
              Frame {selectedFrame.frame_number}
            </p>
            <p className="mt-1 text-sm font-semibold">{selectedFrame.timestamp_sec}s</p>
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {result.rawJson.frames.map((frame, index) => {
          const isSelected = index === selectedIndex;

          return (
            <button
              key={`${frame.frame_number}-${frame.timestamp_sec}`}
              type="button"
              onClick={() => setSelectedIndex(index)}
              className={`rounded-[24px] border p-4 text-left transition ${
                isSelected
                  ? 'border-cyan-400 bg-cyan-500/10'
                  : 'border-slate-700 bg-slate-900/70 hover:border-cyan-400/70 hover:bg-slate-900'
              }`}
            >
              <p className="text-sm font-semibold text-white">Frame {frame.frame_number}</p>
              <p className="mt-1 text-xs text-slate-400">{frame.timestamp_sec}s</p>
              <p className="mt-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                {frame.objects.length} objects · {frame.road_signs.length} signs
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
