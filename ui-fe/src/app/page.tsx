"use client";

import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Upload, X, Film, Settings, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export default function UploadPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [maxSnapshots, setMaxSnapshots] = useState(5);
  const [strategy, setStrategy] = useState("clustering");
  const [showConfig, setShowConfig] = useState(false);
  const [dragging, setDragging] = useState(false);

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    const accepted = Array.from(incoming).filter((f) =>
      f.type.startsWith("video/")
    );
    setFiles((prev) => [...prev, ...accepted]);
  }, []);

  const removeFile = (idx: number) =>
    setFiles((prev) => prev.filter((_, i) => i !== idx));

  const startRun = () => {
    if (files.length === 0) return;
    const payload = files.map((f) => ({
      name: f.name,
      size: f.size,
      type: f.type,
    }));
    sessionStorage.setItem("ls_files", JSON.stringify(payload));
    sessionStorage.setItem("ls_max_snapshots", String(maxSnapshots));
    sessionStorage.setItem("ls_strategy", strategy);

    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    (window as any).__ls_files = dt.files;

    router.push("/run");
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          Dashcam Video Analysis
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Upload dashcam videos for automated object detection, hazard analysis,
          and safety assessment.
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors",
          dragging
            ? "border-brand-500 bg-brand-50"
            : "border-gray-300 hover:border-brand-400 hover:bg-gray-50"
        )}
      >
        <Upload className="mx-auto h-10 w-10 text-gray-400" />
        <p className="mt-3 text-sm font-medium text-gray-700">
          Drag &amp; drop video files here, or click to browse
        </p>
        <p className="mt-1 text-xs text-gray-400">MP4, AVI, MOV — up to 500 MB</p>
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          multiple
          className="hidden"
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {/* File list */}
      {files.length > 0 && (
        <ul className="divide-y rounded-lg border bg-white">
          {files.map((f, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3">
              <Film className="h-5 w-5 text-brand-500 shrink-0" />
              <span className="flex-1 text-sm truncate">{f.name}</span>
              <span className="text-xs text-gray-400">
                {(f.size / 1024 / 1024).toFixed(1)} MB
              </span>
              <button
                onClick={() => removeFile(i)}
                className="p-1 rounded hover:bg-gray-100"
              >
                <X className="h-4 w-4 text-gray-400" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Config */}
      <div>
        <button
          onClick={() => setShowConfig(!showConfig)}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700"
        >
          <Settings className="h-4 w-4" />
          Processing Settings
          <ChevronDown
            className={cn("h-4 w-4 transition-transform", showConfig && "rotate-180")}
          />
        </button>
        {showConfig && (
          <div className="mt-3 grid grid-cols-2 gap-4 rounded-lg border bg-white p-4">
            <label className="space-y-1">
              <span className="text-xs font-medium text-gray-600">
                Max Snapshots
              </span>
              <input
                type="number"
                min={1}
                max={20}
                value={maxSnapshots}
                onChange={(e) => setMaxSnapshots(Number(e.target.value))}
                className="block w-full rounded border px-3 py-1.5 text-sm"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-gray-600">
                Frame Strategy
              </span>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="block w-full rounded border px-3 py-1.5 text-sm"
              >
                <option value="clustering">Clustering (Smart)</option>
                <option value="naive">Uniform</option>
                <option value="scene_change">Scene Change</option>
              </select>
            </label>
          </div>
        )}
      </div>

      {/* Run button */}
      <button
        disabled={files.length === 0}
        onClick={startRun}
        className={cn(
          "w-full py-3 rounded-lg text-sm font-semibold transition-colors",
          files.length > 0
            ? "bg-brand-600 text-white hover:bg-brand-700"
            : "bg-gray-200 text-gray-400 cursor-not-allowed"
        )}
      >
        Analyze {files.length > 0 ? `${files.length} Video${files.length > 1 ? "s" : ""}` : "Videos"}
      </button>
    </div>
  );
}
