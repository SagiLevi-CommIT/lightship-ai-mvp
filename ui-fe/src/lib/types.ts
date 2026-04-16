import type { ObjectLabel, HazardEvent, PipelineResultJson } from "./api-client";

export type RunStage =
  | "uploading"
  | "queued"
  | "processing"
  | "finalizing"
  | "completed"
  | "failed";

export interface Asset {
  id: string;
  file: File;
  name: string;
}

export interface AssetResult {
  assetId: string;
  filename: string;
  pipeline: PipelineResultJson;
  jobId: string;
}

export interface RunState {
  assets: Asset[];
  config: { max_snapshots: number; snapshot_strategy: string };
  stage: RunStage;
  progress: number;
  message: string;
  jobIds: Record<string, string>;
  results: Record<string, AssetResult>;
}

export interface HistoricalRun {
  jobId: string;
  filename: string;
  status: string;
  createdAt: string;
  completedAt?: string;
}

export function mapBackendStage(currentStep: string | undefined): RunStage {
  switch (currentStep) {
    case "init":
      return "queued";
    case "processing":
      return "processing";
    case "finalize":
      return "finalizing";
    case "completed":
      return "completed";
    case "error":
      return "failed";
    default:
      return "processing";
  }
}

export function stageName(stage: RunStage): string {
  const labels: Record<RunStage, string> = {
    uploading: "Uploading video to S3…",
    queued: "Queued for processing…",
    processing: "Analyzing video (CV + LLM)…",
    finalizing: "Finalizing results…",
    completed: "Complete",
    failed: "Failed",
  };
  return labels[stage];
}
