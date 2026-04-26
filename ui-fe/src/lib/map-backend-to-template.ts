import type {
  AnnotatedFrame,
  AssetResult,
  FrameObject,
  Hazard,
  JsonBBox,
  Lane,
  PipelineResultJson,
  ResultPropertyRow,
  RoadSign,
  TrafficSignal,
  UploadedAsset,
} from '@/components/evaluation/flow-types';
import type {
  BackendObjectLabel,
  BackendVideoOutput,
  ClientConfigsBundle,
} from '@/lib/api';

const SEVERITY_RANK: Record<string, Hazard['severity']> = {
  Critical: 'high',
  High: 'high',
  Medium: 'medium',
  Low: 'low',
  None: 'low',
};

const ROAD_SIGN_KEYWORDS = ['sign', 'speed', 'stop', 'yield', 'warning'];
const TRAFFIC_SIGNAL_KEYWORDS = ['signal', 'light', 'traffic_light', 'traffic-light'];
const LANE_KEYWORDS = ['lane', 'lane(current)', 'lane_current', 'lane_other'];

const bboxOf = (obj: BackendObjectLabel): JsonBBox | null => {
  if (
    obj.x_min != null &&
    obj.y_min != null &&
    obj.x_max != null &&
    obj.y_max != null
  ) {
    const w = Math.max(0, obj.x_max - obj.x_min);
    const h = Math.max(0, obj.y_max - obj.y_min);
    return {
      x_min: obj.x_min,
      x_max: obj.x_max,
      y_min: obj.y_min,
      y_max: obj.y_max,
      width: obj.width ?? w,
      height: obj.height ?? h,
    };
  }
  if (obj.center) {
    const { x, y } = obj.center;
    return { x_min: x - 10, x_max: x + 10, y_min: y - 10, y_max: y + 10, width: 20, height: 20 };
  }
  return null;
};

const isLane = (d: string) => LANE_KEYWORDS.some((k) => d.toLowerCase().includes(k));
const isRoadSign = (d: string) => ROAD_SIGN_KEYWORDS.some((k) => d.toLowerCase().includes(k));
const isSignal = (d: string) => TRAFFIC_SIGNAL_KEYWORDS.some((k) => d.toLowerCase().includes(k));

const toFrameObject = (obj: BackendObjectLabel): FrameObject | null => {
  const bbox = bboxOf(obj);
  if (!bbox) return null;
  const center = obj.center ?? {
    x: Math.round((bbox.x_min + bbox.x_max) / 2),
    y: Math.round((bbox.y_min + bbox.y_max) / 2),
  };
  return {
    class: obj.description,
    description: obj.location_description || obj.description,
    distance: obj.distance,
    center,
    bbox,
  };
};

const toLane = (obj: BackendObjectLabel, laneId: number): Lane | null => {
  if (!obj.polygon || obj.polygon.length === 0) {
    const bbox = bboxOf(obj);
    if (!bbox) return null;
    return {
      lane_id: laneId,
      type: obj.description.toLowerCase().includes('current') ? 'ego_lane' : 'other_lane',
      polygon: [
        [bbox.x_min, bbox.y_max],
        [bbox.x_min, bbox.y_min],
        [bbox.x_max, bbox.y_min],
        [bbox.x_max, bbox.y_max],
      ],
    };
  }
  return {
    lane_id: laneId,
    type: obj.description.toLowerCase().includes('current') ? 'ego_lane' : 'other_lane',
    polygon: obj.polygon.map(({ x, y }) => [x, y] as [number, number]),
  };
};

const toRoadSign = (obj: BackendObjectLabel): RoadSign | null => {
  const bbox = bboxOf(obj);
  if (!bbox) return null;
  return { label: obj.description, bbox };
};

const toTrafficSignal = (obj: BackendObjectLabel): TrafficSignal | null => {
  const bbox = bboxOf(obj);
  if (!bbox) return null;
  return { label: obj.description, bbox };
};

const groupByTimestamp = (objects: BackendObjectLabel[]): Map<number, BackendObjectLabel[]> => {
  const map = new Map<number, BackendObjectLabel[]>();
  for (const o of objects) {
    const key = Math.round(o.start_time_ms);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(o);
  }
  return map;
};

export function backendToPipelineResultJson(
  bv: BackendVideoOutput,
  configs?: ClientConfigsBundle | null,
): PipelineResultJson {
  const byTs = groupByTimestamp(bv.objects);
  const timestamps = Array.from(byTs.keys()).sort((a, b) => a - b);

  const frames: AnnotatedFrame[] = timestamps.map((ts, idx) => {
    const objs = byTs.get(ts) ?? [];
    const frameObjects: FrameObject[] = [];
    const lanes: Lane[] = [];
    const roadSigns: RoadSign[] = [];
    const signals: TrafficSignal[] = [];

    let laneCounter = 0;
    for (const o of objs) {
      const d = o.description.toLowerCase();
      if (isLane(d)) {
        const lane = toLane(o, ++laneCounter);
        if (lane) lanes.push(lane);
      } else if (isSignal(d)) {
        const s = toTrafficSignal(o);
        if (s) signals.push(s);
      } else if (isRoadSign(d)) {
        const s = toRoadSign(o);
        if (s) roadSigns.push(s);
      } else {
        const fo = toFrameObject(o);
        if (fo) frameObjects.push(fo);
      }
    }

    return {
      frame_number: idx,
      timestamp_sec: ts / 1000,
      objects: frameObjects,
      lanes,
      road_signs: roadSigns,
      traffic_signals: signals,
    };
  });

  const hazards: Hazard[] = bv.hazard_events.map((h) => ({
    timestamp_sec: h.start_time_ms / 1000,
    description: h.hazard_description,
    severity: SEVERITY_RANK[h.hazard_severity] ?? 'medium',
  }));

  return {
    filename: bv.filename,
    video_description: bv.description || 'Processed dashcam footage',
    video_class: configs?.video_class ?? (bv.collision !== 'none' ? 'hazard_detection' : 'reactivity'),
    road_type: bv.camera || 'unknown',
    weather: bv.weather || 'unknown',
    traffic: bv.traffic || 'unknown',
    speed: bv.speed || 'unknown',
    hazards,
    frames,
  };
}

export function pipelineResultToPropertyRows(result: PipelineResultJson): ResultPropertyRow[] {
  const objectCount = result.frames.reduce((t, f) => t + f.objects.length, 0);
  const laneCount = result.frames.reduce((t, f) => t + f.lanes.length, 0);
  const signCount = result.frames.reduce((t, f) => t + f.road_signs.length, 0);
  const signalCount = result.frames.reduce((t, f) => t + f.traffic_signals.length, 0);
  return [
    { label: 'filename', value: result.filename },
    { label: 'video_description', value: result.video_description },
    { label: 'video_class', value: result.video_class },
    { label: 'camera', value: result.road_type },
    { label: 'weather', value: result.weather },
    { label: 'traffic', value: result.traffic },
    { label: 'speed', value: result.speed },
    { label: 'hazards', value: `${result.hazards.length} event(s)` },
    { label: 'frames', value: `${result.frames.length} annotated frame(s)` },
    { label: 'objects', value: `${objectCount} detected object(s)` },
    { label: 'lanes', value: `${laneCount} lane polygon(s)` },
    { label: 'road_signs', value: `${signCount} sign(s)` },
    { label: 'traffic_signals', value: `${signalCount} traffic signal(s)` },
  ];
}

export function buildAssetResultFromBackend(
  asset: UploadedAsset,
  bv: BackendVideoOutput,
  configs?: ClientConfigsBundle | null,
): AssetResult {
  const rawJson = backendToPipelineResultJson(bv, configs);
  return {
    assetId: asset.id,
    assetName: asset.name,
    previewUrl: asset.previewUrl,
    kind: asset.kind,
    rawJson,
    propertyRows: pipelineResultToPropertyRows(rawJson),
  };
}
