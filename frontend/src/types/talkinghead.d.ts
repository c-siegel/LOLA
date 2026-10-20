// @met4citizen/talkinghead ships no TypeScript types (plain .mjs) — minimal ambient
// declaration covering only what TalkingHeadAvatar.tsx actually uses.
declare module "@met4citizen/talkinghead" {
  export interface TalkingHeadAvatarOptions {
    url: string;
    body?: "M" | "F";
    avatarMood?: string;
    lipsyncLang?: string;
    [key: string]: unknown;
  }

  export class TalkingHead {
    constructor(node: HTMLElement, options?: Record<string, unknown>);
    showAvatar(avatar: TalkingHeadAvatarOptions, onprogress?: (event: ProgressEvent) => void): Promise<void>;
    start(): void;
    stop(): void;
    // Unlike stop() (which only pauses the render loop and suspends audioCtx), this actually
    // releases the WebGL context (via WEBGL_lose_context) and disposes the Three.js renderer — see
    // TalkingHeadAvatar.tsx's unmount cleanup for why that distinction matters.
    dispose(): void;
    // For the HeadAudio integration (components/TalkingHeadAvatar/headAudioIntegration.ts):
    // audioCtx/audioSpeechGainNode are public properties that TalkingHead manages itself.
    audioCtx: AudioContext;
    audioSpeechGainNode: AudioNode;
    mtAvatar: Record<string, { newvalue: number; needsUpdate: boolean }>;
    opt: { update?: (dt: number) => void; [key: string]: unknown };
    speakAudio(r: { audio: AudioBuffer }, opt?: Record<string, unknown>): void;
    // Where TalkingHead's own playAudio() connects speech; it feeds audioSpeechGainNode, so audio
    // played into it also reaches HeadAudio. Used by queueBuffer() in TalkingHeadAvatar.tsx, which
    // also sets stateName and calls speakWithHands() the way startSpeaking() does.
    audioAnalyzerNode: AnalyserNode;
    stateName: string;
    // Every sound passes through this convolver; setReverb(null) loads a dry impulse. See the
    // constructor call in TalkingHeadAvatar.tsx for why `normalize` is switched off.
    audioReverbNode: ConvolverNode;
    setReverb(url?: string | null): Promise<void>;
    speakWithHands(delay?: number, prob?: number): void;
    // Stops the currently playing audio source and clears the queued speech/animation backlog.
    stopSpeaking(): void;
    isSpeaking: boolean;
    isListening: boolean;
    // Not a fixed literal union: the library throws at runtime on an unrecognized mood name (one
    // of "neutral"/"happy"/"angry"/"sad"/"fear"/"disgust"/"love"/"sleep"), so callers must already
    // pass from a known list — this stays a plain string to keep the declaration honest.
    setMood(mood: string): void;
    // x/y null = look toward the camera's own eye-projection point instead of a fixed screen
    // coordinate. Self-clearing: the queued look expires after `t` ms with no revert call needed.
    lookAt(x: number | null, y: number | null, t: number): void;
    lookAtCamera(t: number): void;
    // For components/TalkingHeadAvatar's mic-listening cue — analyzer comes from the same
    // MediaStream already used for recording, see startListening() in TalkingHeadAvatar.tsx.
    startListening(
      analyzer: AnalyserNode,
      opt?: Record<string, unknown>,
      onchange?: ((volume: number, active: boolean) => void) | null,
    ): void;
    stopListening(): void;
  }
}
