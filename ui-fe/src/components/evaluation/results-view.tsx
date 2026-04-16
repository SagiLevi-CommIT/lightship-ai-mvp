"use client";

import { useState } from "react";
import type { PipelineResultJson, ObjectLabel, HazardEvent } from "@/lib/api-client";
import { getFrameUrl } from "@/lib/api-client";
import {
  AlertTriangle,
  Camera,
  Eye,
  Cloud,
  Car,
  Gauge,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface ResultsViewProps {
  result: PipelineResultJson;
  jobId: string;
  filename: string;
}

/* ── Badge helpers ─────────────────────────────────────────────── */

function PriorityBadge({ priority }: { priority: string }) {
  const colors: Record<string, string> = {
    critical: "bg-red-100 text-red-800",
    high: "bg-orange-100 text-orange-800",
    medium: "bg-yellow-100 text-yellow-800",
    low: "bg-green-100 text-green-800",
    none: "bg-gray-100 text-gray-600",
  };
  return (
    <span
      className={cn(
        "inline-block px-2 py-0.5 rounded text-xs font-medium",
        colors[priority] ?? colors.none
      )}
    >
      {priority}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    Critical: "bg-red-100 text-red-800",
    High: "bg-orange-100 text-orange-800",
    Medium: "bg-yellow-100 text-yellow-800",
    Low: "bg-blue-100 text-blue-700",
    None: "bg-gray-100 text-gray-600",
  };
  return (
    <span
      className={cn(
        "inline-block px-2 py-0.5 rounded text-xs font-medium",
        colors[severity] ?? colors.None
      )}
    >
      {severity}
    </span>
  );
}

/* ── Stat card ─────────────────────────────────────────────────── */

function Stat({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: React.ElementType;
}) {
  return (
    <div className="rounded-lg border bg-white p-4 flex items-start gap-3">
      <Icon className="h-5 w-5 text-brand-500 mt-0.5 shrink-0" />
      <div>
        <p className="text-xs text-gray-500">{label}</p>
        <p className="font-semibold text-gray-900 text-sm">{value}</p>
      </div>
    </div>
  );
}

/* ── Main component ────────────────────────────────────────────── */

export function ResultsView({ result, jobId, filename }: ResultsViewProps) {
  const timestamps = [...new Set(result.objects.map((o) => o.start_time_ms))].sort(
    (a, b) => a - b
  );
  const [activeTs, setActiveTs] = useState<number | null>(timestamps[0] ?? null);
  const [showHazards, setShowHazards] = useState(true);

  const objectsByTs = (ts: number) =>
    result.objects.filter((o) => o.start_time_ms === ts);

  const priorityCounts: Record<string, number> = {};
  result.objects.forEach((o) => {
    priorityCounts[o.priority] = (priorityCounts[o.priority] || 0) + 1;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Analysis Results</h2>
          <p className="text-sm text-gray-500">{filename}</p>
        </div>
        <a
          href={`/download/json/${jobId}`}
          className="text-sm text-brand-600 hover:underline"
          download
        >
          Download JSON
        </a>
      </div>

      {/* Video metadata */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat icon={Camera} label="Camera" value={result.camera} />
        <Stat icon={Eye} label="Lighting" value={result.lighting} />
        <Stat icon={Cloud} label="Weather" value={result.weather} />
        <Stat icon={Car} label="Traffic" value={result.traffic} />
        <Stat icon={Gauge} label="Speed" value={result.speed} />
        <Stat
          icon={AlertTriangle}
          label="Hazards"
          value={String(result.hazard_events.length)}
        />
      </div>

      {/* Description */}
      {result.description && (
        <div className="rounded-lg border bg-white p-4">
          <p className="text-sm text-gray-700">{result.description}</p>
        </div>
      )}

      {/* Priority distribution */}
      <div className="rounded-lg border bg-white p-4">
        <h3 className="text-sm font-medium text-gray-700 mb-3">
          Priority Distribution ({result.objects.length} objects)
        </h3>
        <div className="flex flex-wrap gap-2">
          {Object.entries(priorityCounts)
            .sort(([a], [b]) => {
              const order = ["critical", "high", "medium", "low", "none"];
              return order.indexOf(a) - order.indexOf(b);
            })
            .map(([p, c]) => (
              <span key={p} className="text-xs">
                <PriorityBadge priority={p} /> ×{c}
              </span>
            ))}
        </div>
      </div>

      {/* Frame-by-frame */}
      <div className="rounded-lg border bg-white overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50">
          <h3 className="text-sm font-medium text-gray-700">
            Frame Analysis ({timestamps.length} frames)
          </h3>
        </div>

        {/* Frame tabs */}
        <div className="flex gap-1 p-2 overflow-x-auto border-b bg-gray-50/50">
          {timestamps.map((ts, i) => (
            <button
              key={ts}
              onClick={() => setActiveTs(ts)}
              className={cn(
                "px-3 py-1.5 rounded text-xs font-medium whitespace-nowrap transition-colors",
                ts === activeTs
                  ? "bg-brand-600 text-white"
                  : "bg-white border text-gray-600 hover:bg-gray-100"
              )}
            >
              Frame {i + 1} · {(ts / 1000).toFixed(1)}s
            </button>
          ))}
        </div>

        {/* Active frame objects */}
        {activeTs !== null && (
          <div className="p-4 space-y-2">
            <p className="text-xs text-gray-400 mb-2">
              {objectsByTs(activeTs).length} objects at{" "}
              {(activeTs / 1000).toFixed(2)}s
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 border-b">
                    <th className="pb-2 pr-4">Object</th>
                    <th className="pb-2 pr-4">Distance</th>
                    <th className="pb-2 pr-4">Priority</th>
                    <th className="pb-2 pr-4">Location</th>
                    <th className="pb-2">BBox</th>
                  </tr>
                </thead>
                <tbody>
                  {objectsByTs(activeTs).map((obj, i) => (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-medium text-gray-800">
                        {obj.description}
                      </td>
                      <td className="py-2 pr-4 text-gray-600">{obj.distance}</td>
                      <td className="py-2 pr-4">
                        <PriorityBadge priority={obj.priority} />
                      </td>
                      <td className="py-2 pr-4 text-gray-500 text-xs">
                        {obj.location_description || "—"}
                      </td>
                      <td className="py-2 text-xs text-gray-400 font-mono">
                        {obj.x_min != null
                          ? `(${Math.round(obj.x_min)},${Math.round(obj.y_min!)})`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Hazard events */}
      {result.hazard_events.length > 0 && (
        <div className="rounded-lg border bg-white overflow-hidden">
          <button
            onClick={() => setShowHazards(!showHazards)}
            className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 border-b"
          >
            <h3 className="text-sm font-medium text-gray-700">
              Hazard Events ({result.hazard_events.length})
            </h3>
            <ChevronDown
              className={cn(
                "h-4 w-4 text-gray-400 transition-transform",
                showHazards && "rotate-180"
              )}
            />
          </button>
          {showHazards && (
            <div className="divide-y">
              {result.hazard_events.map((h, i) => (
                <div key={i} className="p-4 space-y-1">
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={h.hazard_severity} />
                    <span className="text-sm font-medium text-gray-800">
                      {h.hazard_type}
                    </span>
                    <span className="text-xs text-gray-400 ml-auto">
                      {(h.start_time_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                  <p className="text-sm text-gray-600">
                    {h.hazard_description}
                  </p>
                  <p className="text-xs text-gray-400">
                    Conditions: {h.road_conditions}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
