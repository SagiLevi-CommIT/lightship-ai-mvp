"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  uploadAndProcess,
  getJobStatus,
  getPipelineResult,
  getFrameUrl,
  type PipelineResultJson,
  type JobStatus,
} from "@/lib/api-client";
import { mapBackendStage, stageName, type RunStage } from "@/lib/types";
import { Loader2, CheckCircle2, XCircle, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { ResultsView } from "@/components/evaluation/results-view";

interface JobTracker {
  file: File;
  jobId?: string;
  stage: RunStage;
  progress: number;
  message: string;
  result?: PipelineResultJson;
  error?: string;
}

const POLL_MS = 3000;

export default function RunPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobTracker[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const stored = (window as any).__ls_files as FileList | undefined;
    if (!stored || stored.length === 0) {
      router.replace("/");
      return;
    }

    const maxSnaps = Number(sessionStorage.getItem("ls_max_snapshots") || "5");
    const strategy = sessionStorage.getItem("ls_strategy") || "clustering";

    const trackers: JobTracker[] = Array.from(stored).map((f) => ({
      file: f,
      stage: "uploading" as RunStage,
      progress: 0,
      message: "Starting upload…",
    }));
    setJobs(trackers);

    trackers.forEach((t, idx) => {
      processOne(idx, t.file, { max_snapshots: maxSnaps, snapshot_strategy: strategy });
    });

    delete (window as any).__ls_files;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function processOne(
    idx: number,
    file: File,
    config: { max_snapshots: number; snapshot_strategy: string }
  ) {
    const update = (patch: Partial<JobTracker>) =>
      setJobs((prev) => prev.map((j, i) => (i === idx ? { ...j, ...patch } : j)));

    try {
      update({ stage: "uploading", message: "Uploading to S3…", progress: 10 });
      const { job_id } = await uploadAndProcess(file, config);
      update({ jobId: job_id, stage: "queued", message: "Queued", progress: 20 });

      // Poll
      let done = false;
      while (!done) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        let status: JobStatus;
        try {
          status = await getJobStatus(job_id);
        } catch {
          continue;
        }
        const s = status.status.toUpperCase();
        if (s === "COMPLETED") {
          update({
            stage: "finalizing",
            progress: 90,
            message: "Loading results…",
          });
          const result = await getPipelineResult(job_id);
          update({
            stage: "completed",
            progress: 100,
            message: "Done",
            result,
          });
          done = true;
        } else if (s === "FAILED") {
          update({
            stage: "failed",
            progress: 0,
            message: status.message || "Processing failed",
            error: status.message,
          });
          done = true;
        } else {
          const stage = mapBackendStage(status.current_step);
          update({
            stage,
            progress: Math.round(status.progress * 100),
            message: status.message || stageName(stage),
          });
        }
      }
    } catch (err: any) {
      update({
        stage: "failed",
        progress: 0,
        message: err.message ?? "Unknown error",
        error: err.message,
      });
    }
  }

  const activeJob = jobs[activeIdx];

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-gray-400">
        <Loader2 className="h-6 w-6 animate-spin mr-2" /> Initializing…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Processing</h1>
        <button
          onClick={() => router.push("/")}
          className="text-sm text-brand-600 hover:underline"
        >
          + New Analysis
        </button>
      </div>

      {/* Job tabs */}
      {jobs.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {jobs.map((j, i) => (
            <button
              key={i}
              onClick={() => setActiveIdx(i)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm whitespace-nowrap border transition-colors",
                i === activeIdx
                  ? "border-brand-500 bg-brand-50 text-brand-700"
                  : "border-gray-200 text-gray-500 hover:bg-gray-50"
              )}
            >
              {j.stage === "completed" && (
                <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
              )}
              {j.stage === "failed" && (
                <XCircle className="h-3.5 w-3.5 text-red-500" />
              )}
              {j.stage !== "completed" && j.stage !== "failed" && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-500" />
              )}
              {j.file.name}
            </button>
          ))}
        </div>
      )}

      {/* Active job status */}
      {activeJob && activeJob.stage !== "completed" && (
        <div className="rounded-xl border bg-white p-8 text-center space-y-4">
          {activeJob.stage === "failed" ? (
            <XCircle className="mx-auto h-12 w-12 text-red-400" />
          ) : (
            <Loader2 className="mx-auto h-12 w-12 text-brand-500 animate-spin" />
          )}
          <p className="text-lg font-medium text-gray-700">
            {stageName(activeJob.stage)}
          </p>
          <p className="text-sm text-gray-500">{activeJob.message}</p>
          {activeJob.progress > 0 && activeJob.stage !== "failed" && (
            <div className="mx-auto w-80">
              <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-brand-500 transition-all duration-500 rounded-full"
                  style={{ width: `${activeJob.progress}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-gray-400">
                {activeJob.progress}%
              </p>
            </div>
          )}
        </div>
      )}

      {/* Results */}
      {activeJob && activeJob.stage === "completed" && activeJob.result && (
        <ResultsView
          result={activeJob.result}
          jobId={activeJob.jobId!}
          filename={activeJob.file.name}
        />
      )}
    </div>
  );
}
