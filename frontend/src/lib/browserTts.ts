// What the public chat and the latency test page need from an engine that speaks on this device
// instead of the server, so they stay independent of the engine (Pocket TTS today).

export interface BrowserSynthesis {
  /** From the request until the first audio samples arrived. */
  firstAudioMs: number;
  /** Generation time inside the worker. */
  synthMs: number;
  audioSeconds: number;
}

export interface BrowserTts {
  /** Model profile in use (e.g. "webgpu-fp16", "wasm-int8"); null until loaded. */
  readonly backend: string | null;
  /** Speaks `text`, handing each piece of audio to `onAudio` as soon as it's generated. */
  synthesize(
    text: string,
    language: string,
    onAudio: (samples: Float32Array<ArrayBuffer>, sampleRate: number) => void,
    /** Stops generating early (the promise still resolves). */
    signal?: AbortSignal,
  ): Promise<BrowserSynthesis>;
}
