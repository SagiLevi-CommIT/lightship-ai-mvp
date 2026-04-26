/**
 * Backend API client for the Lightship pipeline.
 *
 * Replaces mock-results.ts with real HTTP calls to the FastAPI backend.
 * The backend serves PipelineResultJson at GET /pipeline-result/{jobId}.
 */

import type {
  PipelineResultJson,
  PipelineConfig,
  AssetResult,
  ResultPropertyRow,
} from '@/components/evaluation/flow-types';

const API_BASE =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_BASE ?? '')
    : '';

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}

export async function healthCheck(): Promise<boolean> {
  try {
    await apiFetch('/health');
    return true;
  } catch {
    return false;
  }
}

export type PresignResponse = {
  upload_url: string;
  s3_key: string;
};

export async function getPresignedUploadUrl(
  filename: string,
): Promise<PresignResponse> {
  return apiFetch<PresignResponse>(
    `/presign-upload?filename=${encodeURIComponent(filename)}`,
  );
}

export async function uploadToPresignedUrl(
  url: string,
  file: File,
): Promise<void> {
  const res = await fetch(url, {
    method: 'PUT',
    body: file,
    headers: {
      'Content-Type': file.type || 'video/mp4',
    },
  });

  if (!res.ok) {
    throw new Error(`S3 upload failed: ${res.status}`);
  }
}

export type ProcessVideoRequest = {
  s3_key: string;
  filename: string;
  config?: {
    snapshot_strategy?: string;
    max_snapshots?: number;
    s3_output_path?: string;
  };
};

export type ProcessVideoResponse = {
  job_id: string;
  status: string;
  message: string;
};

export async function processVideo(
  req: ProcessVideoRequest,
): Promise<ProcessVideoResponse> {
  return apiFetch<ProcessVideoResponse>('/process-video', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export type JobStatus = {
  status: string;
  progress: number;
  current_stage?: string;
  stages_completed?: number;
  total_stages?: number;
  message?: string;
};

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return apiFetch<JobStatus>(`/status/${jobId}`);
}

export async function getPipelineResult(
  jobId: string,
): Promise<PipelineResultJson> {
  return apiFetch<PipelineResultJson>(`/pipeline-result/${jobId}`);
}

export type HistoricalJob = {
  job_id: string;
  status: string;
  filename?: string;
  video_class?: string;
  road_type?: string;
  created_at?: string;
  completed_at?: string;
  s3_results_uri?: string;
  current_stage?: string;
  progress?: string;
};

export async function listJobs(
  limit: number = 50,
): Promise<Array<HistoricalJob>> {
  return apiFetch<Array<HistoricalJob>>(`/jobs?limit=${limit}`);
}

export async function downloadJson(jobId: string): Promise<unknown> {
  return apiFetch(`/download/json/${jobId}`);
}

/**
 * Upload a file and start processing. Returns the job_id.
 */
export async function uploadAndProcess(
  file: File,
  config: PipelineConfig,
): Promise<string> {
  let s3Key: string;

  try {
    const presign = await getPresignedUploadUrl(file.name);
    await uploadToPresignedUrl(presign.upload_url, file);
    s3Key = presign.s3_key;
  } catch {
    const formData = new FormData();
    formData.append('file', file);
    formData.append(
      'config',
      JSON.stringify({
        snapshot_strategy:
          config.frameSelectionMethod === 'scene-change'
            ? 'scene_change'
            : config.frameSelectionMethod === 'native'
              ? 'naive'
              : 'clustering',
        max_snapshots: parseInt(config.nativeFps, 10) || 5,
        s3_output_path: config.s3BucketPath,
      }),
    );

    const res = await fetch(`${API_BASE}/process-video`, {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      throw new Error(`Upload failed: ${res.status}`);
    }

    const data = await res.json();
    return data.job_id;
  }

  const resp = await processVideo({
    s3_key: s3Key,
    filename: file.name,
    config: {
      snapshot_strategy:
        config.frameSelectionMethod === 'scene-change'
          ? 'scene_change'
          : config.frameSelectionMethod === 'native'
            ? 'naive'
            : 'clustering',
      max_snapshots: parseInt(config.nativeFps, 10) || 5,
      s3_output_path: config.s3BucketPath,
    },
  });

  return resp.job_id;
}

/**
 * Convert PipelineResultJson to the AssetResult shape used by the UI.
 */
export function toAssetResult(
  assetId: string,
  assetName: string,
  previewUrl: string,
  kind: 'video' | 'image',
  raw: PipelineResultJson,
): AssetResult {
  const rows: Array<ResultPropertyRow> = [
    { label: 'Filename', value: raw.filename },
    { label: 'Video Class', value: raw.video_class },
    { label: 'Road Type', value: raw.road_type },
    { label: 'Weather', value: raw.weather },
    { label: 'Traffic', value: raw.traffic },
    { label: 'Speed', value: raw.speed },
    { label: 'Description', value: raw.video_description || '—' },
    { label: 'Frames Analyzed', value: String(raw.frames.length) },
    {
      label: 'Total Objects',
      value: String(raw.frames.reduce((sum, f) => sum + f.objects.length, 0)),
    },
    { label: 'Hazards', value: String(raw.hazards.length) },
  ];

  return {
    assetId,
    assetName,
    previewUrl,
    kind,
    rawJson: raw,
    propertyRows: rows,
  };
}
