# AI Video Production Hub

A self-hosted web app that turns **prompt packs** or **narration scripts** into finished short-form videos by chaining AI image models, AI video models and FFmpeg — with a job queue you can pause, resume, cancel and partially re-run.

Built for one operator (or a small team) producing AI video content for social channels. You bring your own provider API keys; nothing is sent anywhere else.

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup: password and API keys](#setup-password-and-api-keys)
- [Quick start (Docker Compose)](#quick-start-docker-compose)
- [Deploy on Coolify](#deploy-on-coolify)
- [Deploy with plain Docker](#deploy-with-plain-docker)
- [Local development](#local-development)
- [Configuration reference](#configuration-reference)
- [Writing prompt packs](#writing-prompt-packs)
- [Data, backups and upgrades](#data-backups-and-upgrades)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Optional: clone a channel's style](#optional-clone-a-channels-style)
- [License](#license)

---

## What it does

**Prompt-pack pipelines** — paste (or drop a `.txt` / `.md` / `.json` file with) a list of image and video prompts. The app detects the shape of the pack and picks a video system:

| Pack shape | Mode | What happens |
|---|---|---|
| N images + N−1 videos | Frame · chain | Clip *i* animates from image *i* to image *i+1* (first/last frame) |
| N images + N videos | Frame · chain + hero reveal | As above, plus a final clip on the last image |
| 2N images + N videos | Frame · paired bookends | Each clip has its own explicit first and last frame |
| 1 image + ≥1 videos | Extend | One clip is generated from the image on Veo 3.1, then extended clip by clip (SnapGen Veo extend) |

Every clip is rendered on **SnapGen**. You pick the video model, and the form only offers the resolutions, clip lengths and aspect ratios that model accepts:

| Video model | Images used | Aspect | Resolution | Clip length | SnapGen credits / clip | Extend packs |
|---|---|---|---|---|---|---|
| Veo 3.1 Fast (default) | exact first/last frame | 16:9, 9:16 | 720p, 1080p | 4, 6, 8 s | 4 | yes |
| Veo 3.1 Lite | exact first/last frame | 16:9, 9:16 | 720p, 1080p | 4, 6, 8 s | 4 | yes |
| Veo 3.1 | exact first/last frame | 16:9, 9:16 | 720p, 1080p | 4, 6, 8 s | 100 | yes |
| Omni Flash | references (not exact frames) | 16:9, 9:16 | 720p, 1080p | 4, 6, 8, 10 s | 13 | no |
| Vela AI (experimental) | first image only | 16:9, 9:16, 1:1 | — | 5 s | free (daily limit) | no |

Vela AI is not in SnapGen's public API docs; the app calls the endpoint SnapGen's own web app uses, so SnapGen may reject it — use a Veo model if it does. If SnapGen fails a clip after its retries, the run fails (there is no other video provider).

Images are generated **sequentially**, each using the previous image as a reference, so the scene stays consistent (same camera, same room, same character). You pick one **image model**, grouped by provider:

| Provider | Image models | Credits per image (as listed by SnapGen — may change) |
|---|---|---|
| SnapGen | Nano Banana 2 (default), Nano Banana Pro, Nano Banana 2 Lite | free (daily limit) |
| SnapGen | Grok Image (speed mode) · GPT Image 2 (low, 1K) | 4 · 3 |
| Kie.ai | Nano Banana 2 | billed by Kie.ai |
| fal.ai | Nano Banana 2 / Nano Banana / Nano Banana Pro, FLUX.1 Kontext [pro], Seedream 4.0 | USD from fal's pricing API |

If the chosen model fails, the next configured provider is tried along the ladder **SnapGen → Kie.ai → fal.ai** (wrapping round, providers without an API key are skipped), each with its default model (Nano Banana 2). Grok Image and GPT Image 2 take the previous image by its SnapGen id, or as an uploaded file when another provider made it. Clips are normalised and joined with FFmpeg.

**Script Studio** — paste a narration script and get a finished video:

1. Text-to-speech narration (ElevenLabs voices via fal.ai, with Kie.ai as backup)
2. An LLM (any model on OpenRouter) splits the script into scenes and writes every scene's image prompt in one call
3. One image per scene (any image model above — falling back along the same ladder if the chosen model fails), animated with slow pan/zoom (Ken Burns)
4. Karaoke-style subtitles — the spoken word highlights in sync
5. Optional watermark logo (background removed automatically)
6. Final mux to MP4

**Around both:**

- Queue with live progress, per-step timeline and logs
- Pause, resume, cancel and retry; retries resume from where the run stopped instead of paying for finished work again — and can switch models (image, video, LLM, voice) for the work that's left
- **Regenerate a single image or clip** you don't like, optionally on a different model — only that item and the clips that depend on it are re-rendered
- Each image and clip shows the model that made it and the credits the provider reported; the run page totals them, and the settings panel shows your SnapGen and Kie.ai credit balances
- Generation counts, SnapGen's published video credits, and a USD estimate where the provider publishes prices (fal.ai) — shown before you launch
- A setup check that tells you exactly which API keys are missing before you can launch
- Browser notification when a run finishes
- Finished videos are either uploaded to **NextCloud** (public share link) or kept on the server and played/downloaded in the app
- Optional mirroring of runs into **NocoDB** tables

## How it works

```
Browser ──► nginx (:80) ──► FastAPI (:8001, loopback only) ──► SQLite (/app/data)
                                   │
                                   ├── SnapGen     images · video (Veo 3.1, Omni Flash, Vela AI)
                                   ├── OpenRouter  LLM (Script Studio)
                                   ├── fal.ai      images · ElevenLabs TTS
                                   ├── Kie.ai      image + TTS fallback
                                   ├── FFmpeg      normalise · concat · subtitles · watermark
                                   ├── NextCloud   (optional) upload + share link
                                   └── NocoDB      (optional) run records
```

Everything ships as **one Docker image**: nginx serves the React UI and proxies `/api` to the FastAPI backend; supervisord runs both. Pipelines run as background tasks inside the backend process (one at a time; others queue).

## Requirements

**To run it:** Docker (or Coolify, which uses Docker). ~2 GB RAM is comfortable; FFmpeg and the logo background-removal model are the heavy parts.

**Accounts:** the app ships with **no keys and no default password**. Whoever installs it creates their own provider accounts and enters their own keys — see [Setup](#setup-password-and-api-keys).

## Setup: password and API keys

The app will not start without a password, and each feature stays locked until its provider keys are set. Nothing is shared between installs — every key below is yours.

### Step 1 — choose your password

Pick a long random password. This is what you type on the sign-in screen. Also generate a session secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Step 2 — create provider accounts and API keys

| Provider | Get a key | Env variable | Needed for |
|---|---|---|---|
| [SnapGen](https://snapgen.ai) (formerly GeminiGen) | Sign in → API keys in your account dashboard ([docs](https://docs.snapgen.ai)) | `SNAPGEN_API_KEY` | **Prompt packs (required)** — all video and the first image provider in the fallback ladder |
| [OpenRouter](https://openrouter.ai) | [openrouter.ai/keys](https://openrouter.ai/keys) | `OPENROUTER_API_KEY` | **Script Studio (required)** — scene splitting and image prompts |
| [fal.ai](https://fal.ai) | [fal.ai/dashboard/keys](https://fal.ai/dashboard/keys) | `FAL_KEY` | **Script Studio narration (required unless Kie.ai is set)**, scene images, last image fallback |
| [Kie.ai](https://kie.ai) | [kie.ai/api-key](https://kie.ai/api-key) | `KIE_API_KEY` | Recommended — image fallback and backup narration |

**Minimum sets:**

- **Prompt packs only:** `SNAPGEN_API_KEY`
- **Script Studio only:** `OPENROUTER_API_KEY` + `FAL_KEY` (or `KIE_API_KEY` for narration and images)
- **Everything, with fallbacks:** all four

All four are paid, credit-based services — add credit in each dashboard. You are responsible for your usage and for following each provider's terms.

### Step 3 — put them in the environment

- **Docker Compose / plain Docker:** copy `.env.example` to `.env` and fill in the values. `.env` is git-ignored.
- **Coolify:** paste the same `NAME=value` lines into the app's **Environment Variables** tab ([step 3 below](#deploy-on-coolify)).
- **Local development:** `.env` in the project root.

```env
APP_PASSWORD=your-long-random-password
SESSION_SECRET=paste-the-generated-secret
SNAPGEN_API_KEY=...
OPENROUTER_API_KEY=...
FAL_KEY=...
KIE_API_KEY=...
```

Restart the container (or redeploy on Coolify) after any change — keys are read at startup.

### Step 4 — check

Sign in. If anything is missing, the page for that feature shows a **Setup incomplete** banner naming the exact variables to add, and its Launch button stays disabled until they're set.

**Optional extras:** NextCloud (share links for finished videos) and NocoDB (run records) — see the [configuration reference](#configuration-reference). Without NextCloud, finished videos are kept on the server and played/downloaded in the app.

> Upgrading from an older version? `GEMINIGEN_API_KEY` is still read if `SNAPGEN_API_KEY` isn't set. Straico is no longer supported — its key is ignored.

## Quick start (Docker Compose)

```bash
git clone https://github.com/devhuzi/ai-video-hub.git
cd ai-video-hub

cp .env.example .env
# Edit .env: set APP_PASSWORD, SESSION_SECRET and your provider keys (see Setup).

docker compose up -d --build
```

Open **http://localhost:8080** and sign in with `APP_PASSWORD`.

Data (database, uploads, renders) is stored in `./data` next to the compose file. Check health with `curl http://localhost:8080/api/` and logs with `docker compose logs -f`.

To stop: `docker compose down` (your `./data` folder is kept).

## Deploy on Coolify

[Coolify](https://coolify.io) builds straight from the repository's `Dockerfile`.

1. **Create the resource**
   In your Coolify project: **+ New → Public Repository** (or **Private Repository (with GitHub App)** for a private fork). Paste the repository URL and choose the branch (`main`).

2. **Build settings**
   - **Build Pack:** `Dockerfile`
   - **Base Directory:** `/`
   - **Dockerfile Location:** `/Dockerfile`
   - **Ports Exposes:** `80`

   Use the Dockerfile build pack rather than Docker Compose — the compose file expects a local `.env` file that doesn't exist on Coolify.

3. **Environment variables**
   Open **Environment Variables** and add your password and keys ([Setup](#setup-password-and-api-keys) explains each one):

   ```
   APP_PASSWORD=<long random password>
   SESSION_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
   SNAPGEN_API_KEY=...
   OPENROUTER_API_KEY=...
   FAL_KEY=...
   KIE_API_KEY=...
   ```

   Tick **Is Literal?** for values containing `$` so Coolify doesn't try to interpolate them.

   Do **not** set `DATA_DIR` — the image already uses `/app/data`. Leave `CORS_ORIGINS` empty.

4. **Persistent storage (important)**
   Open **Persistent Storage → + Add → Volume Mount**:
   - **Name:** `video-hub-data` (anything)
   - **Destination Path:** `/app/data`

   Without this, the database, uploaded logos and locally stored videos are lost on every redeploy.

5. **Domain**
   Under **General → Domains**, set your domain, e.g. `https://video.example.com`. Coolify issues the TLS certificate. Point the domain's DNS `A` record at your server first.

6. **Health check** (optional — the image already defines one)
   Path `/api/`, port `80`.

7. **Deploy.** The first build takes a few minutes (frontend build + Python dependencies). Open your domain and sign in.

**Auto-deploy:** with the GitHub App source, Coolify redeploys on every push to the chosen branch. With a public repository, enable the webhook under **Webhooks** or click **Redeploy**.

**Resources:** if Script Studio renders are slow or get killed, raise the container's memory limit under **Resources** (2 GB+ recommended).

## Deploy with plain Docker

```bash
docker build -t ai-video-production-hub .

docker run -d --name video-hub \
  --restart unless-stopped \
  -p 8080:80 \
  --env-file .env \
  -v video-hub-data:/app/data \
  ai-video-production-hub
```

Put it behind a reverse proxy with TLS (Caddy, Traefik, nginx) for anything reachable from the internet — the login password travels in the request body.

## Local development

Prerequisites: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node.js 20+, and FFmpeg on your `PATH`.

```bash
cp .env.example .env          # set APP_PASSWORD; DATA_DIR=./data is already set
```

**Backend** (http://127.0.0.1:8001):

```bash
cd backend
uv venv
uv pip install -r requirements.txt -r requirements-dev.txt
uv run uvicorn server:app --reload --port 8001
```

**Frontend** (http://localhost:5173, proxies `/api` to the backend on port 8001):

```bash
cd frontend
npm install
npm start
```

**Tests** — provider APIs are mocked; no keys or credits needed:

```bash
cd backend
uv run pytest -q
```

```bash
cd frontend
npm run build
```

## Configuration reference

All configuration is via environment variables (`.env` locally, the Environment Variables tab on Coolify).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_PASSWORD` | **yes** | — | Password for the web UI. The server will not start without it. |
| `SESSION_SECRET` | recommended | derived from `APP_PASSWORD` | Key that signs login tokens (valid 7 days). Changing it — or the password — signs everyone out. |
| `SNAPGEN_API_KEY` | for prompt packs | — | SnapGen images and all video (Veo 3.1, Omni Flash, Vela AI). Legacy name `GEMINIGEN_API_KEY` is still read. |
| `OPENROUTER_API_KEY` | for Script Studio | — | OpenRouter LLM (scene split + image prompts) |
| `FAL_KEY` | for Script Studio* | — | fal.ai ElevenLabs narration, scene images, image fallback. *Or `KIE_API_KEY` for narration. |
| `KIE_API_KEY` | recommended | — | Kie.ai image fallback and backup narration |
| `DEFAULT_LLM_MODEL` | no | `anthropic/claude-sonnet-5` | Default OpenRouter model for Script Studio (the UI lists every OpenRouter model) |
| `NEXTCLOUD_URL` | no | — | WebDAV base, e.g. `https://cloud.example.com/remote.php/dav/files/<user>` |
| `NEXTCLOUD_USERNAME` | no | — | NextCloud user |
| `NEXTCLOUD_PASSWORD` | no | — | Use an app password |
| `NEXTCLOUD_ROOT` | no | `/AI_Video_Production_Hub` | Folder videos are uploaded into |
| `NOCODB_URL` | no | — | NocoDB base URL |
| `NOCODB_API_TOKEN` | no | — | NocoDB API token |
| `NOCODB_BASE_ID` | no | — | NocoDB base to create tables in. Sync runs only when all three NocoDB vars are set. |
| `DATA_DIR` | no | `/app/data` (Docker) | SQLite DB, uploads, renders, model cache. Relative paths resolve from the project root. |
| `CORS_ORIGINS` | no | empty | Comma-separated origins allowed to call the API cross-origin. Leave empty for the standard single-container setup. |
| `APIFY_API_TOKEN`, `GOOGLE_AI_STUDIO_API_KEY` | no | — | Only for the optional `execution/` scripts |

When NextCloud isn't configured, finished videos stay in `DATA_DIR` and are played and downloaded from the run page.

## Writing prompt packs

The parser accepts labelled sections, JSON, or emoji-titled blocks. The simplest form:

```text
Image 1: Wide static shot from the doorway, eye level, 24mm lens. An empty concrete
garage with cracked grey floor, bare fluorescent tubes, flat cold light...

Image 2: Same wide static shot from the doorway, eye level, 24mm lens. Two workers in
grey coveralls grind the floor...

Image 3: Same wide static shot from the doorway, eye level, 24mm lens. A glossy
charcoal epoxy floor reflects warm pendant lights...

Video 1: The workers move across the floor with grinders, dust drifting in the light...
Video 2: The fresh coat cures as the lights warm up; the camera holds perfectly still...
```

or JSON:

```json
{ "image_prompts": ["...", "...", "..."], "video_prompts": ["...", "..."] }
```

What makes sequences consistent:

- **Repeat the camera setup verbatim** at the start of every image prompt.
- **Describe characters and locations once in full, then repeat that exact text** every time they reappear.
- **Describe only what is present.** Image and video models read every word as something to draw — "no split screen" tends to *produce* split screens. The backend strips `NEGATIVE:` blocks as a safety net, but positive wording works best.
- Aim for 150+ words per image prompt and 100+ per video prompt.

## Data, backups and upgrades

Everything that matters lives in `DATA_DIR` (`/app/data` in Docker):

```
/app/data
├── video_hub.db        SQLite database (runs, images, videos, scenes, logs)
├── uploads/            watermark logos
├── script_studio/<id>/ Script Studio renders
├── pack_work/<id>/     prompt-pack renders and local final videos
└── models/             background-removal model cache (downloaded on first use)
```

- **Backup:** stop the container (or accept a crash-consistent copy) and archive the folder or volume, e.g.
  `docker run --rm -v video-hub-data:/data -v "$PWD":/backup alpine tar czf /backup/video-hub-$(date +%F).tgz -C /data .`
- **Upgrade:** pull the new version and rebuild (`docker compose up -d --build`, or redeploy on Coolify). Database migrations run automatically on startup and only ever add tables and columns.
- **Interrupted runs:** if the server restarts mid-run, those runs are marked failed with "Interrupted by server restart" — press **Retry** to resume them.
- Deleting a run from the UI also deletes its render folder.

## Security notes

- Every `/api` route except the health check and login requires a signed bearer token. Failed logins are rate-limited per IP.
- The API listens on loopback inside the container; only nginx is exposed.
- The API process runs as an unprivileged user.
- Uploaded logos are size-limited (10 MB) and verified as real images.
- **Use HTTPS** in front of the app (Coolify does this for you).
- This is a single-password app for trusted operators — there are no user accounts or roles.
- Secrets live only in environment variables. `.env` is git-ignored; never commit it.

Found a vulnerability? Please open a private security advisory on the repository rather than a public issue.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits immediately, log says `APP_PASSWORD` is not set | Set `APP_PASSWORD` in `.env` / Coolify environment variables |
| Everything is lost after a Coolify redeploy | Add the persistent volume at `/app/data` (step 4 above) |
| "Unauthorized" right after signing in | `SESSION_SECRET` or `APP_PASSWORD` changed — sign in again |
| **Setup incomplete** banner / Launch disabled | Add the variables the banner names (see [Setup](#setup-password-and-api-keys)), then restart or redeploy |
| "… is not configured on the server" when launching | Same — the named key is missing on the server |
| Image step fails with "policy violation" | The provider's safety filter rejected the prompt — reword it and use **Regenerate** on that image |
| Run stuck in one step for a long time | Video jobs can legitimately take 10–30 min. Check the run's logs; cancel and retry if the provider timed out |
| Image step fails with credit / 402 errors | Top up the provider shown in the log; the fallback chain moves on automatically where it can |
| First Script Studio run with a logo is slow | The background-removal model (~170 MB) downloads once into `/app/data/models` |
| Finished video has no share link | NextCloud isn't configured — use the player / download on the run page |
| A clip fails with a `4xx` error | Check the error in the run log — it carries SnapGen's own message (e.g. an unsupported setting or no credits). Vela AI errors usually mean SnapGen's API doesn't accept it; switch to a Veo model |
| 1:1 or 9:16 output looks wrong | Pick the aspect ratio before launching; it applies to every image and clip in the run |

## Project structure

```
backend/
  app/
    api.py            routes
    auth.py           login + bearer tokens + rate limit
    config.py         environment variables
    models.py         request validation
    views.py          response shaping, step timeline
    providers/        SnapGen, OpenRouter, fal.ai, Kie.ai, image fallback chain
    integrations/     NextCloud, NocoDB (optional)
    pipelines/        runner (queue, pause/cancel), pack, script
  database.py         SQLite + migrations
  script_studio.py    TTS, subtitles, FFmpeg helpers
  server.py           uvicorn entry point (server:app)
  tests/              pytest suite
frontend/
  src/pages/          Queue, New pack, Script Studio, Run detail
  src/lib/            API client, prompt-pack parser
deploy/               nginx + supervisord config
directives/, execution/   optional channel-style tooling (below)
Dockerfile, docker-compose.yml, .env.example
```

## Optional: clone a channel's style

`execution/` has three standalone scripts that scrape a YouTube / TikTok / Facebook page (via Apify), download recent videos, and analyse their visual style with Gemini. The workflow in [`directives/clone_channel_style.md`](directives/clone_channel_style.md) turns that analysis into a reusable style recipe and prompt packs for this app. It's written as an SOP for an AI coding agent (the `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` files describe the project to agents), but you can follow it by hand.

```bash
uv pip install -r execution/requirements.txt
```

These scripts are not part of the Docker image. Respect each platform's terms of service and creators' rights when using them.

## License

[MIT](LICENSE)
