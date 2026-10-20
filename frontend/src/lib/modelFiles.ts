// Downloading self-hosted model files (the ones scripts/fetch-*-model.sh put under /models/) inside
// a worker: progress, a stall timeout, and a Cache API copy so later visits load from disk. Shared
// by the speech recognition worker (workers/parakeetWorker.ts) and the Pocket TTS worker.

// A download that receives nothing for this long counts as failed, so the caller can fall back
// instead of keeping the page on a loading screen indefinitely (e.g. a truncated file on the
// server, or a connection that silently hangs).
const STALL_TIMEOUT_MS = 30_000;

export interface ManifestFile {
  path: string;
  bytes: number;
}

/** Downloads one model file into a buffer of its known size, reporting bytes as they arrive.
 * Served from the Cache API on every visit after the first. */
export async function loadModelFile(url: string, bytes: number, cache: Cache | null, onBytes: (n: number) => void) {
  const cached = await cache?.match(url);
  if (cached) {
    const buffer = new Uint8Array(await cached.arrayBuffer());
    if (buffer.byteLength === bytes) {
      onBytes(bytes);
      return buffer;
    }
  }
  const abort = new AbortController();
  let stallTimer = setTimeout(() => abort.abort(), STALL_TIMEOUT_MS);
  const resetStallTimer = () => {
    clearTimeout(stallTimer);
    stallTimer = setTimeout(() => abort.abort(), STALL_TIMEOUT_MS);
  };
  try {
    // no-store: the Cache API below already keeps a copy — letting the HTTP cache keep a second
    // one would double the disk space a model of hundreds of MB takes on the student's device.
    const response = await fetch(url, { cache: "no-store", signal: abort.signal });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status} for ${url}`);
    // tee() lets the Cache API write the file to disk while we read it, instead of holding a
    // second full copy in memory (Parakeet's encoder alone is 344 MB — iPads kill tabs for less).
    const [ours, forCache] = cache ? response.body.tee() : [response.body, null];
    const cacheWrite = forCache ? cache!.put(url, new Response(forCache)).catch(() => undefined) : null;

    const buffer = new Uint8Array(bytes);
    let offset = 0;
    const reader = ours.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      resetStallTimer();
      if (offset + value.byteLength > bytes) throw new Error(`${url} is larger than the manifest says`);
      buffer.set(value, offset);
      offset += value.byteLength;
      onBytes(value.byteLength);
    }
    if (offset !== bytes) throw new Error(`${url} is incomplete (${offset} of ${bytes} bytes)`);
    await cacheWrite;
    return buffer;
  } catch (error) {
    // A cut-off download can still have been stored in full by the Cache API (its stream ended
    // normally, just too early) — remove it so the next visit doesn't trip over it again.
    await cache?.delete(url).catch(() => undefined);
    if (abort.signal.aborted) throw new Error(`Download of ${url} stalled for ${STALL_TIMEOUT_MS / 1000} s`);
    throw error;
  } finally {
    clearTimeout(stallTimer);
  }
}

export async function openModelCache(name: string): Promise<Cache | null> {
  try {
    return await caches.open(name);
  } catch {
    return null; // e.g. private browsing — still works, just downloads again next visit
  }
}

/** Drops files from an older model version, so a version bump doesn't leave the old files behind. */
export async function pruneModelCache(cache: Cache | null, currentUrls: Iterable<string>): Promise<void> {
  if (!cache) return;
  const current = new Set(currentUrls);
  for (const request of await cache.keys()) {
    if (!current.has(request.url)) await cache.delete(request);
  }
}
