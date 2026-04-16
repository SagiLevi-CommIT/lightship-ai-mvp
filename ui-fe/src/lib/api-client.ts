/**
 * API client for the Lightship backend (FastAPI behind ALB).
 *
 * When NEXT_PUBLIC_API_BASE is empty the browser makes same-origin
 * requests through the ALB which routes /health, /process-video, etc.
 * to the backend Lambda target group.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

/* ------------------------------------------------------------------ */
/*  Types matching backend JSON contracts                              */
/* ------------------------------------------------------------------ */

export interface PresignResponse {
  presign_url: string;
  s3_key: string;
  required_headers: Record<string, string>;
}

export interface ProcessVideoResponse {
  job_id: string;
  status: string;
}

export interface JobStatus {
  status: string;
  progress: number;
  message: string;
  current_step?: string;
}

export interface ObjectLabel {
  description: string;
  start_time_ms: number;
  distance: string;
  priority: string;
  location_description?: string;
  center?: { x: number; y: number };
  polygon?: { x: number; y: number }[];
  x_min?: number;
  y_min?: number;
  x_max?: number;
  y_max?: number;
  width?: number;
  height?: number;
}

export interface HazardEvent {
  start_time_ms: number;
  hazard_type: string;
  hazard_description: string;
  hazard_severity: string;
  road_conditions: string;
  duration_ms?: number;
}

export interface PipelineResultJson {
  filename: string;
  fps: number;
  camera: string;
  description: string;
  traffic: string;
  lighting: string;
  weather: string;
  collision: string;
  speed: string;
  video_duration_ms: number;
  objects: ObjectLabel[];
  hazard_events: HazardEvent[];
}

export interface SnapshotInfo {
  frame_idx: number;
  timestamp_ms: number;
  frame_path: string;
}

export interface JobResults {
  output_json: string;
  extracted_frames: Record<number, string>;
  snapshots: SnapshotInfo[];
  video_metadata: {
    filename: string;
    camera: string;
    fps: number;
    duration_ms: number;
    width: number;
    height: number;
  };
  summary: {
    filename: string;
    total_objects: number;
    num_snapshots: number;
    num_hazards: number;
    priority_distribution: Record<string, number>;
    distance_distribution: Record<string, number>;
    hazard_severity_distribution: Record<string, number>;
  };
}

export interface Job {
  job_id: string;
  status: string;
  filename?: string;
  created_at?: string;
  completed_at?: string;
  max_snapshots?: number;
  snapshot_strategy?: string;
  error_message?: string;
}

/* ------------------------------------------------------------------ */
/*  API helpers                                                        */
/* ------------------------------------------------------------------ */

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ */
/*  Public API functions                                               */
/* ------------------------------------------------------------------ */

export async function healthCheck(): Promise<{ status: string }> {
  return apiFetch("/health");
}

export async function getPresignedUrl(
  filename: string,
  contentType = "video/mp4"
): Promise<PresignResponse> {
  const params = new URLSearchParams({ filename, content_type: contentType });
  return apiFetch(`/presign-upload?${params}`);
}

export async function uploadToPresignedUrl(
  presignUrl: string,
  file: File,
  requiredHeaders: Record<string, string>
): Promise<void> {
  const res = await fetch(presignUrl, {
    method: "PUT",
    body: file,
    headers: {
      ...requiredHeaders,
    },
  });
  if (!res.ok) {
    throw new Error(`S3 upload failed: ${res.status} ${res.statusText}`);
  }
}

export async function uploadAndProcess(
  file: File,
  config: { max_snapshots?: number; snapshot_strategy?: string } = {}
): Promise<ProcessVideoResponse> {
  // Step 1: Get presigned URL
  const presign = await getPresignedUrl(file.name, file.type || "video/mp4");

  // Step 2: Upload to S3 via presigned URL
  await uploadToPresignedUrl(presign.presign_url, file, presign.required_headers);

  // Step 3: Trigger processing with s3_key (form-encoded)
  const form = new FormData();
  form.append("s3_key", presign.s3_key);
  form.append("config", JSON.stringify(config));

  return apiFetch("/process-video", { method: "POST", body: form });
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return apiFetch(`/status/${jobId}`);
}

export async function getJobResults(jobId: string): Promise<JobResults> {
  return apiFetch(`/results/${jobId}`);
}

export async function getPipelineResult(jobId: string): Promise<PipelineResultJson> {
  return apiFetch(`/download/json/${jobId}`);
}

export async function listJobs(limit = 50): Promise<Job[]> {
  const data = await apiFetch<{ jobs: Job[] }>(`/jobs?limit=${limit}`);
  return data.jobs;
}

export function getFrameUrl(jobId: string, frameIdx: number): string {
  return `${API_BASE}/download/frame/${jobId}/${frameIdx}`;
}

export async function cleanupJob(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/cleanup/${jobId}`, { method: "DELETE" });
}
