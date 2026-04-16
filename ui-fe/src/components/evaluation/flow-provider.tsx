"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import type { Asset, AssetResult, RunState, RunStage } from "@/lib/types";

interface FlowContextValue {
  state: RunState;
  addAssets: (files: File[]) => void;
  removeAsset: (id: string) => void;
  setConfig: (cfg: Partial<RunState["config"]>) => void;
  setStage: (stage: RunStage) => void;
  setProgress: (p: number, msg: string) => void;
  setJobId: (assetId: string, jobId: string) => void;
  completeRun: (assetId: string, result: AssetResult) => void;
  reset: () => void;
}

const initial: RunState = {
  assets: [],
  config: { max_snapshots: 5, snapshot_strategy: "clustering" },
  stage: "uploading",
  progress: 0,
  message: "",
  jobIds: {},
  results: {},
};

const FlowContext = createContext<FlowContextValue | null>(null);

export function useFlow() {
  const ctx = useContext(FlowContext);
  if (!ctx) throw new Error("useFlow must be used within FlowProvider");
  return ctx;
}

export function FlowProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RunState>(initial);

  const addAssets = useCallback((files: File[]) => {
    const newAssets: Asset[] = files.map((f) => ({
      id: crypto.randomUUID(),
      file: f,
      name: f.name,
    }));
    setState((s) => ({ ...s, assets: [...s.assets, ...newAssets] }));
  }, []);

  const removeAsset = useCallback((id: string) => {
    setState((s) => ({
      ...s,
      assets: s.assets.filter((a) => a.id !== id),
    }));
  }, []);

  const setConfig = useCallback(
    (cfg: Partial<RunState["config"]>) => {
      setState((s) => ({ ...s, config: { ...s.config, ...cfg } }));
    },
    []
  );

  const setStage = useCallback((stage: RunStage) => {
    setState((s) => ({ ...s, stage }));
  }, []);

  const setProgress = useCallback((progress: number, message: string) => {
    setState((s) => ({ ...s, progress, message }));
  }, []);

  const setJobId = useCallback((assetId: string, jobId: string) => {
    setState((s) => ({
      ...s,
      jobIds: { ...s.jobIds, [assetId]: jobId },
    }));
  }, []);

  const completeRun = useCallback((assetId: string, result: AssetResult) => {
    setState((s) => ({
      ...s,
      results: { ...s.results, [assetId]: result },
    }));
  }, []);

  const reset = useCallback(() => setState(initial), []);

  return (
    <FlowContext.Provider
      value={{
        state,
        addAssets,
        removeAsset,
        setConfig,
        setStage,
        setProgress,
        setJobId,
        completeRun,
        reset,
      }}
    >
      {children}
    </FlowContext.Provider>
  );
}
