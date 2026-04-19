'use client';

import type {
  AnnotatedFrame,
  AssetResult,
  Hazard,
  OutputCategory,
  PipelineConfig,
  PipelineResultJson,
  ResultPropertyRow,
  UploadedAsset,
} from '@/components/evaluation/flow-types';

const FRAME_WIDTH = 1280;
const FRAME_HEIGHT = 720;

const SEVERITY_MAP: Record<OutputCategory, Hazard['severity']> = {
  high: 'high',
  medium: 'medium',
  low: 'low',
  'all-frames': 'medium',
};

const createPropertyRows = (result: PipelineResultJson): Array<ResultPropertyRow> => {
  const objectCount = result.frames.reduce((total, frame) => total + frame.objects.length, 0);
  const laneCount = result.frames.reduce((total, frame) => total + frame.lanes.length, 0);
  const roadSignCount = result.frames.reduce((total, frame) => total + frame.road_signs.length, 0);
  const trafficSignalCount = result.frames.reduce((total, frame) => total + frame.traffic_signals.length, 0);

  return [
    { label: 'filename', value: result.filename },
    { label: 'video_description', value: result.video_description },
    { label: 'video_class', value: result.video_class },
    { label: 'road_type', value: result.road_type },
    { label: 'weather', value: result.weather },
    { label: 'traffic', value: result.traffic },
    { label: 'speed', value: result.speed },
    { label: 'hazards', value: `${result.hazards.length} item(s)` },
    { label: 'frames', value: `${result.frames.length} annotated frame(s)` },
    { label: 'objects', value: `${objectCount} detected object(s)` },
    { label: 'lanes', value: `${laneCount} lane polygon(s)` },
    { label: 'road_signs', value: `${roadSignCount} sign(s)` },
    { label: 'traffic_signals', value: `${trafficSignalCount} traffic signal(s)` },
  ];
};

const createFrame = (index: number, severity: Hazard['severity']): AnnotatedFrame => {
  const frameNumber = 372 + index * 24;
  const timestamp = 12.4 + index * 0.8;

  return {
    frame_number: frameNumber,
    timestamp_sec: Number(timestamp.toFixed(1)),
    objects: [
      {
        class: 'motorcycle',
        description: `Motorcycle moving close to ego lane, severity ${severity}`,
        distance: 'near',
        center: { x: 540 + index * 12, y: 380 - index * 5 },
        bbox: { x_min: 490 + index * 10, x_max: 590 + index * 10, y_min: 310, y_max: 450, width: 100, height: 140 },
      },
      {
        class: 'car',
        description: 'Silver sedan ahead in ego lane, brake lights on',
        distance: 'mid',
        center: { x: 640, y: 340 },
        bbox: { x_min: 560, x_max: 720, y_min: 290, y_max: 390, width: 160, height: 100 },
      },
      {
        class: 'cone',
        description: 'Construction cone guiding traffic away from the work zone',
        distance: 'far',
        center: { x: 680, y: 310 },
        bbox: { x_min: 665, x_max: 695, y_min: 285, y_max: 335, width: 30, height: 50 },
      },
    ],
    lanes: [
      {
        lane_id: 1,
        type: 'ego_lane',
        polygon: [
          [450, 720],
          [560, 400],
          [720, 400],
          [830, 720],
        ],
      },
      {
        lane_id: 2,
        type: 'other_lane',
        polygon: [
          [180, 720],
          [480, 400],
          [560, 400],
          [450, 720],
        ],
      },
    ],
    road_signs: [
      {
        label: 'speed limit',
        bbox: { x_min: 1050, x_max: 1090, y_min: 150, y_max: 200, width: 40, height: 50 },
      },
      {
        label: 'construction',
        bbox: { x_min: 780, x_max: 830, y_min: 130, y_max: 195, width: 50, height: 65 },
      },
    ],
    traffic_signals: [
      {
        label: 'green',
        bbox: { x_min: 610, x_max: 640, y_min: 90, y_max: 150, width: 30, height: 60 },
      },
    ],
  };
};

const createRawJson = (asset: UploadedAsset, config: PipelineConfig): PipelineResultJson => {
  const severity = SEVERITY_MAP[config.outputCategory];
  const frameCount = config.outputCategory === 'all-frames' ? 4 : 2;
  const descriptionPrefix = asset.kind === 'image' ? 'Uploaded road scene image' : 'Uploaded dashcam footage';

  return {
    filename: asset.name,
    video_description: `${descriptionPrefix} processed with ${config.frameSelectionMethod} frame selection and results stored at ${config.s3BucketPath}.`,
    video_class: asset.kind === 'image' ? 'qa_educational' : 'hazard_detection',
    road_type: 'highway',
    weather: 'moderate rain, reduced visibility',
    traffic: 'moderate',
    speed: asset.kind === 'image' ? 'unknown' : '>60 mph',
    hazards: [
      {
        timestamp_sec: 12.4,
        description: 'Motorcycle cuts into ego lane from left without signaling',
        severity,
      },
    ],
    frames: Array.from({ length: frameCount }, (_, index) => createFrame(index, severity)),
  };
};

export const createRunId = () => `run_${Date.now()}`;

export const createResultsByAssetId = (
  assets: Array<UploadedAsset>,
  config: PipelineConfig,
): Record<string, AssetResult> => {
  return assets.reduce<Record<string, AssetResult>>((results, asset) => {
    const rawJson = createRawJson(asset, config);

    results[asset.id] = {
      assetId: asset.id,
      assetName: asset.name,
      previewUrl: asset.previewUrl,
      kind: asset.kind,
      rawJson,
      propertyRows: createPropertyRows(rawJson),
    };

    return results;
  }, {});
};

export const getFrameCanvasSize = () => ({
  width: FRAME_WIDTH,
  height: FRAME_HEIGHT,
});
