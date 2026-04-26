'use client';

import { useId, useRef, useState } from 'react';

type UploadDropzoneProps = {
  multiple: boolean;
  accept: string;
  title: string;
  description: string;
  onFilesSelected: (files: Array<File>) => void | Promise<void>;
};

export default function UploadDropzone({
  multiple,
  accept,
  title,
  description,
  onFilesSelected,
}: UploadDropzoneProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [isDragging, setIsDragging] = useState<boolean>(false);

  const handleOpenPicker = () => {
    inputRef.current?.click();
  };

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) {
      return;
    }

    void onFilesSelected(Array.from(files));
  };

  return (
    <label
      htmlFor={inputId}
      role="button"
      tabIndex={0}
      data-test-id="upload-dropzone"
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleOpenPicker();
        }
      }}
      onDragEnter={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setIsDragging(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setIsDragging(true);
      }}
      onDragLeave={(event) => {
        event.preventDefault();
        event.stopPropagation();

        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }

        setIsDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        event.stopPropagation();
        setIsDragging(false);
        handleFiles(event.dataTransfer.files);
      }}
      className={`block cursor-pointer rounded-[28px] border-2 border-dashed p-8 text-center transition focus:outline-none focus:ring-2 focus:ring-cyan-300/70 focus:ring-offset-2 focus:ring-offset-slate-950 ${
        isDragging
          ? 'border-cyan-400 bg-cyan-500/10'
          : 'border-cyan-500/30 bg-slate-950/75 hover:border-cyan-300 hover:bg-slate-950/85'
      }`}
    >
      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className="sr-only"
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          handleFiles(event.currentTarget.files);
          event.currentTarget.value = '';
        }}
      />

      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 text-xl text-white shadow-[0_0_20px_rgba(56,189,248,0.35)]">
        +
      </div>
      <h2 className="mt-5 font-[family:var(--font-ibm-plex-sans)] text-2xl font-semibold text-white">{title}</h2>
      <p className="mx-auto mt-3 max-w-3xl text-sm leading-7 text-slate-300">{description}</p>
      <p className="mt-6 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
        Drag and drop or click to browse
      </p>
    </label>
  );
}
