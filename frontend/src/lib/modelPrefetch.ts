// Downloads the on-device models into the browser's Cache API ahead of any chat, for the student
// start page (pages/Start): on a shared iPad the first student of the day shouldn't wait for
// ~500 MB. Only downloads — the chat's workers still load the models themselves, now from disk.
// The avatar isn't included: which one a chat shows is only known once its key is entered.

import { MODEL_CACHES, openModelCache, prefetchModelFile, pruneOtherVersions, type ManifestFile } from "@/lib/modelFiles";
import { isWebGpuAvailable } from "@/lib/parakeetStt";
import { TURN_MODEL_URL, VAD_MODEL_URL } from "@/lib/vad";

export type ModelId = "vad" | "stt" | "tts";

export type PrefetchState =
  | { status: "waiting" }
  /** The server doesn't offer this model. */
  | { status: "unavailable" }
  /** This device can't run it (speech recognition needs WebGPU), so the chat won't use it. */
  | { status: "unsupported" }
  | { status: "downloading"; loadedBytes: number; totalBytes: number }
  | { status: "ready"; totalBytes: number }
  | { status: "error"; message: string };

interface Part {
  baseUrl: string;
  cache: string;
  /** The files the chat's worker loads from this manifest. */
  files(manifest: { files: Record<string, ManifestFile> }): ManifestFile[];
}

interface ModelPackage {
  id: ModelId;
  needsWebGpu: boolean;
  parts: Part[];
}

const allFiles: Part["files"] = (manifest) => Object.values(manifest.files);

/** Smallest first, so something is ready quickly and a broken setup shows early. `urls` are the
 * deployment's model URLs from /settings/public (null: not offered). */
export function modelPackages(urls: { stt: string | null; tts: string | null }): Record<ModelId, ModelPackage | null> {
  return {
    // Voice activity detection and Smart Turn, used by hands-free voice input (lib/vad.ts).
    vad: {
      id: "vad",
      needsWebGpu: false,
      parts: [
        { baseUrl: VAD_MODEL_URL, cache: MODEL_CACHES.vad, files: allFiles },
        { baseUrl: TURN_MODEL_URL, cache: MODEL_CACHES.turn, files: allFiles },
      ],
    },
    stt: urls.stt ? { id: "stt", needsWebGpu: true, parts: [{ baseUrl: urls.stt, cache: MODEL_CACHES.stt, files: allFiles }] } : null,
    // Without the voices (~6 MB each): the chat loads the one its project uses.
    tts: urls.tts ? { id: "tts", needsWebGpu: false, parts: [{ baseUrl: urls.tts, cache: MODEL_CACHES.tts, files: allFiles }] } : null,
  };
}

async function prefetchPackage(pkg: ModelPackage, onState: (state: PrefetchState) => void, signal: AbortSignal) {
  if (pkg.needsWebGpu && !(await isWebGpuAvailable())) {
    onState({ status: "unsupported" });
    return;
  }
  // The same absolute URLs the workers build, so their cache lookups find these files.
  const parts = await Promise.all(
    pkg.parts.map(async (part) => {
      const baseUrl = new URL(part.baseUrl, window.location.href).href;
      const response = await fetch(new URL("manifest.json", baseUrl), { cache: "no-cache", signal });
      if (!response.ok) throw new Error(`Model manifest not found at ${baseUrl} (HTTP ${response.status})`);
      const files = part.files(await response.json());
      return { baseUrl, files, cache: await openModelCache(part.cache) };
    }),
  );
  if (parts.some((part) => !part.cache)) throw new Error("This browser doesn't allow storing files (private mode?)");

  const totalBytes = parts.reduce((sum, part) => sum + part.files.reduce((s, file) => s + file.bytes, 0), 0);
  let loadedBytes = 0;
  let lastReport = 0;
  onState({ status: "downloading", loadedBytes, totalBytes });
  const onBytes = (n: number) => {
    loadedBytes += n;
    const now = performance.now();
    if (now - lastReport > 200) {
      lastReport = now;
      onState({ status: "downloading", loadedBytes, totalBytes });
    }
  };
  for (const part of parts) {
    await pruneOtherVersions(part.cache!, part.baseUrl);
    for (const file of part.files) {
      await prefetchModelFile(new URL(file.path, part.baseUrl).href, file.bytes, part.cache!, onBytes, signal);
    }
  }
  onState({ status: "ready", totalBytes });
}

/** Downloads every offered model one after another; a failing one doesn't stop the others. */
export async function prefetchModels(
  packages: Record<ModelId, ModelPackage | null>,
  onState: (id: ModelId, state: PrefetchState) => void,
  signal: AbortSignal,
): Promise<void> {
  for (const [id, pkg] of Object.entries(packages) as [ModelId, ModelPackage | null][]) {
    if (!pkg) onState(id, { status: "unavailable" });
  }
  // Asks the browser not to evict the models under storage pressure (see parakeetStt.ts).
  void navigator.storage?.persist?.().catch(() => undefined);
  for (const pkg of Object.values(packages)) {
    if (!pkg || signal.aborted) continue;
    try {
      await prefetchPackage(pkg, (state) => onState(pkg.id, state), signal);
    } catch (error) {
      if (signal.aborted) return;
      console.error(`Model ${pkg.id} could not be downloaded in advance.`, error);
      onState(pkg.id, { status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }
}
