'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react';
import {
  clearPersistedHistory as lsClearHistory,
  loadPersistedHistory,
  persistHistory,
  type PersistedRun,
} from '@/lib/history-persist';
import type { ReactNode } from 'react';
import type {
  AssetResult,
  AssetStatus,
  EvaluationFlowState,
  HistoricalRun,
  NotificationState,
  PipelineConfig,
  ProcessingMode,
  RunProgress,
  UploadedAsset,
} from '@/components/evaluation/flow-types';
import { uuidv4 } from '@/lib/uuid';

const DEFAULT_PIPELINE_CONFIG: PipelineConfig = {
  frameSelectionMethod: 'native',
  nativeSamplingMode: 'count',
  nativeFps: '2',
  maxSnapshots: '5',
  detectorBackend: 'florence2',
  s3BucketPath: 's3://lightship-mvp-processing-336090301206/results',
};

const DEFAULT_RUN_PROGRESS: RunProgress = {
  phase: 'idle',
  percent: 0,
  currentStage: 'Waiting to start',
  activeAssetId: null,
  totalAssets: 0,
  completedAssets: 0,
  startedAt: null,
  completedAt: null,
};

const VIDEO_CONTENT_TYPES: Record<string, string> = {
  avi: 'video/x-msvideo',
  m4v: 'video/x-m4v',
  mkv: 'video/x-matroska',
  mov: 'video/quicktime',
  mp4: 'video/mp4',
  mpeg: 'video/mpeg',
  mpg: 'video/mpeg',
  webm: 'video/webm',
  wmv: 'video/x-ms-wmv',
};

const getFileExtension = (filename: string) => filename.split('.').pop()?.toLowerCase() ?? '';

const inferVideoContentType = (filename: string) => VIDEO_CONTENT_TYPES[getFileExtension(filename)] ?? '';

const isVideoFile = (file: File) => file.type.startsWith('video/') || Boolean(inferVideoContentType(file.name));

const INITIAL_STATE: EvaluationFlowState = {
  mode: 'batch',
  assets: [],
  selectedAssetId: null,
  pipelineConfig: DEFAULT_PIPELINE_CONFIG,
  configConfirmed: false,
  runProgress: DEFAULT_RUN_PROGRESS,
  currentRunId: null,
  resultsByAssetId: {},
  historicalRuns: [],
  notificationPermission:
    typeof window !== 'undefined' && 'Notification' in window ? Notification.permission : 'unsupported',
  notificationMessage: null,
};

type FlowAction =
  | { type: 'SET_MODE'; payload: ProcessingMode }
  | { type: 'SET_ASSETS'; payload: Array<UploadedAsset> }
  | { type: 'REMOVE_ASSET'; payload: string }
  | { type: 'SELECT_ASSET'; payload: string | null }
  | { type: 'SET_ASSET_JOB_ID'; payload: { assetId: string; jobId: string } }
  | { type: 'UPDATE_PIPELINE_CONFIG'; payload: Partial<PipelineConfig> }
  | { type: 'SET_RUN_PROGRESS'; payload: RunProgress }
  | { type: 'SET_ASSET_STATUS'; payload: { assetId: string; status: AssetStatus } }
  | {
      type: 'SET_RESULTS';
      payload: {
        runId: string;
        mode: ProcessingMode;
        createdAt: number;
        completedAt: number;
        resultsByAssetId: Record<string, AssetResult>;
      };
    }
  | { type: 'SET_NOTIFICATION_PERMISSION'; payload: NotificationState }
  | { type: 'SET_NOTIFICATION_MESSAGE'; payload: string | null }
  | { type: 'HYDRATE_HISTORY'; payload: Array<HistoricalRun> }
  | { type: 'CLEAR_HISTORY' }
  | { type: 'RESET_FLOW' };

type EvaluationFlowContextValue = {
  state: EvaluationFlowState;
  setMode: (mode: ProcessingMode) => void;
  addFiles: (files: FileList | Array<File>) => Promise<void>;
  addS3Uri: (uri: string) => void;
  setAssetJobId: (assetId: string, jobId: string) => void;
  removeAsset: (assetId: string) => void;
  selectAsset: (assetId: string) => void;
  updatePipelineConfig: (patch: Partial<PipelineConfig>) => void;
  setRunProgress: (progress: RunProgress) => void;
  setAssetStatus: (assetId: string, status: AssetStatus) => void;
  completeRun: (
    runId: string,
    resultsByAssetId: Record<string, AssetResult>,
    message: string,
    metadata: { mode: ProcessingMode; createdAt: number; completedAt: number },
  ) => void;
  requestNotificationPermission: () => Promise<void>;
  setNotificationMessage: (message: string | null) => void;
  resetFlow: () => void;
  clearHistory: () => void;
};

const EvaluationFlowContext = createContext<EvaluationFlowContextValue | null>(null);

const revokePreviewUrls = (assets: Array<UploadedAsset>) => {
  assets.forEach((asset) => {
    URL.revokeObjectURL(asset.previewUrl);
  });
};

const createAssetId = () => `asset_${uuidv4()}`;

const getImageMetadata = (url: string) =>
  new Promise<{ width: number; height: number }>((resolve, reject) => {
    const image = new Image();

    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
    };

    image.onerror = () => {
      reject(new Error('Unable to read image metadata.'));
    };

    image.src = url;
  });

const getVideoMetadata = (url: string) =>
  new Promise<{ width: number; height: number; durationSec: number }>((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';

    video.onloadedmetadata = () => {
      resolve({
        width: video.videoWidth,
        height: video.videoHeight,
        durationSec: Number(video.duration.toFixed(1)),
      });
    };

    video.onerror = () => {
      reject(new Error('Unable to read video metadata.'));
    };

    video.src = url;
  });

const buildUploadedAsset = async (file: File): Promise<UploadedAsset> => {
  const previewUrl = URL.createObjectURL(file);
  const baseAsset: UploadedAsset = {
    id: createAssetId(),
    file,
    name: file.name,
    size: file.size,
    type: file.type || inferVideoContentType(file.name) || 'video/mp4',
    kind: file.type.startsWith('image/') ? 'image' : 'video',
    previewUrl,
    status: 'ready',
    validationErrors: [],
  };

  try {
    if (baseAsset.kind === 'image') {
      const metadata = await getImageMetadata(previewUrl);

      return {
        ...baseAsset,
        width: metadata.width,
        height: metadata.height,
      };
    }

    const metadata = await getVideoMetadata(previewUrl);

    return {
      ...baseAsset,
      width: metadata.width,
      height: metadata.height,
      durationSec: metadata.durationSec,
    };
  } catch {
    return baseAsset;
  }
};

const flowReducer = (state: EvaluationFlowState, action: FlowAction): EvaluationFlowState => {
  switch (action.type) {
    case 'SET_MODE':
      return {
        ...state,
        mode: action.payload,
        assets: [],
        selectedAssetId: null,
        runProgress: DEFAULT_RUN_PROGRESS,
        currentRunId: null,
        resultsByAssetId: {},
        notificationMessage: null,
      };

    case 'SET_ASSETS':
      return {
        ...state,
        assets: action.payload,
        selectedAssetId: action.payload[0]?.id ?? null,
      };

    case 'REMOVE_ASSET': {
      const nextAssets = state.assets.filter((asset) => asset.id !== action.payload);

      return {
        ...state,
        assets: nextAssets,
        selectedAssetId:
          state.selectedAssetId === action.payload ? (nextAssets[0]?.id ?? null) : state.selectedAssetId,
      };
    }

    case 'SELECT_ASSET':
      return {
        ...state,
        selectedAssetId: action.payload,
      };

    case 'UPDATE_PIPELINE_CONFIG':
      return {
        ...state,
        pipelineConfig: {
          ...state.pipelineConfig,
          ...action.payload,
        },
        configConfirmed: true,
      };

    case 'SET_RUN_PROGRESS':
      return {
        ...state,
        runProgress: action.payload,
      };

    case 'SET_ASSET_STATUS':
      return {
        ...state,
        assets: state.assets.map((asset) =>
          asset.id === action.payload.assetId ? { ...asset, status: action.payload.status } : asset,
        ),
      };

    case 'SET_ASSET_JOB_ID':
      return {
        ...state,
        assets: state.assets.map((asset) =>
          asset.id === action.payload.assetId
            ? { ...asset, jobId: action.payload.jobId }
            : asset,
        ),
      };

    case 'SET_RESULTS':
      const nextRun: HistoricalRun = {
        runId: action.payload.runId,
        mode: action.payload.mode,
        createdAt: action.payload.createdAt,
        completedAt: action.payload.completedAt,
        assetCount: Object.keys(action.payload.resultsByAssetId).length,
        resultsByAssetId: action.payload.resultsByAssetId,
      };

      return {
        ...state,
        currentRunId: action.payload.runId,
        resultsByAssetId: action.payload.resultsByAssetId,
        historicalRuns: [nextRun, ...state.historicalRuns.filter((run) => run.runId !== action.payload.runId)],
      };

    case 'HYDRATE_HISTORY':
      return {
        ...state,
        historicalRuns: action.payload,
      };

    case 'CLEAR_HISTORY':
      return {
        ...state,
        historicalRuns: [],
        currentRunId: null,
        resultsByAssetId: {},
      };

    case 'SET_NOTIFICATION_PERMISSION':
      return {
        ...state,
        notificationPermission: action.payload,
      };

    case 'SET_NOTIFICATION_MESSAGE':
      return {
        ...state,
        notificationMessage: action.payload,
      };

    case 'RESET_FLOW':
      return {
        ...INITIAL_STATE,
        historicalRuns: state.historicalRuns,
        notificationPermission:
          typeof window !== 'undefined' && 'Notification' in window ? Notification.permission : 'unsupported',
      };

    default:
      return state;
  }
};

export function FlowProvider({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  const [state, dispatch] = useReducer(flowReducer, INITIAL_STATE);
  const assetsRef = useRef<Array<UploadedAsset>>([]);

  useEffect(() => {
    assetsRef.current = state.assets;
  }, [state.assets]);

  useEffect(() => {
    return () => {
      revokePreviewUrls(assetsRef.current);
    };
  }, []);

  // Hydrate historical runs from localStorage once on mount so reloads
  // keep prior runs visible (and results navigation can fall back to
  // local history when the in-memory reducer state has been lost).
  useEffect(() => {
    const persisted = loadPersistedHistory();
    if (persisted.length > 0) {
      dispatch({ type: 'HYDRATE_HISTORY', payload: persisted });
    }
  }, []);

  useEffect(() => {
    persistHistory(state.historicalRuns);
  }, [state.historicalRuns]);

  const setMode = useCallback(
    (mode: ProcessingMode) => {
      revokePreviewUrls(state.assets);
      dispatch({ type: 'SET_MODE', payload: mode });
    },
    [state.assets],
  );

  const addFiles = useCallback(
    async (incomingFiles: FileList | Array<File>) => {
      const files = Array.from(incomingFiles);
      const validFiles = files.filter(isVideoFile);

      if (validFiles.length === 0) {
        dispatch({
          type: 'SET_NOTIFICATION_MESSAGE',
          payload: 'Batch mode accepts video files only. Supported files include MP4, MOV, M4V, AVI, MKV, WebM, MPEG, and WMV.',
        });
        return;
      }

      const uploadedAssets = await Promise.all(validFiles.map((file) => buildUploadedAsset(file)));
      const assets = [...state.assets, ...uploadedAssets];

      dispatch({ type: 'SET_ASSETS', payload: assets });
      dispatch({ type: 'SET_NOTIFICATION_MESSAGE', payload: null });
    },
    [state.assets],
  );

  const addS3Uri = useCallback(
    (uri: string) => {
      const trimmed = uri.trim();
      if (!trimmed.startsWith('s3://')) {
        dispatch({
          type: 'SET_NOTIFICATION_MESSAGE',
          payload: 'S3 URI must start with s3://bucket/key',
        });
        return;
      }
      const rest = trimmed.slice(5);
      const slash = rest.indexOf('/');
      if (slash < 0) {
        dispatch({
          type: 'SET_NOTIFICATION_MESSAGE',
          payload: 'S3 URI must include a key: s3://bucket/key',
        });
        return;
      }
      const [bucket, key] = [rest.slice(0, slash), rest.slice(slash + 1)];
      const name = key.split('/').pop() || key;
      const asset: UploadedAsset = {
        id: createAssetId(),
        source: 's3',
        s3Uri: trimmed,
        name,
        size: 0,
        type: 'video/mp4',
        kind: 'video',
        previewUrl: '',
        status: 'ready',
        validationErrors: [],
      };
      dispatch({ type: 'SET_ASSETS', payload: [...state.assets, asset] });
      dispatch({ type: 'SET_NOTIFICATION_MESSAGE', payload: null });
    },
    [state.assets],
  );

  const setAssetJobId = useCallback((assetId: string, jobId: string) => {
    dispatch({ type: 'SET_ASSET_JOB_ID', payload: { assetId, jobId } });
  }, []);

  const removeAsset = useCallback(
    (assetId: string) => {
      const asset = state.assets.find((item) => item.id === assetId);

      if (!asset) {
        return;
      }

      URL.revokeObjectURL(asset.previewUrl);
      dispatch({ type: 'REMOVE_ASSET', payload: assetId });
    },
    [state.assets],
  );

  const selectAsset = useCallback((assetId: string) => {
    dispatch({ type: 'SELECT_ASSET', payload: assetId });
  }, []);

  const updatePipelineConfig = useCallback((patch: Partial<PipelineConfig>) => {
    dispatch({ type: 'UPDATE_PIPELINE_CONFIG', payload: patch });
  }, []);

  const setRunProgress = useCallback((progress: RunProgress) => {
    dispatch({ type: 'SET_RUN_PROGRESS', payload: progress });
  }, []);

  const setAssetStatus = useCallback((assetId: string, status: AssetStatus) => {
    dispatch({ type: 'SET_ASSET_STATUS', payload: { assetId, status } });
  }, []);

  const completeRun = useCallback(
    (
      runId: string,
      resultsByAssetId: Record<string, AssetResult>,
      message: string,
      metadata: { mode: ProcessingMode; createdAt: number; completedAt: number },
    ) => {
      dispatch({
        type: 'SET_RESULTS',
        payload: {
          runId,
          mode: metadata.mode,
          createdAt: metadata.createdAt,
          completedAt: metadata.completedAt,
          resultsByAssetId,
        },
      });
      dispatch({ type: 'SET_NOTIFICATION_MESSAGE', payload: message });

      if (state.notificationPermission === 'granted' && 'Notification' in window) {
        new Notification('LightShip results are ready', {
          body: message,
        });
      }
    },
    [state.notificationPermission],
  );

  const requestNotificationPermission = useCallback(async () => {
    if (!('Notification' in window)) {
      dispatch({ type: 'SET_NOTIFICATION_PERMISSION', payload: 'unsupported' });
      dispatch({ type: 'SET_NOTIFICATION_MESSAGE', payload: 'Browser notifications are not supported here.' });
      return;
    }

    const permission = await Notification.requestPermission();
    dispatch({ type: 'SET_NOTIFICATION_PERMISSION', payload: permission });
    dispatch({
      type: 'SET_NOTIFICATION_MESSAGE',
      payload:
        permission === 'granted'
          ? 'Browser notifications enabled.'
          : 'Browser notifications were not enabled. In-app notifications will still be shown.',
    });
  }, []);

  const setNotificationMessage = useCallback((message: string | null) => {
    dispatch({ type: 'SET_NOTIFICATION_MESSAGE', payload: message });
  }, []);

  const resetFlow = useCallback(() => {
    revokePreviewUrls(state.assets);
    dispatch({ type: 'RESET_FLOW' });
  }, [state.assets]);

  const clearHistory = useCallback(() => {
    lsClearHistory();
    dispatch({ type: 'CLEAR_HISTORY' });
  }, []);

  const contextValue = useMemo<EvaluationFlowContextValue>(
    () => ({
      state,
      setMode,
      addFiles,
      addS3Uri,
      setAssetJobId,
      removeAsset,
      selectAsset,
      updatePipelineConfig,
      setRunProgress,
      setAssetStatus,
      completeRun,
      requestNotificationPermission,
      setNotificationMessage,
      resetFlow,
      clearHistory,
    }),
    [
      addFiles,
      addS3Uri,
      clearHistory,
      completeRun,
      removeAsset,
      requestNotificationPermission,
      resetFlow,
      selectAsset,
      setAssetJobId,
      setAssetStatus,
      setMode,
      setNotificationMessage,
      setRunProgress,
      state,
      updatePipelineConfig,
    ],
  );

  return <EvaluationFlowContext.Provider value={contextValue}>{children}</EvaluationFlowContext.Provider>;
}

export const useEvaluationFlow = () => {
  const context = useContext(EvaluationFlowContext);

  if (!context) {
    throw new Error('useEvaluationFlow must be used within FlowProvider.');
  }

  return context;
};
