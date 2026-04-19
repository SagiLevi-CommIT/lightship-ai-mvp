'use client';

import { useRef, useState } from 'react';

type UploadDropzoneProps = {
  multiple: boolean;
  accept: string;
  title: string;
  description: string;
  onFilesSelected: (files: FileList) => void;
};

export default function UploadDropzone({
  multiple,
  accept,
  title,
  description,
  onFilesSelected,
}: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState<boolean>(false);

  const handleOpenPicker = () => {
    inputRef.current?.click();
  };

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) {
      return;
    }

    onFilesSelected(files);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      data-test-id="upload-dropzone"
      onClick={handleOpenPicker}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleOpenPicker();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setIsDragging(false);
        handleFiles(event.dataTransfer.files);
      }}
      className={`rounded-[28px] border-2 border-dashed p-8 text-center transition ${
        isDragging
          ? 'border-cyan-400 bg-cyan-500/10'
          : 'border-cyan-500/30 bg-slate-950/75 hover:border-cyan-300 hover:bg-slate-950/85'
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(event) => handleFiles(event.target.files)}
      />

      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 text-xl text-white shadow-[0_0_20px_rgba(56,189,248,0.35)]">
        +
      </div>
      <h2 className="mt-5 font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">{title}</h2>
      <p className="mx-auto mt-3 max-w-3xl text-sm leading-7 text-slate-300">{description}</p>
      <p className="mt-6 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
        Drag and drop or click to browse
      </p>
    </div>
  );
}
