import type { ImageChunk } from "./types";

const STORAGE_KEY = (threadId: string) => `open-swe:pending-prompts:${threadId}`;

export interface PendingPrompt {
  id: string;
  prompt: string;
  insertAt: number;
  createdAt: number;
  images?: Array<ImageChunk>;
}

function isImageChunk(value: unknown): value is ImageChunk {
  const image = value as { base64?: unknown; kind?: unknown; mimeType?: unknown };
  return (
    typeof value === "object" &&
    value !== null &&
    image.kind === "image" &&
    typeof image.base64 === "string" &&
    typeof image.mimeType === "string"
  );
}

function isPendingPrompt(value: unknown): value is PendingPrompt {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as PendingPrompt).id === "string" &&
    typeof (value as PendingPrompt).prompt === "string" &&
    typeof (value as PendingPrompt).insertAt === "number" &&
    typeof (value as PendingPrompt).createdAt === "number" &&
    ((value as PendingPrompt).images === undefined ||
      (Array.isArray((value as PendingPrompt).images) &&
        (value as PendingPrompt).images!.every(isImageChunk)))
  );
}

function normalizePendingPrompt(value: unknown, index: number): PendingPrompt | null {
  if (isPendingPrompt(value)) return value;
  if (
    typeof value !== "object" ||
    value === null ||
    typeof (value as PendingPrompt).prompt !== "string" ||
    typeof (value as PendingPrompt).insertAt !== "number"
  ) {
    return null;
  }
  const images = (value as PendingPrompt).images;
  if (images !== undefined && (!Array.isArray(images) || !images.every(isImageChunk))) {
    return null;
  }
  const createdAt = Date.now();
  return {
    id: `legacy-${createdAt}-${index}`,
    prompt: (value as PendingPrompt).prompt,
    insertAt: (value as PendingPrompt).insertAt,
    createdAt,
    images,
  };
}

function safeRead(threadId: string): Array<PendingPrompt> {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY(threadId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item, index) => normalizePendingPrompt(item, index))
      .filter((item): item is PendingPrompt => item !== null);
  } catch {
    return [];
  }
}

function safeWrite(threadId: string, prompts: Array<PendingPrompt>): void {
  if (typeof window === "undefined") return;
  if (prompts.length === 0) {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
    return;
  }
  try {
    window.sessionStorage.setItem(STORAGE_KEY(threadId), JSON.stringify(prompts));
  } catch {
    window.sessionStorage.removeItem(STORAGE_KEY(threadId));
  }
}

export function getPendingPrompts(threadId: string): Array<PendingPrompt> {
  return safeRead(threadId);
}

export function addPendingPrompt(
  threadId: string,
  prompt: string,
  insertAt: number,
  images?: Array<ImageChunk>,
): Array<PendingPrompt> {
  const createdAt = Date.now();
  const next = [
    ...safeRead(threadId),
    {
      id: `pending-${createdAt}-${Math.random().toString(36).slice(2)}`,
      prompt,
      insertAt,
      createdAt,
      images,
    },
  ];
  safeWrite(threadId, next);
  return next;
}

export function dropPendingPrompts(
  threadId: string,
  predicate: (entry: PendingPrompt) => boolean,
): Array<PendingPrompt> {
  const next = safeRead(threadId).filter((p) => !predicate(p));
  safeWrite(threadId, next);
  return next;
}
