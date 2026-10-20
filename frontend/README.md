# LOLA frontend

React + TypeScript single-page app (SPA) for LOLA: the dashboard where a user manages
projects/avatars, and the public chat page visitors use to talk to an avatar.

See the [root README](../README.md) for how this fits into the rest of the app, and
[CLAUDE.md](../CLAUDE.md) for how code in this repo is commented.

## Stack

- **React 18 + TypeScript**, built and served in dev mode by **Vite**.
- **react-router-dom** — client-side routing between pages.
- **TanStack Query** — fetching and caching data from the backend API. A global error handler
  (`src/main.tsx`) redirects to `/login` whenever any query or mutation reports an expired
  session — not just the initial auth check — so an auth cookie expiring mid-edit (e.g. in the
  Configurator) sends the user back to login instead of showing a generic error forever.
- **three.js** + **[@met4citizen/talkinghead](https://github.com/met4citizen/TalkingHead)** — 3D
  avatar rendering and lip-sync.
- **react-markdown** — renders chat messages that may contain Markdown.

## Folder map

| Path | Contents |
|---|---|
| `src/api/` | Functions that call the backend HTTP API |
| `src/components/` | Reusable UI pieces — small design-system components (`Button`, `Card`, `Table`, ...) and the avatar renderer (`TalkingHeadAvatar`, `Avatar`) |
| `src/hooks/` | Reusable React hooks |
| `src/i18n/` | Localization: English and German strings are found here in json |
| `src/layouts/` | Page layout wrappers (e.g. the dashboard shell) |
| `src/lib/` | Small standalone helper functions |
| `src/pages/` | One folder per route — see [Pages](#pages) below |
| `src/styles/` | Global styles |
| `src/types/` | Shared TypeScript types |
| `src/vendor/` | Third-party code copied in rather than installed: `motion-engine/` (the avatar's gestures, see its `ATTRIBUTION.md`) |
| `src/workers/` | Code that runs off the main thread: on-device speech recognition (`parakeetWorker.ts`, used through `src/lib/parakeetStt.ts`), in-browser speech output for the latency test — Sopro (`soproWorker.ts`, used through `src/lib/soproTts.ts`) and Pocket TTS (`pocketWorker.ts` through `src/lib/pocketTts.ts`; the generation itself is `src/lib/pocketTtsCore.ts`, the model comes from `scripts/fetch-tts-model.sh`) and the microphone capture AudioWorklet |

## Pages

| Route | Folder | What it's for |
|---|---|---|
| `/` | `Landing` | Marketing/info page for logged-out visitors |
| `/login`, `/register`, `/forgot-password`, `/reset-password` | `Login`, `Register`, `ForgotPassword`, `ResetPassword` | Account creation and recovery |
| `/dashboard` | `Dashboard/Overview` | A user's list of projects (avatars) |
| `/dashboard/projects/:id` | `Dashboard/Configurator` | The 5-step wizard for building a project: appearance, technical (AI provider/language), behavior (personality prompt), preview chat, publish. Publishing always saves the current draft first, so there's no separate "save, then publish" step. |
| `/dashboard/api` | `Dashboard/ApiDashboard` | Connect/manage a user's own LLM/TTS provider API keys ("bring your own key") |
| `/dashboard/pronunciation` | `Dashboard/Pronunciation` | A user's TTS word list per language: add/edit terms, preset packs (physics), text/CSV import and export, and a test box that shows the spoken text live and plays it with a chosen TTS key/voice, voice clip or project's settings |
| `/dashboard/analytics` | `Dashboard/Analytics` | Usage stats and per-conversation transcripts for a user's own projects |
| `/dashboard/latency` | `Dashboard/LatencyLab` | Latency test: simulated student conversations (typed, recorded or replayed audio clips, scripted with repeats) against one of the teacher's projects, timing every module and the end-to-end wait per configuration (STT on device/server, LLM, streaming, TTS incl. Sopro in the browser, avatar on/off). Nothing is saved; results export as CSV/JSON. See [docs/latency-test.md](../docs/latency-test.md) |
| `/dashboard/profile` | `Dashboard/Profile` | Account settings: picture, password, logout-everywhere, account deletion |
| `/dashboard/change-password-required` | `Dashboard/ForcePasswordChange` | Forced password reset (e.g. after an admin-issued reset) |
| `/dashboard/admin`, `/dashboard/admin/settings` | `Dashboard/Admin` | Admin-only: manage other users, site-wide settings (e.g. registration on/off) |
| `/dashboard/admin/pages`, `/dashboard/admin/pages/:id` | `Dashboard/Admin/Pages` | Admin-only: edit the landing page texts, imprint and privacy policy per language, create pages such as an accessibility statement, and see who changed what when |
| `/c/:projectSlug` | `PublicChat` | The public chat page visitors use — no login required. Shows a loading screen until the on-device speech recognition model is ready (or has failed, in which case voice input uses the server) |
| `/stt-test` | `SttTest` | Unlinked device test for on-device speech recognition: load time, live-text latency, re-decode times (see [docs/stt-device-test.md](../docs/stt-device-test.md)) |
| `/impressum`, `/datenschutz`, `/credits` | `Imprint`, `Privacy`, `Credits` | Legal/attribution pages (German URLs, matching German legal terminology). Imprint and privacy show an admin's Markdown when there is one (visitor's language, then English), otherwise the built-in template — see `lib/pages.ts` |
| `/p/:slug` | `CustomPage` | Pages an admin created, e.g. an accessibility statement; published ones can be linked in the site footer |

## Debugging

For comparing devices and configurations, use the dashboard's latency test page
(`/dashboard/latency`, see [docs/latency-test.md](../docs/latency-test.md)) — it shows the same
timings per message, in a table. The console log below works on the real public chat.

Open the public chat with `?latencyTest=1` appended to the URL (e.g.
`http://localhost:5173/c/<slug>?latencyTest=1`) to log latency timings to the browser console. The
query parameter only toggles these console logs — chat behavior is identical either way, and without
it the console stays silent.

- **Every message** (typed or spoken, streamed or not) logs a `[Latency-Test] Ping` — the total
  round-trip time from firing the request to the full reply text being ready. This is the quickest
  number to check for "is the server slow right now?".
- **Voice messages only** additionally log a full speech-to-audio breakdown after the avatar's
  spoken reply becomes audible — how long it took from releasing the mic button to hearing the
  reply, broken into STT (speech-to-text) round trip, LLM (large language model) time, TTS
  (text-to-speech) time, audio decode time, and speaking/animation stats. See `logLatency` in
  `src/pages/PublicChat/index.tsx` for what each field means (e.g. `sttBackendMs` is the server's
  Whisper time, or — with on-device recognition — the time from releasing the mic to the final
  text; `sttRoundTripMs` also includes network time).

The backend-side timings that feed these logs (`llmMs`, `ttsMs`, `sttMs`, ...) are documented in
[backend/README.md](../backend/README.md#latency-monitoring).

## Local setup

Step-by-step setup (`npm install`, dev server) now lives in the root README — see
[Deploy A: local development](../README.md#deploy-a-local-development). Start the backend first
(it must be running at `http://localhost:8000` for the dev server's `/api/*` proxy to work — see
`vite.config.ts`).

Other scripts:
```bash
npm run build     # type-checks and builds a production bundle into dist/
npm run preview   # serves the production build locally
```

## Version and documentation link

The footer of the public pages (`components/SiteFooter`) shows the build version
(`package.json` version plus the short commit, e.g. `0.1.0 (a1b2c3d)`) and a documentation link.

- `APP_COMMIT` (build-time env / Docker build arg): the commit shown in the footer. The release
  workflow passes `github.sha`; local builds fall back to `git rev-parse`.
- `VITE_DOCS_URL` (build-time env / Docker build arg): URL of the documentation. While it is unset
  the footer shows "Documentation (coming soon)" instead of a link.
