# Deploying LOLA

Everything needed to run LOLA in production with Docker: images, a Compose file, the
reverse-proxy config, and a systemd unit. Works on any Docker host — the backend container's app
user adapts to whatever UID/GID owns your data directory via `PUID`/`PGID` (see
[Deploying](#deploying) below). The defaults for both happen to match **TrueNAS SCALE**'s built-in
"apps" user, so a TrueNAS deployment needs no extra configuration, but nothing here is TrueNAS-only.

See the [root README](../README.md) for what the app does and for the shared `.env` setup, and
[CLAUDE.md](../CLAUDE.md) for how code in this repo is commented.

## What's here

| File | Purpose |
|---|---|
| `backend.Dockerfile` / `frontend.Dockerfile` | Build the backend and frontend images. Both run as a non-root user; the backend's adapts its UID/GID at container start to match the bind-mounted data directory (see below), the frontend's is a fixed user since it has no data directory to adapt to |
| `local-tts.Dockerfile` + `local-tts-entrypoint.sh` | Image for the optional local text-to-speech service (see [Local text-to-speech](#local-text-to-speech-optional)) |
| `backend-entrypoint.sh` | Container startup: creates upload/cache folders, runs database migrations, then starts the API |
| `Caddyfile` | Reverse-proxy config — routes `/api/*` to the backend, serves the speech recognition model under `/models/*` (pre-gzipped, from the data directory) and the frontend's static files otherwise (with SPA fallback so client-side routes work on page reload); also sets baseline security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS) on every response |
| `docker-compose.yml` | The stack: `web` (frontend + Caddy), `backend` (the API), `stt-model` (one-off: downloads the speech recognition model), and the optional `tts-local` (local text-to-speech) |
| `lola.service` | systemd unit to start/stop the stack on boot |

The `.env` file itself lives one level up, at the repo root — see [Deploying](#deploying) below.

## How it fits together

`docker-compose.yml` **pulls** prebuilt images (`ghcr.io/c-siegel/lola:frontend` /
`ghcr.io/c-siegel/lola:backend`) rather than building them itself — the two Dockerfiles are used in
a separate build/publish step. The `web` container runs Caddy, which serves the built frontend
and reverse-proxies API calls to the `backend` container. Caddy itself only speaks plain HTTP
(`auto_https off`); TLS is expected to be terminated in front of it (e.g. a Cloudflare Tunnel).

Both services read their configuration from `../.env` (relative to this folder) via
`env_file: ../.env` — the same file used by [Deploy A](../README.md#deploy-a-local-development).
This assumes the repo is checked out on the host and Compose is run from within it (e.g. via
`lola.service`), not e.g. pasted into a UI that only takes the YAML without the surrounding
repo.

## Health checks

Both containers have a Docker healthcheck (`docker-compose.yml`): `backend` polls its own
`/health` endpoint directly (via Python's `urllib`, since the slim base image has no curl/wget);
`web` checks Caddy's own static file serving, not the backend — a backend outage shouldn't make an
orchestrator restart Caddy, which wouldn't fix it. `web`'s `depends_on` waits for `backend`'s
healthcheck to pass, not just for its container to have started.

The backend's `/health` is reachable two ways behind Caddy: `/api/v1/health` (the general `/api/*`
route; every backend route lives under `/api/v1`) and the bare `/health` path (a dedicated route in `Caddyfile`, for whatever external load
balancer or uptime monitor tries the more obvious URL first). Both hit the same endpoint.

## Deploying

This assumes the repo is checked out on the host (e.g. at `/opt/lola`) — see the root
[README](../README.md#deployment) for the shared `.env` setup (`cp .env.example .env` at the repo
root, then fill in the required secrets).

1. Nothing to do for the on-device speech recognition model: the one-off `stt-model` service in
   `docker-compose.yml` downloads it into `<LOLA_DATA_DIR>/models` on every `up` and then
   exits. The first run fetches ~380 MB from Hugging Face (a pinned revision, every file checked
   against its SHA-256 hash) and stores a gzipped copy next to each file, which Caddy serves to
   browsers (`/models/*` in `Caddyfile`): ~175 MB per device instead of ~380 MB. Later runs only
   re-download missing or damaged files and finish in seconds. The site doesn't wait for it —
   until the download is complete, browsers use server transcription. Follow its progress with
   `docker compose -f docker/docker-compose.yml --env-file .env logs stt-model`. To skip on-device
   recognition entirely, set `BROWSER_STT_ENABLED=false`.

   After the first run, check that compression actually reaches browsers — this also catches a
   proxy in front that strips it:
   ```bash
   curl -sI -H 'Accept-Encoding: gzip' https://<your-site>/models/parakeet-redux/v1/encoder-model.onnx \
     | grep -i -E 'content-encoding|content-length'
   # expect: content-encoding: gzip, content-length around 151000000
   ```

   Behind Cloudflare, also let Cloudflare cache the model. It doesn't by default: `.onnx` isn't
   on its list of cacheable file extensions, so every device's ~175 MB comes through the tunnel
   from your own server, and a whole class downloading at once is limited by that server's
   upload speed. In the Cloudflare dashboard, add a **Cache Rule**: *URI Path starts with
   `/models/`* → *Eligible for cache*, edge TTL *Use cache-control header if present* (Caddy
   already marks these files immutable for a year). Every file stays below Cloudflare's 512 MB
   per-file cache limit. Check it by running the `curl` above twice, with `cf-cache-status` added
   to the `grep` pattern: the second response should say `cf-cache-status: HIT`.
2. Start the stack from the repo root:
   ```bash
   docker compose -f docker/docker-compose.yml --env-file .env up -d
   ```
   The `web` service listens on port `30080` by default (see `docker-compose.yml`).

   **`--env-file .env` is required, not optional.** Without it, Compose falls back to looking for a
   `.env` file next to the compose file itself (`docker/.env`) for `${VAR}` substitution — which
   doesn't exist, since the real one lives at the repo root. `LOLA_DATA_DIR` would silently
   resolve to an empty string, turning the volume mount into `- :/data` and breaking the container
   at startup. (`lola.service` already passes the equivalent absolute-path flag.)
3. Optional — run it as a systemd service so it survives reboots: install `lola.service`
   (it expects the repo checked out at `/opt/lola` — adjust `WorkingDirectory` and the
   paths in `ExecStart`/`ExecStop` if you use a different location) and enable it with
   `systemctl enable --now lola`.

For a real (non-`localhost`) deployment, also set `SITE_ADDRESS`, `CORS_ORIGINS`,
`FRONTEND_BASE_URL`, and `LOLA_DATA_DIR` in the root `.env` — see `.env.example` for what
each one does.

To get your first admin account (dashboard access to manage other accounts and site settings),
set `ADMIN_EMAIL`/`ADMIN_PASSWORD` in the root `.env` before the first start — `backend-
entrypoint.sh` creates or promotes that account on every container start (a no-op once it's
already an admin), so the values are safe to leave in `.env` indefinitely.

**On a non-TrueNAS host**, also set `PUID`/`PGID` in `.env` to the UID/GID that should own
`LOLA_DATA_DIR` (e.g. your own user's `id -u`/`id -g`) — otherwise the backend container
defaults to UID/GID 568 (TrueNAS's built-in "apps" user) and won't have write access to a data
directory owned by anyone else. The backend's entrypoint adjusts its in-container user to match at
every start (see `backend-entrypoint.sh`), so the image itself doesn't need rebuilding for a
different host. The `web` and `stt-model` containers run as the same UID/GID, so they can read and
write the speech recognition model in `<LOLA_DATA_DIR>/models` even where only that
user/group has access (as on a TrueNAS dataset).

## Local text-to-speech (optional)

The `tts-local` service runs the local text-to-speech engine, Kyutai's Pocket TTS (see
[local-tts/](../local-tts/)): speech output for projects without a cloud TTS key, with preset
voices and — given a Hugging Face token — voices cloned from teachers' own clips (Dashboard →
Voices). It's off unless you switch it on, because it needs ~0.7 GB of RAM per language and keeps
CPU cores busy while it speaks. To enable it, add to the root `.env`:

```bash
COMPOSE_PROFILES=local-tts   # makes `docker compose up` also start the tts-local service
LOCAL_TTS_ENABLED=true       # makes the backend use it
# Optional:
TTS_LANGUAGES=de,en          # which Pocket TTS models to load (de en fr it es pt nl)
TTS_HF_TOKEN=hf_...          # voice cloning, see local-tts/README.md#voice-cloning
```

and run the usual `docker compose -f docker/docker-compose.yml --env-file .env up -d`. Its data
lives in `<LOLA_DATA_DIR>/local-tts/`: the downloaded models (~220 MB per language, fetched on
the first start) and a cache of teachers' clips and cloned voices. The models load in the
background right after the container starts (about 20 s per language); after that, a sentence
starts within a fraction of a second. An older `voices/` folder there (default clips for the
previous engine, Sopro) is no longer read and can be deleted.

## On-device speech output (optional)

Projects can let the visitor's own device speak the replies (Kyutai's Pocket TTS in the browser,
German only); the project's server TTS stays the fallback for devices that can't load the model.
Each device downloads the model once (~99 MB). To offer it, add to the root `.env`:

```bash
COMPOSE_PROFILES=browser-tts   # or e.g. local-tts,browser-tts — starts the tts-model service
BROWSER_TTS_ENABLED=true       # lets teachers switch it on per project in the configurator
```

The one-off `tts-model` service downloads the model into `<LOLA_DATA_DIR>/models/pocket-tts-de/`
(~270 MB with all 23 preset voices, plus gzip copies, checksum-verified, with the CC BY 4.0
`NOTICE.md`), then exits; like `stt-model`, it only downloads what's missing on later starts. Each
device fetches only the model and its project's voice.

## Knowledge bases (optional)

The `rag` service runs the knowledge service (see [rag/](../rag/)): teachers upload documents
(Dashboard → Knowledge), and projects answer from them with any LLM provider. Off unless you
switch it on. To enable it, add to the root `.env`:

```bash
COMPOSE_PROFILES=rag            # or e.g. local-tts,rag — starts the rag service
RAG_ENABLED=true                # makes the backend use it
RAG_SERVICE_TOKEN=<random>      # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Both containers read the same `RAG_SERVICE_TOKEN` from `.env`. The service publishes no port —
only the backend reaches it, on the Compose network. It runs as `PUID:PGID` with a read-only root
filesystem and no capabilities, because it parses untrusted uploads (each file additionally in a
resource-limited subprocess). Its data lives in `<LOLA_DATA_DIR>/rag/`: the index
(`rag.db`), in-flight uploads (deleted once parsed) and the local embedding model (about 600 MB,
downloaded from Hugging Face on the first upload). Plan on about 1–2 GB of RAM for it.

Upload limits (file size, pages, documents, storage per teacher, uploads per 10 minutes) are site
settings an admin changes under Admin → Settings. The ceilings they can't exceed are env settings
of the `rag` service (`RAG_HARD_MAX_UPLOAD_MB` etc., see [rag/README.md](../rag/README.md#settings)).

**Docling (optional, better parsing).** For tables, multi-column layouts and scanned PDFs (text
recognition), add the `docling` profile and point the knowledge service at it:

```bash
COMPOSE_PROFILES=rag,docling
DOCLING_URL=http://docling:5001
```

Teachers then get a "Docling" choice when uploading. The image is large (several GB, includes
PyTorch) and needs a few GB of RAM. Check that the default image tag in `docker-compose.yml`
exists ([releases](https://github.com/docling-project/docling-serve/releases)) or set
`DOCLING_SERVE_IMAGE`. Docling's code is MIT-licensed and its models Apache-2.0 /
CDLA-Permissive-2.0.

**Quality evaluation (optional, Ragas).** Lets teachers measure how well a project answers from
its material (Dashboard → Answer quality: test questions and runs). Add the `rag-eval` profile
next to `rag` and switch it on in the backend:

```bash
COMPOSE_PROFILES=rag,rag-eval
RAG_EVALUATION_ENABLED=true
```

The `rag-eval` service gets only `RAG_SERVICE_TOKEN` from `.env`, not the other secrets; the judge
LLM's key (one of the teacher's own) arrives with each request. It runs like `rag` (no port,
read-only root filesystem, no capabilities, about 1 GB of RAM) and keeps only cached prompt
translations in `<LOLA_DATA_DIR>/rag-eval/`. Its Ragas telemetry is switched off. Runs cost
the teacher judge calls on their own key; admins cap the questions per run under Admin → Settings.

## Deploying behind another reverse proxy

Some hosts (e.g. a university's own infrastructure) put another reverse proxy — commonly nginx —
in front of everything, terminating TLS there instead of via a Cloudflare Tunnel. `docker/Caddyfile`
needs no changes for this: `auto_https off` already means Caddy expects TLS to be terminated by
whatever sits in front of it, and it doesn't care whether that's cloudflared or an institutional
nginx.

Point the outer proxy at the `web` container's exposed port (`30080` by default) and configure it
to:

- **Preserve the `Host` header** — Caddy's site block only matches requests whose `Host` header
  equals `SITE_ADDRESS`. Many reverse proxies default to sending their own upstream address as
  `Host` instead of forwarding the original one, which makes Caddy reject the request.
- **Forward `X-Forwarded-For` and `X-Forwarded-Proto`** — otherwise the backend can't see the real
  visitor address (see `FORWARDED_ALLOW_IPS` below) or scheme.
- **Pass compressed model files through unchanged** — `/models/*` responses are already gzipped
  (see step 1 under [Deploying](#deploying)); a proxy that decompresses or re-encodes them sends
  every student ~380 MB instead of ~175 MB.
- **Not buffer the streamed chat reply** — `backend/app/features/chat/public_router.py`'s chat endpoint streams
  its reply as `text/event-stream` so a student hears the first words as soon as they're ready.
  A proxy that buffers the whole response before forwarding it defeats that.

An nginx `location` block covering all of these:

```nginx
location / {
    proxy_pass http://<docker-host>:30080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

(No WebSocket `Upgrade`/`Connection` headers needed — the app only uses SSE for streaming, not
WebSockets.)

Also add this outer proxy's own address to `FORWARDED_ALLOW_IPS` in `.env` — see the comment next
to it in `.env.example` for why: it's now an extra hop that appends itself to `X-Forwarded-For`,
and every hop in that chain has to be trusted or the real visitor address is lost.

### Serving under a sub-path

If the outer proxy gives LOLA only a path on a shared host (e.g.
`https://host.example/lola/`) instead of a domain of its own, set in the root `.env`:

```bash
BASE_PATH=/lola
FRONTEND_BASE_URL=https://host.example/lola   # links in emails
CORS_ORIGINS=https://host.example             # an origin has no path
SITE_ADDRESS=:80                              # if the proxy forwards the shared host's name
```

The same images work at the root and under any prefix, nothing is rebuilt:

- **Caddy** strips the prefix before routing (see `Caddyfile`), so it doesn't matter whether the
  outer proxy forwards `/lola/…` or strips it itself.
- **The frontend** is built with relative asset URLs, and Caddy writes the prefix into
  `index.html`'s `<base href>` per request (`templates` in `Caddyfile`, see
  `frontend/vite.config.ts`). The app reads it from there for its routes, API calls and static
  files (`frontend/src/lib/basePath.ts`).
- **The backend** keeps its routes at `/api/v1` but puts the prefix into every URL it hands out
  (`backend/app/core/urls.py`) and limits its cookies to that path, so other apps on the same
  host don't receive the login token.
