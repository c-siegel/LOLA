import type { TalkingHead } from "@met4citizen/talkinghead";
// The worklet runs in its own AudioWorkletGlobalScope, loaded by URL, so it can't go through the
// module graph like the node class below. The package's prebuilt dist/ file is self-contained (the
// modules/ version imports its siblings), so Vite can ship it as a plain asset.
import headWorkletUrl from "@met4citizen/headaudio/dist/headworklet.min.mjs?url";
import modelUrl from "@met4citizen/headaudio/dist/model-en-mixed.bin?url";

// HeadAudio (npm: @met4citizen/headaudio, MIT) computes lipsync visemes in real time directly from
// the audio being played — independent of language/TTS provider, without word timestamps.
// Replaces TalkingHead's own text-based viseme path (which would need word timing that
// litellm.speech() doesn't provide). Setup matches the official integration example from the
// HeadAudio docs 1:1.
//
// Known limitation: the only pretrained model (model-en-mixed.bin) was trained on English voices
// only — unverified for German.
export async function attachHeadAudio(head: TalkingHead): Promise<void> {
  await head.audioCtx.audioWorklet.addModule(headWorkletUrl);
  // Dynamic, so the node class only loads for avatars that actually speak.
  const { HeadAudio } = await import("@met4citizen/headaudio/modules/headaudio.mjs");
  const headaudio = new HeadAudio(head.audioCtx, {
    parameterData: { vadGateActiveDb: -40, vadGateInactiveDb: -60 },
  });
  await headaudio.loadModel(modelUrl);
  head.audioSpeechGainNode.connect(headaudio);
  headaudio.onvalue = (key, value) => {
    Object.assign(head.mtAvatar[key], { newvalue: value, needsUpdate: true });
  };
  head.opt.update = headaudio.update.bind(headaudio);
}
