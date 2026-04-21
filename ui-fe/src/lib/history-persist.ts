'use client';

import type { HistoricalRun } from '@/components/evaluation/flow-types';

const STORAGE_KEY = 'lightship.history.v1';
const MAX_ENTRIES = 25;

export type PersistedRun = HistoricalRun;

export function loadPersistedHistory(): Array<PersistedRun> {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Array<PersistedRun>;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((run) => run && typeof run === 'object' && typeof run.runId === 'string')
      .slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

export function persistHistory(runs: Array<PersistedRun>): void {
  if (typeof window === 'undefined') return;
  try {
    const sliced = runs.slice(0, MAX_ENTRIES);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sliced));
  } catch {
    /* quota exceeded — drop silently */
  }
}

export function clearPersistedHistory(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* no-op */
  }
}
