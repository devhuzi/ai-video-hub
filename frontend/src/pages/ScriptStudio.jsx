import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Upload, X } from "lucide-react";
import PageHeader from "../components/PageHeader";
import Estimate from "../components/Estimate";
import SetupBanner, { missingReason } from "../components/SetupBanner";
import { PanelSection, SettingsPanel } from "../components/SettingsPanel";
import { Button } from "../components/ui/button";
import { FieldError, Hint, Input, Label, Textarea } from "../components/ui/field";
import { SelectField } from "../components/ui/select";
import { Combobox } from "../components/ui/combobox";
import Balances from "../components/Balances";
import { usePipelines } from "../hooks/usePipelines";
import { useRemoteList } from "../hooks/useRemoteList";
import { useSetup } from "../hooks/useSetup";
import { api, errorMessage } from "../lib/api";
import { formatDuration } from "../lib/format";
import { formatUsd, providerLabel } from "../lib/providers";
import { CONTAINER } from "../lib/layout";
import { ASPECT_OPTIONS, oneOf } from "../lib/options";
import { CREDITS_NOTE, LADDER_HINT, imageModelOptions } from "../lib/models";
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

const STYLE_OPTIONS = STYLE_PRESETS.map((p) => ({ value: p.id, label: p.label }));

// OpenRouter prices are USD per million tokens (input / output).
function llmPrice(model) {
  const inp = formatUsd(model?.prompt_price);
  const out = formatUsd(model?.completion_price);
  return inp && out ? `${inp} in / ${out} out per 1M tokens` : null;
}

// Compact price for the model list: "$3.00 / $15.00", or "Free".
function llmPriceShort(model) {
  if (model?.prompt_price === 0 && model?.completion_price === 0) return "Free";
  const inp = formatUsd(model?.prompt_price);
  const out = formatUsd(model?.completion_price);
  return inp && out ? `${inp} / ${out}` : null;
}

// Saved settings may come from an older version; drop values that no longer exist.
function readSettings() {
  const saved = readJson(SETTINGS_KEY, DEFAULT_SETTINGS);
  return {
    ...saved,
    aspectRatio: oneOf(saved.aspectRatio, ASPECT_OPTIONS, DEFAULT_SETTINGS.aspectRatio),
    stylePreset: oneOf(saved.stylePreset, STYLE_OPTIONS, DEFAULT_SETTINGS.stylePreset),
    customStyle: typeof saved.customStyle === "string" ? saved.customStyle : "",
    aiModel: typeof saved.aiModel === "string" ? saved.aiModel : DEFAULT_SETTINGS.aiModel,
  };
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
  const [settings, setSettings] = useState(readSettings);
  const [name, setName] = useState("");
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
  // A saved voice that's no longer offered falls back to the default voice (or the first one).
  useEffect(() => {
    const { status, items } = voices;
    const ids = items.map(voiceId).filter(Boolean);
    if (status !== "ok" || !ids.length || ids.includes(settings.voiceId)) return;
    const next = ids.includes(DEFAULT_SETTINGS.voiceId) ? DEFAULT_SETTINGS.voiceId : ids[0];
    setSettings((s) => ({ ...s, voiceId: next }));
  }, [voices, settings.voiceId]);
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
      ? `${providerLabel(imageProvider)} isn't configured. Add ${setup.envFor(imageProvider)} or choose another model.`
      : null;
  const errors = {
    script: !script.trim() ? "Paste a script." : null,
    voice: !voiceValid ? "Choose a voice." : null,
    style: isCustom && !resolvedStyle ? "Describe the custom style." : null,
    llm: !settings.aiModel.trim()
      ? "Choose a language model."
      : llmLoaded && !selectedLlm
        ? "Choose a model from the list."
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

  let voicePlaceholder = null;
  if (voices.status === "loading") voicePlaceholder = "Loading voices…";
  else if (voices.status === "error") voicePlaceholder = "Voices unavailable";
  else if (voiceList.length === 0) voicePlaceholder = "No voices returned";
  const voiceOptions = voiceList.map((v) => ({
    value: voiceId(v),
    label: v.description ? `${voiceName(v)} · ${v.description}` : voiceName(v),
  }));

  const imageOptions =
    imageModels.status === "ok"
      ? imageModelOptions(imageModels.items, setup)
      : [{ value: settings.imageModel, label: imageModels.status === "loading" ? "Loading models…" : settings.imageModel }];

  const llmOptions = llmList.map((m) => ({
    value: m.id,
    label: m.name || m.id,
    detail: m.id,
    meta: llmPriceShort(m),
    group: m.id.includes("/") ? m.id.split("/")[0] : "other",
  }));

  return (
    <>
      <PageHeader
        title="Script Studio"
        meta="Narrates a script, splits it into scenes, generates one image per scene and renders a subtitled video."
      />
      <SetupBanner feature={scriptFeature} lead="Script Studio needs" />
      <div className={cn(CONTAINER.editor, "grid gap-6 py-5 lg:grid-cols-[minmax(0,1fr)_340px]")}>
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

        <SettingsPanel label="Run settings">
          <PanelSection title="Output">
            <SelectField
              id="aspect-ratio"
              label="Aspect ratio"
              value={settings.aspectRatio}
              onChange={set("aspectRatio")}
              options={ASPECT_OPTIONS}
            />
          </PanelSection>

          <PanelSection title="Models">
            <SelectField
              id="image-model"
              label="Image model"
              value={settings.imageModel}
              onChange={set("imageModel")}
              options={imageOptions}
              disabled={imageModels.status !== "ok"}
              error={
                imageBlocked ||
                (imageModels.status === "error"
                  ? `Couldn't load image models: ${errorMessage(imageModels.error)}. The last selection will be used.`
                  : null)
              }
              hint={
                imageModels.status === "ok" && !imageBlocked
                  ? `fal.ai prices come from its pricing API. ${CREDITS_NOTE} ${LADDER_HINT}`
                  : null
              }
            />

            <SelectField
              id="llm-model"
              label="Language model"
              labelHint="Scene split and image prompts"
              error={showError("llm")}
              hint={
                llmModels.status === "loading"
                  ? "Loading models…"
                  : llmModels.status === "error"
                    ? `Couldn't load the model list (${errorMessage(llmModels.error)}). Type an OpenRouter model id.`
                    : selectedLlm
                      ? `${selectedLlm.id}${llmPrice(selectedLlm) ? ` · ${llmPrice(selectedLlm)}` : ""}`
                      : null
              }
              control={(props) => (
                <Combobox
                  {...props}
                  value={settings.aiModel}
                  onChange={set("aiModel")}
                  options={llmOptions}
                  allowCustom={!llmLoaded}
                  placeholder="Search models, e.g. claude"
                  emptyText="No models match."
                />
              )}
            />

            <div>
              <SelectField
                id="voice"
                label="Voice"
                labelHint={`ElevenLabs Multilingual v2${voices.provider ? ` via ${providerLabel(voices.provider)}` : ""}`}
                value={voiceValid ? settings.voiceId : ""}
                onChange={set("voiceId")}
                options={voiceOptions}
                placeholder={voicePlaceholder}
                disabled={voices.status !== "ok" || voiceList.length === 0}
                error={
                  voices.status === "error"
                    ? `Couldn't load voices: ${errorMessage(voices.error)}`
                    : voices.status === "ok"
                      ? showError("voice")
                      : null
                }
              />
              {voices.status === "error" && (
                <Button size="sm" variant="ghost" className="-ml-2 mt-1" onClick={voices.reload}>
                  Try again
                </Button>
              )}
            </div>
          </PanelSection>

          <PanelSection title="Style">
            <SelectField
              id="style-preset"
              label="Visual style"
              value={preset.id}
              onChange={set("stylePreset")}
              options={STYLE_OPTIONS}
              hint={isCustom ? null : `Added to every scene image prompt: ${preset.value}`}
            />
            {isCustom && (
              <div>
                <Label htmlFor="custom-style">Custom style</Label>
                <Input
                  id="custom-style"
                  value={settings.customStyle}
                  onChange={(e) => set("customStyle")(e.target.value)}
                  placeholder="e.g. vintage 1970s film photograph, warm faded tones"
                  aria-invalid={!!showError("style") || undefined}
                  aria-describedby="custom-style-error custom-style-hint"
                />
                <FieldError id="custom-style-error">{showError("style")}</FieldError>
                <Hint id="custom-style-hint">Added to every scene image prompt.</Hint>
              </div>
            )}
            <div>
              <p className="mb-1.5 text-sm font-medium text-fg">
                Watermark logo <span className="font-normal text-fg-muted">(optional)</span>
              </p>
              <LogoField logo={logo} onPick={pickLogo} onClear={clearLogo} />
              <FieldError>{logoError}</FieldError>
              {logo && <Hint>The background is removed and the logo is placed in a corner at 35% opacity.</Hint>}
            </div>
          </PanelSection>

          <PanelSection>
            <Estimate query={estimateQuery} />
            <Balances />
          </PanelSection>

          <PanelSection>
            <div>
              <Label htmlFor="script-run-name" hint="Optional">
                Run name
              </Label>
              <Input
                id="script-run-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Script + start date and time"
              />
            </div>
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
            {notReadyReason && <Hint className="mt-0">{notReadyReason}</Hint>}
            {uploading && <Hint className="mt-0">Waiting for the logo upload to finish.</Hint>}
          </PanelSection>
        </SettingsPanel>
      </div>
    </>
  );
}
