'use client';

export type ProcessingMode = 'batch' | 'evaluation';

export type MediaKind = 'image' | 'video';

export type AssetStatus = 'ready' | 'queued' | 'running' | 'completed' | 'failed';

export type FrameSelectionMethod = 'native' | 'scene-change';

export type OutputCategory = 'high' | 'medium' | 'low' | 'all-frames';

export type NotificationState = 'default' | 'granted' | 'denied' | 'unsupported';

export type UploadedAsset = {
  id: string;
  file: File;
  name: string;
  size: number;
  type: string;
  kind: MediaKind;
  previewUrl: string;
  status: AssetStatus;
  validationErrors: Array<string>;
  durationSec?: number;
  width?: number;
  height?: number;
};

export type PipelineConfig = {
  frameSelectionMethod: FrameSelectionMethod;
  nativeFps: string;
  s3BucketPath: string;
  outputCategory: OutputCategory;
};

export type RunPhase = 'idle' | 'queued' | 'running' | 'completed' | 'failed';

export type RunProgress = {
  phase: RunPhase;
  percent: number;
  currentStage: string;
  activeAssetId: string | null;
  totalAssets: number;
  completedAssets: number;
  startedAt: number | null;
  completedAt: number | null;
};

export type JsonCenter = {
  x: number;
  y: number;
};

export type JsonBBox = {
  x_min: number;
  x_max: number;
  y_min: number;
  y_max: number;
  width: number;
  height: number;
};

export type Hazard = {
  timestamp_sec: number;
  description: string;
  severity: 'high' | 'medium' | 'low';
};

export type FrameObject = {
  class: string;
  description: string;
  distance: string;
  center: JsonCenter;
  bbox: JsonBBox;
};

export type Lane = {
  lane_id: number;
  type: 'ego_lane' | 'other_lane';
  polygon: Array<[number, number]>;
};

export type RoadSign = {
  label: string;
  bbox: JsonBBox;
};

export type TrafficSignal = {
  label: string;
  bbox: JsonBBox;
};

export type AnnotatedFrame = {
  frame_number: number;
  timestamp_sec: number;
  objects: Array<FrameObject>;
  lanes: Array<Lane>;
  road_signs: Array<RoadSign>;
  traffic_signals: Array<TrafficSignal>;
};

export type PipelineResultJson = {
  filename: string;
  video_description: string;
  video_class: string;
  road_type: string;
  weather: string;
  traffic: string;
  speed: string;
  hazards: Array<Hazard>;
  frames: Array<AnnotatedFrame>;
};

export type ResultPropertyRow = {
  label: string;
  value: string;
};

export type AssetResult = {
  assetId: string;
  assetName: string;
  previewUrl: string;
  kind: MediaKind;
  rawJson: PipelineResultJson;
  propertyRows: Array<ResultPropertyRow>;
};

export type HistoricalRun = {
  runId: string;
  mode: ProcessingMode;
  createdAt: number;
  completedAt: number;
  assetCount: number;
  resultsByAssetId: Record<string, AssetResult>;
};

export type EvaluationFlowState = {
  mode: ProcessingMode;
  assets: Array<UploadedAsset>;
  selectedAssetId: string | null;
  pipelineConfig: PipelineConfig;
  configConfirmed: boolean;
  runProgress: RunProgress;
  currentRunId: string | null;
  resultsByAssetId: Record<string, AssetResult>;
  historicalRuns: Array<HistoricalRun>;
  notificationPermission: NotificationState;
  notificationMessage: string | null;
};
