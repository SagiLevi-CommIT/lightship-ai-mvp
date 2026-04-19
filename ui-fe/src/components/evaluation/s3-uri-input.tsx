'use client';

import { useState } from 'react';

type Props = {
  onAdd: (uri: string) => void;
};

export default function S3UriInput({ onAdd }: Props) {
  const [value, setValue] = useState('s3://s3-lightship-custom-datasources-us-east-1/');
  const [error, setError] = useState<string | null>(null);

  const handleAdd = () => {
    const trimmed = value.trim();
    if (!trimmed.startsWith('s3://')) {
      setError('URI must start with s3://');
      return;
    }
    const rest = trimmed.slice(5);
    if (rest.indexOf('/') < 0) {
      setError('URI must include a key: s3://bucket/key');
      return;
    }
    setError(null);
    onAdd(trimmed);
  };

  return (
    <div className="mt-6 rounded-2xl border border-cyan-500/25 bg-slate-950/70 p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-white">
            Or load a video from S3
          </h3>
          <p className="mt-0.5 text-xs text-slate-400">
            Paste a full <span className="font-mono text-slate-300">s3://bucket/key</span> URI —
            the pipeline will copy it into the processing bucket and run.
          </p>
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <input
          type="text"
          data-test-id="s3-uri-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="s3://bucket/prefix/video.mp4"
          className="w-full rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 font-mono text-xs text-white outline-none focus:border-cyan-400"
        />
        <button
          type="button"
          onClick={handleAdd}
          className="rounded-lg bg-cyan-500/20 px-4 text-xs font-semibold text-cyan-200 transition hover:bg-cyan-500/30"
        >
          Add S3 video
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-rose-300">{error}</p> : null}
    </div>
  );
}
