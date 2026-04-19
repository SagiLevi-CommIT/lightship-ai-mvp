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
  error_message?: string;
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status} ${path}: ${body || res.statusText}`);
  }
  return (await res.json()) as T;
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

export async function startVideoJob(
  s3Key: string,
  options: { max_snapshots?: number; snapshot_strategy?: string } = {},
): Promise<ProcessVideoResponse> {
  const form = new FormData();
  form.append('s3_key', s3Key);
  form.append('config', JSON.stringify(options));
  return json('/process-video', { method: 'POST', body: form });
}

export async function startS3VideoJob(
  s3Uri: string,
  options: { max_snapshots?: number; snapshot_strategy?: string } = {},
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
  options: { max_snapshots?: number; snapshot_strategy?: string } = {},
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
  intervalMs = 3000,
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
