// What the latency test page (pages/Dashboard/LatencyLab) needs from an engine that speaks on this
// device instead of the server, so it can compare several (Sopro, Pocket TTS) without knowing which.

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
  ): Promise<BrowserSynthesis>;
}
