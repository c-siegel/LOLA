# Avatar, gestures and conversation plan

## 3D avatar and lip-sync

Two MIT-licensed libraries by Mika Suominen:

- **[TalkingHead](https://github.com/met4citizen/TalkingHead)** (`@met4citizen/talkinghead`) renders and animates the avatar (`frontend/src/components/TalkingHeadAvatar/`). Two models, Julia and David, ship in `frontend/public/avatars/` and are offered in every project's configurator; license details in that folder's `ATTRIBUTION.md`. TalkingHead's README explains how to prepare new compatible models.
- **[HeadAudio](https://github.com/met4citizen/HeadAudio)** (`@met4citizen/headaudio`) computes lip-sync visemes from the played audio in real time instead of estimating from text. Its pretrained model was trained on English voices; quality for German audio is unverified.

## Gestures

Gestures come from **[MotionEngine](https://github.com/lhupyn/motion-engine)** (MIT, by lhupyn), a TalkingHead plugin, vendored without its webcam feature into `frontend/src/vendor/motion-engine/` (see `ATTRIBUTION.md` there).

1. With a project's "Gestures and body language" setting on, the system prompt lists ten gestures.
2. The model marks one where it fits by writing `::name::` into its reply, e.g. `::nod_yes:: Exactly!`.
3. The backend ([`motion.py`](../backend/app/features/chat/motion.py)) cuts the markers out before anything is spoken, shown or saved, and sends the gesture names with each streamed sentence.
4. The browser plays them when that sentence starts.

Saved conversations keep which gesture was used where: under the reply in the Analytics transcript and in a "Gesten" column of the CSV export (`name@position`). The setting has no effect with the "Chat only" layout.

**Thinking gestures** (Appearance → Advanced settings, off by default): while the reply is being generated, the avatar plays random `thinking_face`, `raise_eyebrows` or `shrug_confused` gestures, and stops when the reply begins. It needs the gestures setting, runs entirely in the browser (`startThinking` in `frontend/src/components/TalkingHeadAvatar`); the backend only stores the `thinking_motions_enabled` flag.

## Conversation plan

Behavior → Conversation plan, off by default. A list of steps, each with a title and an optional note for the model (e.g. what counts as achieved), worked through in a fixed or any order. The model ticks a step off with `::done_N::` (N = the step's number) and takes it back with `::undone_N::`. [`checklist.py`](../backend/app/features/chat/checklist.py) and `motion.py` cut these markers out in the same pass as the gestures.

- **No server-side state.** Every reply returns its ticks by step ID (per streamed sentence, so a step is crossed off while it is spoken). The browser keeps them on the message, sends them back with the history, and each turn replays them. Editing or regenerating a message rolls the progress back on its own.
- **Prompt.** The model only sees the last 20 messages, which carry no markers, so the plan goes into the system prompt on every turn with a one-line status (`Status: done 1 · next 2`). Finished steps drop their note, so a long plan gets cheaper over time.
- **Stable IDs.** Step IDs survive reordering, so running chats keep pointing at the right step.
- **Student view.** "Show the plan to students" adds a collapsible progress card: on the avatar stage, or above the thread in the "Chat only" layout. Notes are never sent to the public page, but they are in the system prompt: a determined student could talk the model into revealing one. Don't put answers that must stay secret in notes.
- **Analytics.** Ticks appear under the reply in the transcript and in a "Plan" column of the CSV export. Unlike gestures, the plan also works in the "Chat only" layout.

## How a message becomes speech and gestures

See the pipeline diagram in the [main README](../README.md#how-a-chat-message-is-processed).
