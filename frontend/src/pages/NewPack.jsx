import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Upload } from "lucide-react";
import PageHeader from "../components/PageHeader";
import PromptList from "../components/PromptList";
import Estimate from "../components/Estimate";
import SetupBanner, { missingReason } from "../components/SetupBanner";
import { PanelSection, SettingsPanel } from "../components/SettingsPanel";
import { Button } from "../components/ui/button";
import { FieldError, Hint, Input, Label, Textarea } from "../components/ui/field";
import { SelectField } from "../components/ui/select";
import { usePipelines } from "../hooks/usePipelines";
import { useSetup } from "../hooks/useSetup";
import { api, errorMessage } from "../lib/api";
import { formatDuration } from "../lib/format";
import { IMAGE_PROVIDERS, PROVIDER_LABELS, normalizeProvider } from "../lib/providers";
import {
  SHAPE_INFO, calculateTotalLength, detectMode, framesForVideo, generatePipelineName, parsePromptPack,
} from "../lib/promptPackParser";
import { CONTAINER } from "../lib/layout";
import { ASPECT_OPTIONS, oneOf } from "../lib/options";
import { readJson, writeJson } from "../lib/storage";
import { cn } from "../lib/utils";

const SETTINGS_KEY = "aivph.packSettings";
const DEFAULT_SETTINGS = {
  aspectRatio: "16:9",
  videoEngine: "grok",
  shotDuration: 6,
  firstImageModel: "snapgen",
  subsequentImagesModel: "snapgen",
};

const ENGINE_OPTIONS = [
  { value: "grok", label: "Grok · 720p" },
  { value: "veo", label: "Veo 3.1 Fast · 1080p" },
];
// Frame packs have no engine choice; shown as a locked select for clarity.
const FRAME_ENGINE_OPTIONS = [{ value: "veo31_frame", label: "Veo 3.1 · first → last frame" }];
const DURATION_OPTIONS = [6, 10, 15].map((d) => ({ value: String(d), label: `${d} seconds` }));

// Saved settings may come from an older version; drop values that no longer exist.
function readSettings() {
  const saved = readJson(SETTINGS_KEY, DEFAULT_SETTINGS);
  return {
    aspectRatio: oneOf(saved.aspectRatio, ASPECT_OPTIONS, DEFAULT_SETTINGS.aspectRatio),
    videoEngine: oneOf(saved.videoEngine, ENGINE_OPTIONS, DEFAULT_SETTINGS.videoEngine),
    shotDuration: Number(oneOf(saved.shotDuration, DURATION_OPTIONS, DEFAULT_SETTINGS.shotDuration)),
    firstImageModel: normalizeProvider(saved.firstImageModel),
    subsequentImagesModel: normalizeProvider(saved.subsequentImagesModel),
  };
}

// Unconfigured providers stay visible but disabled, with the env var to add.
function providerOptions(setup) {
  return IMAGE_PROVIDERS.map((p) => {
    const ok = setup.configured(p);
    return { value: p, label: PROVIDER_LABELS[p], disabled: !ok, suffix: ok ? null : `add ${setup.envFor(p)}` };
  });
}

const ACCEPTED_EXT = [".txt", ".md", ".json"];
const MAX_FILE_BYTES = 2 * 1024 * 1024;

export default function NewPack() {
  const navigate = useNavigate();
  const { refresh: refreshQueue } = usePipelines();
  const setup = useSetup();
  const packFeature = setup.feature("prompt_packs");
  const notReadyReason = missingReason(packFeature);
  const fileInput = useRef(null);

  const [source, setSource] = useState("");
  const [edited, setEdited] = useState(null); // {images, videos} once rows are edited
  const [fileError, setFileError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [settings, setSettings] = useState(readSettings);
  const [name, setName] = useState("");
  const [launching, setLaunching] = useState(false);

  useEffect(() => writeJson(SETTINGS_KEY, settings), [settings]);
  const set = (key) => (value) => setSettings((s) => ({ ...s, [key]: value }));

  const parsed = useMemo(() => (source.trim() ? parsePromptPack(source) : null), [source]);
  const images = useMemo(() => (edited ? edited.images : parsed?.imagePrompts || []), [edited, parsed]);
  const videos = useMemo(() => (edited ? edited.videos : parsed?.videoPrompts || []), [edited, parsed]);
  const hasPrompts = images.length > 0 || videos.length > 0;

  const detection = useMemo(
    () => (hasPrompts ? detectMode(images, videos) : null),
    [hasPrompts, images, videos],
  );
  const emptyIndex = images.findIndex((p) => !p.trim());
  const emptyVideoIndex = videos.findIndex((p) => !p.trim());

  let packError = null;
  if (parsed && !hasPrompts) packError = parsed.error;
  else if (detection?.error) packError = detection.error;
  else if (emptyIndex >= 0) packError = `Image ${emptyIndex + 1} is empty.`;
  else if (emptyVideoIndex >= 0) packError = `Video ${emptyVideoIndex + 1} is empty.`;

  const mode = packError ? null : detection?.mode;
  const shape = packError ? null : detection?.shape;
  const isExtend = mode === "extend";
  const clipSeconds = isExtend ? (settings.videoEngine === "veo" ? 8 : settings.shotDuration) : 8;
  const totalSeconds = mode ? calculateTotalLength(mode, videos.length, settings.videoEngine, settings.shotDuration) : 0;

  const firstImage = images[0] || "";
  const autoName = useMemo(() => (firstImage ? generatePipelineName([firstImage]) : ""), [firstImage]);
  const finalName = name.trim() || autoName;

  const updateSource = (text) => {
    setSource(text);
    setEdited(null);
  };

  const loadFile = async (file) => {
    setFileError("");
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (!ACCEPTED_EXT.some((ext) => lower.endsWith(ext))) {
      setFileError(`${file.name} isn't a .txt, .md or .json file.`);
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setFileError(`${file.name} is larger than 2 MB.`);
      return;
    }
    try {
      updateSource(await file.text());
    } catch {
      setFileError(`Couldn't read ${file.name}.`);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    loadFile(e.dataTransfer.files?.[0]);
  };

  const describeVideo = (i) => {
    if (!shape) return null;
    const [a, b] = framesForVideo(shape, i, images.length);
    if (shape === "extend") return i === 0 ? "Extends from image 1" : `Extends clip ${i}`;
    if (a == null) return null;
    if (a === b) return `Holds on image ${a + 1}`;
    return `Images ${a + 1} → ${b + 1}`;
  };

  const canLaunch = !!mode && !!finalName && !launching && packFeature.ready;
  const usesVeo = mode === "frame" || (isExtend && settings.videoEngine === "veo");
  const unconfiguredSlots = [
    ["the first image uses", settings.firstImageModel],
    ...(images.length > 1 ? [["the other images use", settings.subsequentImagesModel]] : []),
  ].filter(([, p]) => !setup.configured(p));
  const modelsHint = unconfiguredSlots.length
    ? unconfiguredSlots
        .map(([slot, p]) => `${PROVIDER_LABELS[p]} isn't configured (add ${setup.envFor(p)}), so ${slot} the next configured provider.`)
        .join(" ")
    : "If a provider fails, images fall back in this order: SnapGen, Kie.ai, fal.ai.";
  const aspectHint =
    settings.aspectRatio === "9:16" && usesVeo
      ? `Veo 3.1 is documented as 16:9 only, so portrait may be rejected.${isExtend ? " Grok supports portrait." : ""}`
      : null;
  let engineHint = null;
  if (mode === "frame") engineHint = "Frame packs always use Veo 3.1, 8 seconds per clip.";
  else if (!mode) engineHint = "Used by extend packs (one image). Frame packs always use Veo 3.1.";
  else if (settings.videoEngine === "veo") engineHint = "Veo clips are 8 seconds each.";
  const providers = providerOptions(setup);

  const launch = async () => {
    if (!canLaunch) return;
    setLaunching(true);
    try {
      const res = await api.createPipeline({
        pipeline_name: finalName,
        image_prompts: images.map((p) => p.trim()),
        video_prompts: videos.map((p) => p.trim()),
        video_system: detection.videoSystem,
        aspect_ratio: settings.aspectRatio,
        first_image_model: settings.firstImageModel,
        subsequent_images_model: settings.subsequentImagesModel,
        shot_duration: isExtend ? settings.shotDuration : 6,
        video_engine: isExtend ? settings.videoEngine : "grok",
      });
      toast.success("Run started.");
      refreshQueue();
      navigate(`/run/${res.id}`);
    } catch (err) {
      toast.error(`Couldn't start the run: ${errorMessage(err)}`);
      setLaunching(false);
    }
  };

  const estimateQuery = mode
    ? {
        kind: "pack",
        num_images: images.length,
        num_videos: videos.length,
        video_system: detection.videoSystem,
        first_image_model: settings.firstImageModel,
        subsequent_images_model: images.length > 1 ? settings.subsequentImagesModel : settings.firstImageModel,
        video_engine: isExtend ? settings.videoEngine : undefined,
        aspect_ratio: settings.aspectRatio,
      }
    : null;

  return (
    <>
      <PageHeader title="New prompt pack" meta="Paste or drop image and video prompts. The pipeline shape is detected from the counts." />
      <SetupBanner feature={packFeature} lead="prompt packs need" />
      <div className={cn(CONTAINER.editor, "grid gap-6 py-5 lg:grid-cols-[minmax(0,1fr)_340px]")}>
        {/* Left: source + parsed rows */}
        <div className="min-w-0 space-y-6">
          <div>
            <Label htmlFor="pack-source" hint="JSON, labelled text or numbered lists">
              Prompt pack
            </Label>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={cn("rounded", dragging && "outline outline-2 outline-ring")}
            >
              <Textarea
                id="pack-source"
                value={source}
                onChange={(e) => updateSource(e.target.value)}
                rows={12}
                spellCheck={false}
                aria-invalid={!!packError || undefined}
                aria-describedby="pack-source-status"
                placeholder={'{"image_prompts": ["…"], "video_prompts": ["…"]}\n\nor\n\nImage 1: …\nImage 2: …\nVideo 1: …'}
                className="resize-y font-mono text-xs leading-5"
              />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => fileInput.current?.click()}>
                <Upload className="h-3.5 w-3.5" aria-hidden="true" />
                Open file
              </Button>
              <span className="text-xs text-fg-muted">or drop a .txt, .md or .json file on the text box</span>
              <input
                ref={fileInput}
                type="file"
                accept={ACCEPTED_EXT.join(",")}
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(e) => {
                  loadFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </div>
            <div id="pack-source-status">
              <FieldError>{fileError}</FieldError>
              <FieldError>{source.trim() ? packError : null}</FieldError>
              {edited && (
                <Hint>Rows below have been edited. Changing the text above re-parses it and discards those edits.</Hint>
              )}
            </div>
          </div>

          {hasPrompts && (
            <div className="space-y-5">
              <PromptList
                title="Image prompts"
                noun="Image"
                items={images}
                onChange={(next) => setEdited({ images: next, videos })}
              />
              <PromptList
                title="Video prompts"
                noun="Video"
                items={videos}
                describe={describeVideo}
                onChange={(next) => setEdited({ images, videos: next })}
              />
            </div>
          )}
        </div>

        {/* Right: settings + launch */}
        <SettingsPanel label="Run settings">
          <PanelSection title="Pipeline shape">
            {shape ? (
              <div>
                <p className="text-base text-fg">{SHAPE_INFO[shape].label}</p>
                <p className="mt-1 text-sm text-fg-secondary">{SHAPE_INFO[shape].description}</p>
                <dl className="mt-3 grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <dt className="text-xs text-fg-muted">Images</dt>
                    <dd className="font-mono text-fg tabular">{images.length}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-fg-muted">Videos</dt>
                    <dd className="font-mono text-fg tabular">{videos.length}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-fg-muted">Runtime</dt>
                    <dd className="font-mono text-fg tabular" title={`${videos.length} × ${clipSeconds}s`}>
                      {formatDuration(totalSeconds)}
                    </dd>
                  </div>
                </dl>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">
                {source.trim() ? "Fix the pack to see its shape." : "Paste a pack to detect its shape."}
              </p>
            )}
          </PanelSection>

          <PanelSection title="Output">
            <SelectField
              id="aspect-ratio"
              label="Aspect ratio"
              value={settings.aspectRatio}
              onChange={set("aspectRatio")}
              options={ASPECT_OPTIONS}
              hint={aspectHint}
            />
            <SelectField
              id="video-engine"
              label="Video engine"
              value={mode === "frame" ? FRAME_ENGINE_OPTIONS[0].value : settings.videoEngine}
              onChange={set("videoEngine")}
              options={mode === "frame" ? FRAME_ENGINE_OPTIONS : ENGINE_OPTIONS}
              disabled={mode === "frame"}
              hint={engineHint}
            />
            {isExtend && settings.videoEngine === "grok" && (
              <SelectField
                id="shot-duration"
                label="Clip length"
                value={String(settings.shotDuration)}
                onChange={(v) => set("shotDuration")(Number(v))}
                options={DURATION_OPTIONS}
              />
            )}
          </PanelSection>

          <PanelSection title="Models">
            <SelectField
              id="first-image-model"
              label="First image"
              value={settings.firstImageModel}
              onChange={set("firstImageModel")}
              options={providers}
            />
            <SelectField
              id="subsequent-image-model"
              label="Other images"
              value={settings.subsequentImagesModel}
              onChange={set("subsequentImagesModel")}
              options={providers}
              disabled={images.length === 1}
              hint={modelsHint}
            />
          </PanelSection>

          <PanelSection>
            <Estimate query={estimateQuery} />
          </PanelSection>

          <PanelSection>
            <div>
              <Label htmlFor="run-name" hint="Optional">
                Run name
              </Label>
              <Input
                id="run-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={autoName || "Generated from the first image prompt"}
              />
            </div>
            <Button variant="primary" size="lg" className="w-full justify-center" disabled={!canLaunch} pending={launching} onClick={launch}>
              Launch run
            </Button>
            {notReadyReason && <Hint className="mt-0">{notReadyReason}</Hint>}
          </PanelSection>
        </SettingsPanel>
      </div>
    </>
  );
}
