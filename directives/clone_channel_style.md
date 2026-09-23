---
description: Turn a social media page link into a reusable style recipe + a ready-to-paste prompt pack
---

# Clone a Channel's Style → Style Recipe + Prompt Pack

The output is:

1. A **style recipe** at `directives/styles/<slug>.md` — the reusable formula for this channel.
2. One or more **prompt packs** the user pastes into the New pack page (`/new`).

## Inputs
- `page_url` — YouTube channel, TikTok profile, or Facebook page URL
- Optional: number of videos (default 20), session name

All three scripts spend paid credits (Apify, Gemini). Confirm with the user before running them.

## Step 1: Scrape

```bash
python execution/scrape_social_page.py "<URL>" --session <slug> --limit 20
```

- Auto-detects platform. Apify actors: YouTube `apify/youtube-scraper`, TikTok `clockworks/tiktok-profile-scraper`, Facebook `apify/facebook-posts-scraper`.
- Output: `.tmp/<slug>/videos_metadata.json` (title, caption, hashtags, url, download_url, views, duration, transcript for YouTube if available).

## Step 2: Download (immediately — TikTok URLs expire within hours)

```bash
python execution/download_videos.py --session <slug>
```

- Output: `.tmp/<slug>/video_01.mp4` … `video_20.mp4`. yt-dlp for YouTube/Facebook; TikTok tries direct HTTP first.
- Do NOT delete `.tmp/<slug>/` until the user says so.

## Step 3: Analyze with Gemini

```bash
python execution/analyze_videos.py --session <slug> --limit 5
```

- Uses `GOOGLE_AI_STUDIO_API_KEY`, model `gemini-2.5-pro`. Start with 5; use `--offset 5 --limit 5` etc. to add more.
- Output: `.tmp/<slug>/content_dna.md` (per video) and `.tmp/<slug>/master_dna_summary.md` (synthesized formula).

## Step 4: Q&A with the user

Present the findings, then ask only:

1. **Name** — what to call this style.
2. **Video system** — confirm the recommendation:

   | Use | When |
   |---|---|
   | `veo31_frame` (frame mode) | Camera mostly locked/static; story told through distinct composed frames; before/after transformations |
   | `veo_extend` (extend mode) | Camera travels/follows the subject continuously; POV or journey narrative |

3. **Variations** — approve/add/remove the list of sub-variants (target ~20).

Do not continue until all three are answered.

## Step 5: Write the style recipe

Create `directives/styles/<slug>.md` containing:

- Visual formula from the analysis: camera, lens, lighting progression, palette, pacing, motion, audio feel
- **CHARACTER LOCK** — exact protagonist description to paste verbatim into every prompt
- **ENVIRONMENT LOCK** — how each location is described on first appearance
- **SCENE LOCK** camera line (frame mode)
- Opening state (frame 1) and final state (last frame) descriptions
- Ordered intermediate stages
- If workers/people appear: how many, workwear, tools per stage, when they enter/leave
- Recommended pack shape (e.g. 6 images + 5 videos, or 1 image + 4 extends) and shot duration
- The approved variation list

## Step 6: Generate a prompt pack

Using the recipe, write a pack that follows the **Writing Prompt Packs** rules in `AGENTS.md` (SCENE LOCK, locks, continuity, ≥150/≥100 words, PROMPT PURITY — no `NEGATIVE:` blocks or "no X" lists). Format as `Image 1:` / `Video 1:` labels or JSON so `frontend/src/lib/promptPackParser.js` accepts it.

## Step 7: Test

User pastes the pack into the New pack page. Check that:
1. The parser shows the expected mode and counts (no shape error).
2. Images stay consistent frame to frame (camera, character, environment).
3. Video clips connect cleanly.

Feed any drift back into the recipe (usually a lock that wasn't verbatim enough).

## Step 8: Clean up `.tmp/` — only when the user says so

```powershell
Remove-Item -Path ".tmp\<slug>" -Recurse -Force
```

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Parser shape error | Counts don't match a supported shape | Frame: N+(N-1), N+N, or 2N+N. Extend: 1 image + ≥1 videos |
| Building/room changes between frames | Environment not repeated verbatim | Strengthen ENVIRONMENT LOCK in the recipe |
| Character's clothes/face drift | CHARACTER LOCK paraphrased | Paste the exact same block in every prompt |
| Split-screen / collage artifacts | Negative phrasing in prompt ("no split image") | Rewrite in positive-only language |
| Output too generic | Analysis misread the theme | Re-read `master_dna_summary.md`; correct the recipe by hand |
| TikTok download 403 | Apify media URL expired | Re-run Step 1 and download immediately |
