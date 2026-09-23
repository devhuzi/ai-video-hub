// Flexible prompt pack parser — accepts basically anything the user pastes.
//
// Strategies, tried in order:
//   1. JSON object anywhere in text with image_prompts/video_prompts keys
//      (handles prose wrapper, markdown code fences, trailing commas, etc.)
//   2. Section headers ("Image Prompts:", "## Videos", "### Image Prompts") with
//      numbered/bulleted items underneath.
//   3. Per-item labels on their own lines: IMAGE 1:, Img 2 -, Video #3:, Scene 1:, Frame 2:
//   4. Semantic emoji-prefixed titles: 📸/🖼️ → image, 🎥/🎬 → video,
//      each title line followed by its body paragraph. Classifies unlabeled
//      sections by scanning the title + body for keywords (POV/timelapse →
//      video, setup/still/photograph → image).
//
// Detection rules (see detectMode below):
//   - N images + (N-1 or N) videos, or 2N images + N videos -> mode = "frame"  (veo31_frame)
//   - 1 image + >=1 videos                                  -> mode = "extend" (grok_sequential_extend)
//   - anything else                                         -> error

export function parsePromptPack(text) {
  const empty = { imagePrompts: [], videoPrompts: [], mode: null, shape: null, videoSystem: null, error: null };
  if (!text || !text.trim()) {
    return { ...empty, error: "Paste a prompt pack above." };
  }

  const cleaned = stripCodeFences(text);

  let imagePrompts = [];
  let videoPrompts = [];

  // ---- Strategy 1: JSON object with image_prompts/video_prompts keys ----
  const jsonResult = tryExtractJson(cleaned);
  if (jsonResult.imagePrompts.length || jsonResult.videoPrompts.length) {
    imagePrompts = jsonResult.imagePrompts;
    videoPrompts = jsonResult.videoPrompts;
  }

  // ---- Strategy 2: Section headers with lists ----
  if (imagePrompts.length === 0 && videoPrompts.length === 0) {
    const sectionResult = extractBySections(cleaned);
    imagePrompts = sectionResult.imagePrompts;
    videoPrompts = sectionResult.videoPrompts;
  }

  // ---- Strategy 3: Per-item labels (IMAGE 1:, Video 2 -, Scene 1:, Frame 3:) ----
  if (imagePrompts.length === 0 && videoPrompts.length === 0) {
    const labeledResult = extractLabeledSections(cleaned);
    imagePrompts = labeledResult.imagePrompts;
    videoPrompts = labeledResult.videoPrompts;
  }

  // ---- Strategy 4: Semantic emoji-prefixed titles ----
  // Splits on title lines that begin with a non-word character (emoji, symbol,
  // U+FFFD replacement) and classifies each section by emoji + keyword hints.
  if (imagePrompts.length === 0 && videoPrompts.length === 0) {
    const semanticResult = extractBySemanticTitles(cleaned);
    imagePrompts = semanticResult.imagePrompts;
    videoPrompts = semanticResult.videoPrompts;
  }

  if (imagePrompts.length === 0 && videoPrompts.length === 0) {
    return {
      ...empty,
      error: "Could not find any image or video prompts. Try JSON, or label sections like \"Image Prompts:\" and \"Video Prompts:\".",
    };
  }

  // Clean out empty strings just in case
  imagePrompts = imagePrompts.map((p) => p.trim()).filter(Boolean);
  videoPrompts = videoPrompts.map((p) => p.trim()).filter(Boolean);

  return { imagePrompts, videoPrompts, ...detectMode(imagePrompts, videoPrompts) };
}

// Detection rules, shared by the parser and the editor (which re-runs them
// after the user edits, reorders or deletes prompts).
//   - N images + (N-1) videos, N >= 2  -> frame, chain
//   - N images + N videos, N >= 2      -> frame, chain + hero reveal
//   - 2N images + N videos             -> frame, paired bookends
//   - 1 image + >=1 videos             -> extend
export function detectMode(imagePrompts, videoPrompts) {
  const nImg = imagePrompts.length;
  const nVid = videoPrompts.length;

  if (nImg >= 2) {
    // Frame mode accepts three shapes:
    //   - N images + (N-1) videos: chain — video i uses image[i] and image[i+1].
    //   - N images + N videos: chain + hero reveal — last video re-uses image[-1] as both frames.
    //   - 2N images + N videos: paired bookends — video i uses image[2i] and image[2i+1]
    //     (each scene has its own explicit first and last frame).
    const isChain     = nVid === nImg - 1;
    const isChainHero = nVid === nImg;
    const isPaired    = nVid >= 1 && nImg === 2 * nVid;
    if (!isChain && !isChainHero && !isPaired) {
      return {
        mode: null,
        shape: null,
        videoSystem: null,
        error: `Frame mode needs N images + (N-1) videos (chain), N images + N videos (chain + hero reveal), or 2N images + N videos (paired first/last frame per scene). Found ${nImg} images and ${nVid} videos.`,
      };
    }
    // 2 images + 1 video is both a chain and a pair; the frames used are identical.
    const shape = isChain ? "chain" : isChainHero ? "chain_hero" : "paired";
    return { mode: "frame", shape, videoSystem: "veo31_frame", error: null };
  }

  if (nImg === 1) {
    if (nVid < 1) {
      return { mode: null, shape: null, videoSystem: null, error: "Extend mode needs 1 image and at least 1 video prompt." };
    }
    return { mode: "extend", shape: "extend", videoSystem: "grok_sequential_extend", error: null };
  }

  return { mode: null, shape: null, videoSystem: null, error: "Need at least 1 image prompt." };
}

// Plain-language explanation of how each video uses the images.
export const SHAPE_INFO = {
  chain: {
    label: "Chain",
    description: "Each video runs from one image to the next: video 1 uses images 1 → 2, video 2 uses images 2 → 3, and so on.",
  },
  chain_hero: {
    label: "Chain + hero reveal",
    description: "A chain between consecutive images, plus a final video that holds on the last image as both first and last frame.",
  },
  paired: {
    label: "Paired bookends",
    description: "Each video has its own first and last frame: video 1 uses images 1 → 2, video 2 uses images 3 → 4, and so on.",
  },
  extend: {
    label: "Extend",
    description: "One starting image. The first video animates from it and every following video extends the previous clip.",
  },
};

// Which image indexes (0-based) a video uses, for previews before the backend
// reports first_frame_index / last_frame_index.
export function framesForVideo(shape, videoIndex, numImages) {
  if (shape === "paired") return [2 * videoIndex, 2 * videoIndex + 1];
  if (shape === "chain" || shape === "chain_hero") {
    if (videoIndex + 1 >= numImages) return [numImages - 1, numImages - 1];
    return [videoIndex, videoIndex + 1];
  }
  if (shape === "extend") return videoIndex === 0 ? [0, null] : [null, null];
  return [null, null];
}

// Remove surrounding ```...``` code fences (common when users copy from chat).
function stripCodeFences(text) {
  return text.replace(/```(?:json|javascript|js|text)?\s*\n?/gi, "").replace(/```/g, "");
}

// Try parsing any {...} block in the text as JSON with image/video prompt keys.
// Also handles standalone `image_prompts = [...]` / `video_prompts: [...]` assignments.
function tryExtractJson(text) {
  const candidates = findJsonCandidates(text);
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      const imgs = pickPromptArray(parsed, ["image_prompts", "imagePrompts", "images", "image"]);
      const vids = pickPromptArray(parsed, ["video_prompts", "videoPrompts", "videos", "video"]);
      if (imgs.length || vids.length) {
        return { imagePrompts: imgs, videoPrompts: vids };
      }
    } catch {
      // try next candidate
    }
  }

  // Fallback: look for `image_prompts` / `video_prompts` keywords immediately
  // followed by a bracketed array (handles Python/JS assignments, loose key:value).
  const imgs = extractArrayAfterKey(text, /\b(?:image_?prompts?|images)\b/i);
  const vids = extractArrayAfterKey(text, /\b(?:video_?prompts?|videos)\b/i);
  if (imgs.length || vids.length) {
    return { imagePrompts: imgs, videoPrompts: vids };
  }

  return { imagePrompts: [], videoPrompts: [] };
}

function extractArrayAfterKey(text, keyRegex) {
  const match = keyRegex.exec(text);
  if (!match) return [];
  // Find the first `[` after the key within a reasonable window (skipping `:` / `=` / quotes).
  const start = match.index + match[0].length;
  const window = text.slice(start, start + 20000);
  const bracketIdx = window.indexOf("[");
  if (bracketIdx === -1 || bracketIdx > 50) return [];
  // Walk to the matching `]`.
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = bracketIdx; i < window.length; i++) {
    const ch = window[i];
    if (escape) { escape = false; continue; }
    if (ch === "\\") { escape = true; continue; }
    if (ch === '"' || ch === "'") {
      if (!inString) inString = ch;
      else if (inString === ch) inString = false;
      continue;
    }
    if (inString) continue;
    if (ch === "[") depth++;
    else if (ch === "]") {
      depth--;
      if (depth === 0) {
        const raw = window.slice(bracketIdx, i + 1);
        // Convert single quotes to double for JSON parse (naive but effective).
        const normalized = raw.replace(/'([^'\\]*(?:\\.[^'\\]*)*)'/g, (_, inner) => '"' + inner.replace(/"/g, '\\"') + '"');
        try {
          const arr = JSON.parse(normalized);
          if (Array.isArray(arr)) {
            return arr.map((x) => (typeof x === "string" ? x.trim() : null)).filter(Boolean);
          }
        } catch {
          // not valid JSON — fall through
        }
        return [];
      }
    }
  }
  return [];
}

// Find every balanced {...} substring in the text.
function findJsonCandidates(text) {
  const out = [];
  for (let start = 0; start < text.length; start++) {
    if (text[start] !== "{") continue;
    let depth = 0;
    let inString = false;
    let escape = false;
    for (let i = start; i < text.length; i++) {
      const ch = text[i];
      if (escape) { escape = false; continue; }
      if (ch === "\\") { escape = true; continue; }
      if (ch === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) {
          out.push(text.slice(start, i + 1));
          start = i; // skip past this block; outer loop will advance
          break;
        }
      }
    }
  }
  return out;
}

// From a parsed JSON object, pull the first key that maps to an array of strings.
function pickPromptArray(obj, keys) {
  if (!obj || typeof obj !== "object") return [];
  for (const k of keys) {
    const v = obj[k];
    if (Array.isArray(v)) {
      const strs = v.map((x) => (typeof x === "string" ? x : (x && typeof x === "object" && typeof x.prompt === "string" ? x.prompt : null))).filter(Boolean);
      if (strs.length) return strs;
    }
  }
  return [];
}

// Strategy 2: line-based section detection. Walks the text line-by-line,
// classifies each line as a section header (contains "image"/"video" keyword
// and looks header-like), a list item (starts with number/bullet), or body
// text. Lines under each header are collected as that section's content.
function extractBySections(text) {
  const lines = text.split(/\r?\n/);
  // Identify header lines.
  const isHeaderLine = (line) => {
    const trimmed = line.trim();
    if (!trimmed) return null;
    if (trimmed.length > 80) return null; // too long to be a header
    // Must contain "image" or "video" as a standalone keyword (not followed by a digit,
    // because "Image 1:" is a per-item label handled by strategy 3).
    const kwRegex = /\b(images?(?:[ \t]+prompts?)?|videos?(?:[ \t]+prompts?)?)\b(?!\s*\d)/i;
    const m = trimmed.match(kwRegex);
    if (!m) return null;
    // Looks header-ish: ends with colon, or is short (<=40 chars) with no terminal period,
    // or starts with markdown header / bold markers.
    const endsColon = /:\s*$/.test(trimmed);
    const isShort = trimmed.length <= 40 && !/[.!?]$/.test(trimmed);
    const isMarkdown = /^[#*_]/.test(trimmed);
    if (!endsColon && !isShort && !isMarkdown) return null;
    const kind = m[1].toLowerCase().startsWith("i") ? "image" : "video";
    return kind;
  };

  const sections = [];
  let current = null;
  for (const line of lines) {
    const headerKind = isHeaderLine(line);
    if (headerKind) {
      if (current) sections.push(current);
      current = { kind: headerKind, bodyLines: [] };
      continue;
    }
    if (current) current.bodyLines.push(line);
  }
  if (current) sections.push(current);
  if (sections.length === 0) return { imagePrompts: [], videoPrompts: [] };

  const images = [];
  const videos = [];
  for (const sec of sections) {
    const items = extractListItems(sec.bodyLines.join("\n"));
    if (sec.kind === "image") images.push(...items);
    else videos.push(...items);
  }
  return { imagePrompts: images, videoPrompts: videos };
}

// Extract numbered/bulleted/JSON-array-string items from a chunk of text.
function extractListItems(chunk) {
  if (!chunk) return [];

  // If it looks like a JSON array, try to parse it.
  const arrMatch = chunk.match(/\[[\s\S]*?\]/);
  if (arrMatch) {
    try {
      const arr = JSON.parse(arrMatch[0]);
      if (Array.isArray(arr) && arr.every((x) => typeof x === "string")) {
        const filtered = arr.map((s) => s.trim()).filter(Boolean);
        if (filtered.length) return filtered;
      }
    } catch {
      // not a JSON array — try list markers below
    }
  }

  // Numbered or bulleted items: "1. ...", "1) ...", "- ...", "* ..."
  // Split by detecting item-start markers at line boundaries.
  const itemStartRegex = /(?:^|\n)[ \t]*(?:\d+[.)]|[-*•])[ \t]+/g;
  const markers = [];
  let m;
  while ((m = itemStartRegex.exec(chunk)) !== null) {
    markers.push({ start: m.index + (chunk[m.index] === "\n" ? 1 : 0), contentStart: m.index + m[0].length });
  }
  if (markers.length >= 1) {
    const items = [];
    for (let i = 0; i < markers.length; i++) {
      const end = i + 1 < markers.length ? markers[i + 1].start : chunk.length;
      const body = chunk.slice(markers[i].contentStart, end).trim();
      // Strip surrounding quotes/backticks if present.
      const unquoted = body.replace(/^["'`]+|["'`]+$/g, "").trim();
      if (unquoted) items.push(unquoted);
    }
    if (items.length) return items;
  }

  // Last resort: split by blank lines (paragraph-per-prompt format).
  const paragraphs = chunk.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  return paragraphs;
}

// Strategy 3: per-item labels on their own lines.
// Matches IMAGE 1:, Img 2 -, Video #3:, Scene 1:, Frame 2:, Shot 1:, Clip 2:,
// and also tolerates leading emoji/markdown/bullet decoration and unicode dashes
// (em dash —, en dash –) as separators. Examples that now match:
//   🖼️ IMAGE 1 — Abandoned Facade (Before)
//   ## Video 3 – Hero Reveal
//   **Image 2**: Construction phase
function extractLabeledSections(text) {
  // Leading decoration: up to 6 non-word, non-newline chars (emoji, #, *, _, spaces).
  // Separator set: colon, hyphen, em dash (—), en dash (–), figure dash (‒), horizontal bar (―).
  const labelRegex = /^[^\w\n]{0,8}(image(?:[ \t]+prompt)?|img|picture|scene|frame|video(?:[ \t]+prompt)?|vid|shot|clip)[ \t]*#?[ \t]*(\d+)[ \t]*[*_]{0,2}[ \t]*[:\-\u2014\u2013\u2012\u2015][ \t]*/gim;
  const matches = [];
  let m;
  while ((m = labelRegex.exec(text)) !== null) {
    const label = m[1].toLowerCase();
    const isVideo = label.startsWith("v") || label === "shot" || label === "clip";
    const isImageLabel = label.startsWith("i") || label === "picture" || label === "scene" || label === "frame";
    matches.push({
      kind: isVideo ? "video" : (isImageLabel ? "image" : null),
      number: parseInt(m[2], 10),
      start: m.index,
      contentStart: m.index + m[0].length,
    });
  }
  if (matches.length === 0) return { imagePrompts: [], videoPrompts: [] };

  const sections = [];
  for (let i = 0; i < matches.length; i++) {
    if (!matches[i].kind) continue;
    const end = i + 1 < matches.length ? matches[i + 1].start : text.length;
    const body = text.slice(matches[i].contentStart, end).trim();
    if (body) sections.push({ ...matches[i], body });
  }

  const images = sections
    .filter((s) => s.kind === "image")
    .sort((a, b) => a.number - b.number)
    .map((s) => s.body);
  const videos = sections
    .filter((s) => s.kind === "video")
    .sort((a, b) => a.number - b.number)
    .map((s) => s.body);

  return { imagePrompts: images, videoPrompts: videos };
}

// Strategy 4: detect sections by emoji/symbol-prefixed title lines and
// classify each section as image or video using emoji + keyword heuristics.
//
// A "title line" is a short line (<=140 chars) whose first non-whitespace
// character is NOT a letter or digit — emojis, symbols, or U+FFFD (the
// replacement char for broken encoding). Body text of a section runs until
// the next title line.
function extractBySemanticTitles(text) {
  const lines = text.split(/\r?\n/);
  const isTitleLine = (line) => {
    const t = line.trim();
    if (!t) return false;
    if (t.length > 140) return false;
    // First non-space character must not be a letter or digit.
    const first = t[0];
    if (/[A-Za-z0-9]/.test(first)) return false;
    // Skip pure punctuation lines like "---" or "===".
    if (/^[-=*_]{3,}$/.test(t)) return false;
    return true;
  };

  // Collect title line indices.
  const titleIdxs = [];
  lines.forEach((line, i) => {
    if (isTitleLine(line)) titleIdxs.push(i);
  });
  if (titleIdxs.length === 0) return { imagePrompts: [], videoPrompts: [] };

  // Build sections: each title + body until the next title.
  const sections = [];
  for (let j = 0; j < titleIdxs.length; j++) {
    const start = titleIdxs[j];
    const end = j + 1 < titleIdxs.length ? titleIdxs[j + 1] : lines.length;
    const title = lines[start].trim();
    const body = lines.slice(start + 1, end).join("\n").trim();
    if (!body) continue;
    const kind = classifyTitleAsImageOrVideo(title, body);
    if (kind) sections.push({ kind, title, body });
  }

  // Preserve top-to-bottom order within each kind.
  const images = sections.filter((s) => s.kind === "image").map((s) => s.body);
  const videos = sections.filter((s) => s.kind === "video").map((s) => s.body);
  return { imagePrompts: images, videoPrompts: videos };
}

// Emoji and keyword cues for classifying an unlabeled section as image vs video.
const IMAGE_EMOJIS = /[\u{1F4F8}\u{1F5BC}\u{1F3A8}\u{1F3DE}\u{1F4F7}\u{1F4F1}]/u; // 📸 🖼 🎨 🏞 📷 📱
const VIDEO_EMOJIS = /[\u{1F3A5}\u{1F3AC}\u{1F4F9}\u{1F39E}\u{1F3A6}]/u;            // 🎥 🎬 📹 🎞 🎦

function classifyTitleAsImageOrVideo(title, body) {
  // 1. Explicit emoji in title — strongest signal.
  if (IMAGE_EMOJIS.test(title)) return "image";
  if (VIDEO_EMOJIS.test(title)) return "video";

  // 2. Explicit keywords in title.
  const t = title.toLowerCase();
  const titleHasImageWord = /\b(image|photo|photograph|still|snapshot|portrait|setup\s+image|reference\s+image|starting\s+frame|first\s+frame|last\s+frame)\b/.test(t);
  const titleHasVideoWord = /\b(video|clip|shot|timelapse|time-lapse|pov|footage|animation|animate|scene|sequence|cinematic|motion)\b/.test(t);
  if (titleHasImageWord && !titleHasVideoWord) return "image";
  if (titleHasVideoWord && !titleHasImageWord) return "video";

  // 3. Body keyword scoring.
  const b = body.toLowerCase().slice(0, 800);
  let imageScore = 0;
  let videoScore = 0;
  const countMatches = (regex) => {
    const m = b.match(regex);
    return m ? m.length : 0;
  };
  videoScore += countMatches(/\b(pov|timelapse|time-lapse|footage|animate|animation|cinematic|dolly|pan|tilt|zoom|push[- ]in|tracking\s+shot|camera\s+move)\b/g);
  videoScore += countMatches(/\b(moving|walks?|runs?|crawls?|climbs?|flies|enters?|exits?|continues?)\b/g);
  imageScore += countMatches(/\b(static|still|photograph|photo|kneeling|holding|standing|portrait|snapshot|single\s+frame)\b/g);
  imageScore += countMatches(/\b(composition|symmetry|framed|tableau)\b/g) * 2;

  if (videoScore > imageScore + 1) return "video";
  if (imageScore > videoScore + 1) return "image";
  // Tie / insufficient signal — leave unclassified rather than guess.
  return null;
}

// Calculate total video length in seconds for the detection chip.
export function calculateTotalLength(mode, numVideos, videoEngine, shotDuration) {
  if (!mode || !numVideos) return 0;
  if (mode === "frame") return numVideos * 8;
  if (mode === "extend") {
    if (videoEngine === "veo") return numVideos * 8;
    return numVideos * (shotDuration || 6);
  }
  return 0;
}

// Derive a pipeline name from the parsed prompts + append a date/time suffix.
// Example: "Kitchen Ruin To Luxury — 2026-04-06 14:30"
export function generatePipelineName(imagePrompts) {
  const stamp = formatTimestamp(new Date());
  const slug = extractTopicSlug(imagePrompts);
  return slug ? `${slug} — ${stamp}` : `Prompt Pack — ${stamp}`;
}

function formatTimestamp(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STOP_WORDS = new Set([
  "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "with", "and", "or", "is", "are", "was", "were", "be",
  "this", "that", "these", "those", "it", "its", "as", "from", "into", "over", "under", "between",
  "scene", "lock", "static", "tripod", "camera", "same", "fixed", "position", "angle", "shot", "image",
  "prompt", "prompts", "frame", "negative", "positive", "photo", "photograph", "photographic", "view",
  "no", "not", "any", "all", "some", "one", "two", "three", "four", "five",
]);

function extractTopicSlug(imagePrompts) {
  if (!imagePrompts || imagePrompts.length === 0) return "";
  // Use the first prompt — it usually describes the starting scene.
  let text = imagePrompts[0];
  // Strip SCENE LOCK boilerplate and common preambles.
  text = text.replace(/SCENE LOCK[^.]*\.?/i, " ");
  text = text.replace(/Image\s*\d+[\s:.-]*/i, " ");
  // Take first 200 chars to keep name extraction bounded.
  text = text.slice(0, 300);
  // Tokenize on non-letter characters.
  const words = text.split(/[^A-Za-z]+/).filter(Boolean);
  const picked = [];
  for (const w of words) {
    const lw = w.toLowerCase();
    if (lw.length < 3) continue;
    if (STOP_WORDS.has(lw)) continue;
    picked.push(lw);
    if (picked.length >= 5) break;
  }
  if (picked.length === 0) return "";
  return picked.map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}
