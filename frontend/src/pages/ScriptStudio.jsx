import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Upload, X } from "lucide-react";
import PageHeader from "../components/PageHeader";
import Estimate from "../components/Estimate";
import SetupBanner, { missingReason } from "../components/SetupBanner";
import { Button } from "../components/ui/button";
import { FieldError, Hint, Input, Label, Segmented, Select, Textarea } from "../components/ui/field";
import { usePipelines } from "../hooks/usePipelines";
import { useSetup } from "../hooks/useSetup";
import { api, errorMessage } from "../lib/api";
import { formatDuration } from "../lib/format";
import { PROVIDER_LABELS, formatUsd } from "../lib/providers";
import { readJson, writeJson } from "../lib/storage";
import { cn, countWords } from "../lib/utils";

const SETTINGS_KEY = "aivph.scriptSettings";
const DEFAULT_SETTINGS = {
  voiceId: "Rachel",
  aspectRatio: "16:9",
  aiModel: "anthropic/claude-sonnet-5",
  imageModel: "fal-ai/nano-banana-2",
  stylePreset: "cinematic",
  customStyle: "",
};

const STYLE_PRESETS = [
  { id: "cinematic", label: "Cinematic", value: "cinematic film still, shallow depth of field, dramatic lighting, 35mm lens, rich color grading" },
  { id: "documentary", label: "Documentary realism", value: "documentary photograph, natural lighting, authentic candid composition, photorealistic" },
  { id: "golden_hour", label: "Golden hour", value: "warm golden hour sunlight, soft shadows, cinematic outdoor photography" },
  { id: "noir", label: "Film noir", value: "black and white film noir, high contrast chiaroscuro lighting, 1940s aesthetic, moody atmosphere" },
  { id: "neon_cyberpunk", label: "Neon cyberpunk", value: "neon-lit cyberpunk cityscape, rain-slick streets, magenta and cyan highlights, cinematic sci-fi" },
  { id: "storybook", label: "Illustrated storybook", value: "hand-painted storybook illustration, soft watercolor textures, whimsical warm palette" },
  { id: "3d_animation", label: "3D animation", value: "high-quality 3D animated movie still, Pixar-style rendering, soft ambient lighting, expressive" },
  { id: "anime", label: "Anime", value: "anime key visual, cel-shaded, vibrant saturated colors, dynamic composition, studio-grade" },
  { id: "minimalist", label: "Minimalist editorial", value: "clean minimalist editorial photography, white negative space, muted palette, sharp focus" },
  { id: "vibrant_pop", label: "Vibrant pop", value: "vibrant high-saturation pop art photography, bold primary colors, playful energetic composition" },
  { id: "vintage_film", label: "Vintage film", value: "vintage 1970s film photograph, warm faded tones, natural grain, nostalgic mood" },
  { id: "epic_fantasy", label: "Epic fantasy", value: "epic fantasy matte painting, sweeping vistas, dramatic god rays, painterly detail" },
  { id: "custom", label: "Custom", value: "" },
];

// Used only when the model list can't be loaded, so the form stays usable.
const FALLBACK_LLM_MODELS = [
  { id: "anthropic/claude-sonnet-5", name: "Claude Sonnet 5" },
];

const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"];
const LOGO_MAX_BYTES = 10 * 1024 * 1024; // matches backend MAX_UPLOAD_BYTES
const WORDS_PER_SECOND = 2.5; // ~150 words per minute narration

const voiceId = (v) => v.id || v.voice_id || "";
const voiceName = (v) => v.name || voiceId(v);
const modelId = (m) => m.id || m.model || "";
const imageUsable = (m) => m.configured !== false;

// OpenRouter prices are USD per million tokens (input / output).
function llmPrice(model) {
  const inp = formatUsd(model?.prompt_price);
  const out = formatUsd(model?.completion_price);
  return inp && out ? `${inp} in / ${out} out per 1M tokens` : null;
}

// Fetch a list endpoint once (and on reload), exposing honest loading/error state.
function useRemoteList(fetcher) {
  const [state, setState] = useState({ status: "loading", items: [], defaultId: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, status: "loading", error: null }));
    fetcherRef
      .current(controller.signal)
      .then((result) => setState({ status: "ok", ...result, error: null }))
      .catch((error) => {
        if (error?.name !== "AbortError") setState({ status: "error", items: [], defaultId: null, error });
      });
    return () => controller.abort();
  }, [attempt]);

  const reload = useCallback(() => setAttempt((a) => a + 1), []);
  return { ...state, reload };
}

function LogoField({ logo, onPick, onClear }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);

  if (logo) {
    return (
      <div className="flex items-center gap-3 rounded border border-line bg-canvas p-2">
        <img src={logo.previewUrl} alt="Watermark logo preview" className="h-12 w-12 rounded-sm bg-raised object-contain" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm text-fg">{logo.file.name}</p>
          <p className="text-xs text-fg-muted" role="status">
            {logo.status === "uploading" ? "Uploading…" : "Uploaded"}
          </p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Remove logo" onClick={onClear}>
          <X className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    );
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        onPick(e.dataTransfer.files?.[0]);
      }}
      className={cn(
        "flex flex-col items-center gap-2 rounded border border-dashed border-line-strong px-3 py-4 text-center",
        dragging && "border-ring",
      )}
    >
      <Button size="sm" onClick={() => input.current?.click()} aria-describedby="logo-hint">
        <Upload className="h-3.5 w-3.5" aria-hidden="true" />
        Choose logo
      </Button>
      <p id="logo-hint" className="text-xs text-fg-muted">
        or drop it here. PNG, JPG, JPEG or WEBP, up to 10 MB.
      </p>
      <input
        ref={input}
        id="logo-file"
        type="file"
        accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          onPick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export default function ScriptStudio() {
  const navigate = useNavigate();
  const { refresh: refreshQueue } = usePipelines();
  const setup = useSetup();
  const scriptFeature = setup.feature("script_studio");

  const [script, setScript] = useState("");
  const [settings, setSettings] = useState(() => readJson(SETTINGS_KEY, DEFAULT_SETTINGS));
  const [name, setName] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [logo, setLogo] = useState(null); // {file, previewUrl, status, id}
  const [logoError, setLogoError] = useState("");
  const [launching, setLaunching] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => writeJson(SETTINGS_KEY, settings), [settings]);
  const set = (key) => (value) => setSettings((s) => ({ ...s, [key]: value }));

  const voices = useRemoteList(api.getTtsVoices);
  const imageModels = useRemoteList(api.getImageModels);
  const llmModels = useRemoteList(api.getLlmModels);

  // Keep the latest preview URL so it can be revoked on unmount.
  const previewRef = useRef(null);
  const uploadSeq = useRef(0);
  useEffect(() => () => previewRef.current && URL.revokeObjectURL(previewRef.current), []);

  const clearLogo = () => {
    uploadSeq.current += 1; // ignore any in-flight upload
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    setLogo(null);
  };

  const pickLogo = async (file) => {
    setLogoError("");
    if (!file) return;
    if (!LOGO_TYPES.includes(file.type)) {
      setLogoError(`${file.name} isn't a PNG, JPG, JPEG or WEBP image.`);
      return;
    }
    if (file.size > LOGO_MAX_BYTES) {
      setLogoError(`${file.name} is larger than 10 MB.`);
      return;
    }
    clearLogo();
    const seq = ++uploadSeq.current;
    const previewUrl = URL.createObjectURL(file);
    previewRef.current = previewUrl;
    setLogo({ file, previewUrl, status: "uploading", id: null });
    try {
      const res = await api.uploadLogo(file);
      if (seq !== uploadSeq.current) return;
      if (!res?.id) throw new Error("The server didn't return an upload id.");
      setLogo((l) => (l ? { ...l, status: "done", id: res.id } : l));
    } catch (err) {
      if (seq !== uploadSeq.current) return;
      clearLogo();
      setLogoError(`Upload failed: ${errorMessage(err)}`);
    }
  };

  const words = countWords(script);
  const narrationSeconds = words / WORDS_PER_SECOND;
  const preset = STYLE_PRESETS.find((p) => p.id === settings.stylePreset) || STYLE_PRESETS[0];
  const isCustom = preset.id === "custom";
  const resolvedStyle = isCustom ? settings.customStyle.trim() : preset.value;

  const voiceList = voices.items.filter((v) => voiceId(v));
  const voiceValid = voiceList.some((v) => voiceId(v) === settings.voiceId);
  const llmLoaded = llmModels.status === "ok" && llmModels.items.length > 0;
  const llmList = llmLoaded ? llmModels.items.map((m) => ({ ...m, id: modelId(m) })) : FALLBACK_LLM_MODELS;
  const selectedLlm = llmList.find((m) => m.id === settings.aiModel.trim());
  const selectedImageModel = imageModels.items.find((m) => modelId(m) === settings.imageModel);
  const selectedImagePrice = formatUsd(selectedImageModel?.price_usd);

  // Snap stale saved selections to what the backend offers, preferring its default
  // and skipping models whose provider isn't configured.
  useEffect(() => {
    const { status, items, defaultId } = imageModels;
    if (status !== "ok" || !items.length) return;
    const current = items.find((m) => modelId(m) === settings.imageModel);
    if (current && imageUsable(current)) return;
    const usable = items.filter(imageUsable);
    if (!usable.length) return;
    const next = usable.some((m) => modelId(m) === defaultId) ? defaultId : modelId(usable[0]);
    if (next !== settings.imageModel) setSettings((s) => ({ ...s, imageModel: next }));
  }, [imageModels, settings.imageModel]);
  // Once, when the catalogue arrives: replace a saved model OpenRouter no longer lists.
  const llmChecked = useRef(false);
  useEffect(() => {
    const { status, items, defaultId } = llmModels;
    if (status !== "ok" || !items.length || llmChecked.current) return;
    llmChecked.current = true;
    setSettings((s) => {
      if (items.some((m) => modelId(m) === s.aiModel)) return s;
      return { ...s, aiModel: items.some((m) => modelId(m) === defaultId) ? defaultId : modelId(items[0]) };
    });
  }, [llmModels]);

  const notReadyReason = missingReason(scriptFeature);
  const imageProvider = selectedImageModel?.provider;
  const imageBlocked =
    selectedImageModel && !imageUsable(selectedImageModel)
      ? `${PROVIDER_LABELS[imageProvider] || imageProvider} isn't configured — add ${setup.envFor(imageProvider)} or choose another model.`
      : null;
  const errors = {
    script: !script.trim() ? "Paste a script." : null,
    voice: !voiceValid ? "Choose a voice." : null,
    style: isCustom && !resolvedStyle ? "Describe the custom style." : null,
    llm: !settings.aiModel.trim()
      ? "Choose a language model."
      : llmLoaded && !selectedLlm
        ? "Pick a model id from the list."
        : null,
  };
  const uploading = logo?.status === "uploading";
  const formValid = !errors.script && !errors.voice && !errors.style && !errors.llm;
  const canLaunch = formValid && !uploading && !launching && scriptFeature.ready && !imageBlocked;

  const launch = async () => {
    setSubmitted(true);
    if (!canLaunch) return;
    setLaunching(true);
    try {
      const res = await api.createScriptPipeline({
        pipeline_name: name.trim() || null,
        script_text: script,
        tts_voice_id: settings.voiceId,
        tts_model: "eleven_multilingual_v2",
        aspect_ratio: settings.aspectRatio,
        ai_model: settings.aiModel.trim(),
        image_gen_model: settings.imageModel,
        watermark_logo_id: logo?.id || null,
        global_style: resolvedStyle || null,
      });
      toast.success("Run started.");
      refreshQueue();
      navigate(`/run/${res.id}`);
    } catch (err) {
      toast.error(`Couldn't start the run: ${errorMessage(err)}`);
      setLaunching(false);
    }
  };

  const estimateQuery = script.trim()
    ? {
        kind: "script",
        word_count: words,
        image_model: settings.imageModel,
        aspect_ratio: settings.aspectRatio,
      }
    : null;

  const showError = (key) => (submitted ? errors[key] : null);

  return (
    <>
      <PageHeader
        title="Script Studio"
        meta="Narrates a script, splits it into scenes, generates one image per scene and renders a subtitled video."
      />
      <SetupBanner feature={scriptFeature} title="Script Studio" />
      <div className="grid gap-6 px-4 py-5 md:px-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <Label
            htmlFor="script"
            hint={
              <span className="font-mono tabular">
                {words.toLocaleString()} words · ~{formatDuration(narrationSeconds)} narration
              </span>
            }
          >
            Script
          </Label>
          <Textarea
            id="script"
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={22}
            aria-invalid={!!showError("script") || undefined}
            aria-describedby="script-error"
            placeholder="Paste the narration script."
            className="resize-y"
          />
          <FieldError id="script-error">{showError("script")}</FieldError>
        </div>

        <aside className="self-start space-y-5 rounded border border-line bg-surface p-4 lg:sticky lg:top-4">
          <div>
            <Label
              htmlFor="voice"
              hint={`ElevenLabs Multilingual v2${voices.provider ? ` via ${PROVIDER_LABELS[voices.provider]}` : ""}`}
            >
              Voice
            </Label>
            <div className="flex gap-2">
              <Select
                id="voice"
                value={voiceValid ? settings.voiceId : ""}
                onChange={(e) => set("voiceId")(e.target.value)}
                disabled={voices.status !== "ok" || voiceList.length === 0}
                aria-invalid={!!showError("voice") || undefined}
                aria-describedby="voice-status"
              >
                <option value="">
                  {voices.status === "loading"
                    ? "Loading voices…"
                    : voices.status === "error"
                      ? "Voices unavailable"
                      : voiceList.length === 0
                        ? "No voices returned"
                        : "Choose a voice"}
                </option>
                {voiceList.map((v) => (
                  <option key={voiceId(v)} value={voiceId(v)}>
                    {voiceName(v)}
                    {v.description ? ` · ${v.description}` : ""}
                  </option>
                ))}
              </Select>
              <Button onClick={voices.reload} pending={voices.status === "loading"}>
                Reload
              </Button>
            </div>
            <div id="voice-status">
              {voices.status === "error" && <FieldError>Couldn&apos;t load voices: {errorMessage(voices.error)}</FieldError>}
              <FieldError>{voices.status === "ok" ? showError("voice") : null}</FieldError>
            </div>
          </div>

          <div>
            <Label htmlFor="style-preset">Visual style</Label>
            <Select id="style-preset" value={preset.id} onChange={(e) => set("stylePreset")(e.target.value)}>
              {STYLE_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
            {isCustom ? (
              <div className="mt-2">
                <label htmlFor="custom-style" className="sr-only">
                  Custom style
                </label>
                <Input
                  id="custom-style"
                  value={settings.customStyle}
                  onChange={(e) => set("customStyle")(e.target.value)}
                  placeholder="e.g. vintage 1970s film photograph, warm faded tones"
                  aria-invalid={!!showError("style") || undefined}
                  aria-describedby="custom-style-error"
                />
                <FieldError id="custom-style-error">{showError("style")}</FieldError>
              </div>
            ) : (
              <Hint>Added to every scene image prompt: {preset.value}</Hint>
            )}
          </div>

          <div>
            <Label
              htmlFor="image-model"
              hint={selectedImagePrice ? <span className="font-mono">{selectedImagePrice} / image</span> : null}
            >
              Image model
            </Label>
            <Select
              id="image-model"
              value={settings.imageModel}
              onChange={(e) => set("imageModel")(e.target.value)}
              disabled={imageModels.status !== "ok"}
              aria-describedby="image-model-status"
            >
              {imageModels.status !== "ok" && (
                <option value={settings.imageModel}>
                  {imageModels.status === "loading" ? "Loading models…" : settings.imageModel}
                </option>
              )}
              {imageModels.items.map((m) => {
                const price = formatUsd(m.price_usd);
                const usable = imageUsable(m);
                return (
                  <option key={modelId(m)} value={modelId(m)} disabled={!usable}>
                    {m.name || modelId(m)}
                    {price ? ` · ${price}` : ""}
                    {usable ? "" : ` — add ${setup.envFor(m.provider)} to enable`}
                  </option>
                );
              })}
            </Select>
            <div id="image-model-status">
              {imageModels.status === "error" && (
                <FieldError>
                  Couldn&apos;t load image models: {errorMessage(imageModels.error)}. The last selection will be used.
                </FieldError>
              )}
              {imageBlocked && <FieldError>{imageBlocked}</FieldError>}
              {imageModels.status === "ok" && !imageBlocked && (
                <Hint>Prices come from fal.ai&apos;s pricing API; SnapGen and Kie.ai bill in their own credits.</Hint>
              )}
            </div>
          </div>

          <div>
            <Label htmlFor="llm-model" hint="OpenRouter · scene split and image prompts">
              Language model
            </Label>
            <Input
              id="llm-model"
              list="llm-model-options"
              value={settings.aiModel}
              onChange={(e) => set("aiModel")(e.target.value)}
              onFocus={(e) => e.target.select()}
              spellCheck={false}
              autoComplete="off"
              placeholder="Search models, e.g. claude"
              aria-invalid={!!showError("llm") || undefined}
              aria-describedby="llm-status"
              className="font-mono text-xs"
            />
            <datalist id="llm-model-options">
              {llmList.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name || m.id}
                </option>
              ))}
            </datalist>
            <div id="llm-status">
              {selectedLlm && (
                <Hint>
                  {selectedLlm.name}
                  {llmPrice(selectedLlm) ? ` · ${llmPrice(selectedLlm)}` : ""}
                </Hint>
              )}
              {llmModels.status === "loading" && <Hint>Loading models…</Hint>}
              {llmModels.status === "error" && (
                <Hint>Couldn&apos;t load the model list ({errorMessage(llmModels.error)}). Enter an OpenRouter model id.</Hint>
              )}
              <FieldError>{showError("llm")}</FieldError>
            </div>
          </div>

          <Segmented
            name="script-aspect"
            legend="Aspect ratio"
            value={settings.aspectRatio}
            onChange={set("aspectRatio")}
            options={[
              { value: "16:9", label: "16:9" },
              { value: "9:16", label: "9:16" },
            ]}
          />

          <div>
            <p className="mb-1.5 text-sm font-medium text-fg">
              Watermark logo <span className="font-normal text-fg-muted">(optional)</span>
            </p>
            <LogoField logo={logo} onPick={pickLogo} onClear={clearLogo} />
            <FieldError>{logoError}</FieldError>
            {logo && <Hint>The background is removed and the logo is placed in a corner at 35% opacity.</Hint>}
          </div>

          <div className="border-t border-line pt-4">
            <button
              type="button"
              aria-expanded={advancedOpen}
              aria-controls="script-advanced"
              onClick={() => setAdvancedOpen((o) => !o)}
              className="flex items-center gap-1.5 text-sm font-medium text-fg-secondary hover:text-fg"
            >
              {advancedOpen ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Advanced
            </button>
            {advancedOpen && (
              <div id="script-advanced" className="mt-3">
                <Label htmlFor="script-run-name">Run name</Label>
                <Input
                  id="script-run-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Script + date and time"
                  aria-describedby="script-run-name-hint"
                />
                <Hint id="script-run-name-hint">Leave empty to name it after the start time.</Hint>
              </div>
            )}
          </div>

          <div className="border-t border-line pt-4">
            <Estimate query={estimateQuery} />
          </div>

          <div className="border-t border-line pt-4">
            <Button
              variant="primary"
              size="lg"
              className="w-full justify-center"
              disabled={uploading || launching || (submitted && !formValid) || !scriptFeature.ready || !!imageBlocked}
              pending={launching}
              onClick={launch}
            >
              Launch run
            </Button>
            {notReadyReason && <Hint>Launch is disabled: {notReadyReason}</Hint>}
            {uploading && <Hint>Waiting for the logo upload to finish.</Hint>}
          </div>
        </aside>
      </div>
    </>
  );
}
