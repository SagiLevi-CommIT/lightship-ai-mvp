"use client";

import { useRef, useState } from "react";
import Image from "next/image";
import { Upload, Image as ImageIcon, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { processImage, type ImageResult } from "@/lib/api-client";

export default function ImagePage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<ImageResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const pick = (incoming: FileList | null) => {
    if (!incoming || incoming.length === 0) return;
    const f = incoming[0];
    if (!f.type.startsWith("image/")) {
      setError("Please choose an image file");
      return;
    }
    setError(null);
    setResult(null);
    setFile(f);
    setPreview(URL.createObjectURL(f));
  };

  const analyze = async () => {
    if (!file) return;
    setError(null);
    setLoading(true);
    try {
      const data = await processImage(file);
      setResult(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <ImageIcon className="h-6 w-6" />
          Single-image analysis
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Run Lightship object detection against a single dashcam still or
          job-site photo. Useful for quick QA, calibration, and spot-checks
          without running the full video pipeline.
        </p>
      </div>

      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          pick(e.dataTransfer.files);
        }}
        className={cn(
          "border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors",
          file ? "border-brand-400 bg-brand-50/40" : "border-gray-300 hover:border-brand-400 hover:bg-gray-50"
        )}
      >
        <Upload className="mx-auto h-10 w-10 text-gray-400" />
        <p className="mt-3 text-sm font-medium text-gray-700">
          Drag &amp; drop an image, or click to browse
        </p>
        <p className="mt-1 text-xs text-gray-400">JPG, PNG — up to 10 MB</p>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => pick(e.target.files)}
        />
      </div>

      {file && (
        <div className="flex items-center justify-between rounded-lg border bg-white px-4 py-3">
          <span className="text-sm text-gray-700 truncate">{file.name}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setFile(null);
                setPreview(null);
                setResult(null);
              }}
              className="p-1 rounded hover:bg-gray-100"
              aria-label="Remove"
            >
              <X className="h-4 w-4 text-gray-500" />
            </button>
            <button
              onClick={analyze}
              disabled={loading}
              className="px-4 py-1.5 rounded bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700 disabled:opacity-50"
            >
              {loading ? (
                <span className="flex items-center gap-1.5">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Analyzing…
                </span>
              ) : (
                "Analyze image"
              )}
            </button>
          </div>
        </div>
      )}

      {preview && (
        <div className="rounded-lg overflow-hidden border bg-white">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="Selected" className="max-h-[60vh] w-full object-contain" />
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && (
        <div className="rounded-lg border bg-white p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-gray-900">
              Detection summary
            </h2>
            <span className="text-xs text-gray-500">
              {result.width}×{result.height} · camera: {result.camera}
            </span>
          </div>
          <p className="text-sm text-gray-700">
            Detected <b>{result.num_objects}</b> objects
          </p>
          <ul className="divide-y text-sm">
            {result.objects.map((o, i) => (
              <li key={i} className="py-2 flex justify-between">
                <span>{o.description}</span>
                <span className="text-gray-500">
                  {o.priority} · {o.distance}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
