/**
 * Real Lightship backend client for the template UI.
 *
 * When NEXT_PUBLIC_API_BASE is empty the browser makes same-origin requests
 * and the ALB's path-based routing delivers them to the Lambda backend.
 */

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '');

export type PresignResponse = {
  presign_url: string;
  s3_key: string;
  required_headers: Record<string, string>;
};

export type ProcessVideoResponse = {
  job_id: string;
  status: string;
};

export type JobStatusResponse = {
  status: string;
  progress: number;
  message: string;
  current_step?: string;
};

export type BackendObjectLabel = {
  description: string;
  start_time_ms: number;
  distance: string;
  priority: string;
  location_description?: string;
  center?: { x: number; y: number } | null;
  polygon?: Array<{ x: number; y: number }> | null;
  x_min?: number | null;
  y_min?: number | null;
  x_max?: number | null;
  y_max?: number | null;
  width?: number | null;
  height?: number | null;
};

export type BackendHazardEvent = {
  start_time_ms: number;
  hazard_type: string;
  hazard_description: string;
  hazard_severity: string;
  road_conditions: string;
  duration_ms?: number | null;
};

export type BackendVideoOutput = {
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
  objects: BackendObjectLabel[];
  hazard_events: BackendHazardEvent[];
  run_metadata?: BackendRunMetadata;
  vision_audit?: {
    backend?: string;
    lane_backend?: string;
    frames_evaluated?: number;
  };
};

export type BackendRunMetadata = {
  filename?: string;
  snapshot_strategy?: string;
  frame_selection_method?: string;
  max_snapshots?: number | string;
  native_sampling_mode?: string;
  native_fps?: number | string | null;
  detector_backend?: string;
  lane_backend?: string | null;
};

export type ClientConfigsBundle = {
  video_class: string;
  configs: {
    reactivity: Record<string, unknown>;
    educational: Record<string, unknown>;
    hazard: Record<string, unknown>;
    jobsite: Record<string, unknown>;
  };
};

export type BackendJobRow = {
  job_id: string;
  status: string;
  filename?: string;
  created_at?: string;
  completed_at?: string;
  input_type?: string;
  max_snapshots?: number;
  snapshot_strategy?: string;
  native_sampling_mode?: string;
  native_fps?: number | string | null;
  detector_backend?: string;
  lane_backend?: string;
  error_message?: string;
  config?: BackendRunMetadata;
};

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;
  constructor(status: number, path: string, message: string) {
    super(message);
    this.status = status;
    this.path = path;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new ApiError(res.status, path, `API ${res.status} ${path}: ${body || res.statusText}`);
  }
  return (await res.json()) as T;
}

/**
 * Retry a promise-returning operation while it raises a 404 (or any
 * transient error). Used when a job has just completed but the worker's
 * S3 writes haven't reached read-after-write consistency for the
 * particular key yet, or while Dynamo writes are still catching up on a
 * cold Lambda.
 */
export async function retry404<T>(
  fn: () => Promise<T>,
  attempts = 5,
  delayMs = 1500,
): Promise<T> {
  let lastErr: unknown = null;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      const isRetryable =
        e instanceof ApiError ? e.status === 404 || e.status >= 500 : true;
      if (!isRetryable || i === attempts - 1) break;
      await new Promise((r) => setTimeout(r, delayMs * (i + 1)));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

export async function health(): Promise<{ status: string }> {
  return json('/health');
}

export async function presign(filename: string, contentType = 'video/mp4'): Promise<PresignResponse> {
  const qs = new URLSearchParams({ filename, content_type: contentType });
  return json<PresignResponse>(`/presign-upload?${qs}`);
}

export async function uploadToS3(
  presignUrl: string,
  file: File,
  headers: Record<string, string>,
): Promise<void> {
  const res = await fetch(presignUrl, {
    method: 'PUT',
    body: file,
    headers,
  });
  if (!res.ok) {
    throw new Error(`S3 upload failed (${res.status}): ${res.statusText}`);
  }
}

export type JobOptions = {
  max_snapshots?: number;
  snapshot_strategy?: string;
  native_fps?: number;
  native_sampling_mode?: 'count' | 'fps';
  detector_backend?: 'florence2' | 'yolo' | 'detectron2';
  lane_backend?: string;
  enable_llm_refinement?: boolean;
  enable_hazard_llm?: boolean;
};

export async function startVideoJob(
  s3Key: string,
  options: JobOptions = {},
): Promise<ProcessVideoResponse> {
  const form = new FormData();
  form.append('s3_key', s3Key);
  form.append('config', JSON.stringify(options));
  return json('/process-video', { method: 'POST', body: form });
}

export async function startS3VideoJob(
  s3Uri: string,
  options: JobOptions = {},
): Promise<ProcessVideoResponse & { input_type: string }> {
  return json('/process-s3-video', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ s3_uri: s3Uri, config: options }),
  });
}

export async function getStatus(jobId: string): Promise<JobStatusResponse> {
  return json(`/status/${jobId}`);
}

export async function getOutputJson(jobId: string): Promise<BackendVideoOutput> {
  return json(`/download/json/${jobId}`);
}

export async function getClientConfigs(jobId: string): Promise<ClientConfigsBundle> {
  return json(`/client-configs/${jobId}`);
}

export async function listBackendJobs(limit = 50): Promise<BackendJobRow[]> {
  const data = await json<{ jobs: BackendJobRow[] }>(`/jobs?limit=${limit}`);
  return data.jobs;
}

/** Delete every job in DynamoDB and remove ``results/{job_id}/`` (+ ``input/videos/{job_id}/``) in S3. */
export async function purgeAllBackendJobs(): Promise<{
  deleted_jobs: number;
  s3_results_objects_removed: number;
  s3_input_objects_removed: number;
}> {
  return json('/jobs', { method: 'DELETE' });
}

export async function processImage(file: File): Promise<{
  filename: string;
  camera: string;
  width: number;
  height: number;
  num_objects: number;
  objects: BackendObjectLabel[];
}> {
  const form = new FormData();
  form.append('image', file);
  return json('/process-image', { method: 'POST', body: form });
}

/**
 * Convenience: presign → S3 PUT → POST /process-video.
 * Returns the created job_id.
 */
export async function presignUploadAndStart(
  file: File,
  options: JobOptions = {},
): Promise<string> {
  const { presign_url, s3_key, required_headers } = await presign(
    file.name,
    file.type || 'video/mp4',
  );
  await uploadToS3(presign_url, file, required_headers);
  const { job_id } = await startVideoJob(s3_key, options);
  return job_id;
}

export type FrameManifestEntry = {
  frame_idx: number;
  timestamp_ms: number;
  num_objects: number;
  annotated_url: string | null;
  raw_url: string | null;
  json_url: string | null;
  extraction_source?: string | null;
  extraction_status?: string | null;
  width?: number | null;
  height?: number | null;
};

export type FrameManifest = {
  job_id: string;
  num_frames: number;
  frames: FrameManifestEntry[];
};

export async function getFrames(jobId: string): Promise<FrameManifest> {
  return json(`/frames/${jobId}`);
}

export type VideoClassInfo = {
  job_id: string;
  video_class: string;
  display_label: string;
  collision: string;
  weather: string;
  lighting: string;
  traffic: string;
};

export async function getVideoClass(jobId: string): Promise<VideoClassInfo> {
  return json(`/video-class/${jobId}`);
}

export async function pollJobToTerminal(
  jobId: string,
  onProgress: (s: JobStatusResponse) => void,
  intervalMs = 1000,
  timeoutMs = 15 * 60 * 1000,
): Promise<JobStatusResponse> {
  const deadline = Date.now() + timeoutMs;
  let last: JobStatusResponse = { status: 'QUEUED', progress: 0, message: 'Queued' };
  while (Date.now() < deadline) {
    try {
      last = await getStatus(jobId);
      onProgress(last);
      const s = (last.status || '').toUpperCase();
      if (s === 'COMPLETED' || s === 'FAILED') {
        return last;
      }
    } catch {
      // ignore transient errors and keep polling
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`Polling timed out after ${timeoutMs / 1000}s`);
}

export type BatchStatusRow = JobStatusResponse & {
  job_id: string;
};

export async function getBatchStatus(jobIds: Array<string>): Promise<Array<BatchStatusRow>> {
  if (jobIds.length === 0) return [];
  const qs = new URLSearchParams({ job_ids: jobIds.join(',') });
  const data = await json<{ jobs: Array<BatchStatusRow>; count: number }>(
    `/batch/status?${qs}`,
  );
  return data.jobs;
}

/**
 * Map of jobId -> latest status. Polls in one round-trip per tick so a
 * batch of 50 videos doesn't fan out to 50 parallel /status GETs.
 */
export async function pollBatchToTerminal(
  jobIds: Array<string>,
  onTick: (statuses: Map<string, BatchStatusRow>) => void,
  intervalMs = 1000,
  timeoutMs = 30 * 60 * 1000,
): Promise<Map<string, BatchStatusRow>> {
  const deadline = Date.now() + timeoutMs;
  const live = new Set(jobIds);
  const latest = new Map<string, BatchStatusRow>();

  while (live.size > 0 && Date.now() < deadline) {
    try {
      const rows = await getBatchStatus(Array.from(live));
      for (const row of rows) {
        latest.set(row.job_id, row);
        const s = (row.status || '').toUpperCase();
        if (s === 'COMPLETED' || s === 'FAILED' || s === 'NOT_FOUND') {
          live.delete(row.job_id);
        }
      }
      onTick(new Map(latest));
    } catch {
      // ignore transient errors and keep polling
    }
    if (live.size === 0) break;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return latest;
}

export type BatchItemInput = {
  s3_uri?: string;
  s3_key?: string;
  s3_prefix?: string;
  filename?: string;
  config?: JobOptions;
};

export type BatchProcessResponse = {
  jobs: Array<{
    job_id: string;
    filename: string;
    s3_key: string;
    dispatch: string;
    status: string;
  }>;
  count: number;
};

export async function batchProcess(items: Array<BatchItemInput>): Promise<BatchProcessResponse> {
  return json('/batch/process', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
}

/**
 * Download an entire job's annotated frames + output.json as a single ZIP.
 * Returns an object URL the caller can bind to an ``<a download>`` element.
 */
export async function downloadFramesZipUrl(jobId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/download/frames-zip/${jobId}`);
  if (!res.ok) {
    throw new Error(`ZIP download failed (${res.status}): ${res.statusText}`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
