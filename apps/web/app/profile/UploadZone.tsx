"use client";

import { useRef } from "react";

const ACCEPT = ".pdf,.docx,.txt,.md";

// Feedback (uploading / analyzing / done / failed) lives entirely in
// UploadFlowModal now — this component's only job is picking a file. `onUpload`
// is fire-and-forget from here on purpose; the parent owns the whole flow's state.
export function UploadZone({
  onUpload,
  hasResume,
}: {
  onUpload: (file: File) => void;
  hasResume: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-neutral-300 px-6 py-10 text-center hover:border-neutral-400"
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) onUpload(file);
      }}
    >
      <p className="text-sm font-medium text-neutral-700">
        {hasResume ? "Replace résumé" : "Drop your résumé here, or click to browse"}
      </p>
      <p className="mt-1 text-xs text-neutral-400">PDF, DOCX, TXT, or MD</p>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onUpload(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
