// @met4citizen/headaudio ships no TypeScript types (plain .mjs) — minimal ambient declaration
// covering only what components/TalkingHeadAvatar/headAudioIntegration.ts uses.
declare module "@met4citizen/headaudio/modules/headaudio.mjs" {
  export class HeadAudio extends AudioWorkletNode {
    constructor(context: BaseAudioContext, options?: AudioWorkletNodeOptions);
    loadModel(url: string): Promise<void>;
    update(dt?: number): void;
    onvalue: (key: string, value: number) => void;
  }
}
