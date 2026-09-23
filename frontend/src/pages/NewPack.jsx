import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Upload } from "lucide-react";
import PageHeader from "../components/PageHeader";
import PromptList from "../components/PromptList";
import Estimate from "../components/Estimate";
import SetupBanner, { missingReason } from "../components/SetupBanner";
import { Button } from "../components/ui/button";
import { FieldError, Hint, Input, Label, Segmented, Select, Textarea } from "../components/ui/field";
import { usePipelines } from "../hooks/usePipelines";
import { useSetup } from "../hooks/useSetup";
import { api, errorMessage } from "../lib/api";
import { formatDuration } from "../lib/format";
import { IMAGE_PROVIDERS, PROVIDER_LABELS, normalizeProvider } from "../lib/providers";
import {
  SHAPE_INFO, calculateTotalLength, detectMode, framesForVideo, generatePipelineName, parsePromptPack,
} from "../lib/promptPackParser";
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

function readSettings() {
  const saved = readJson(SETTINGS_KEY, DEFAULT_SETTINGS);
  return {
    ...saved,
    firstImageModel: normalizeProvider(saved.firstImageModel),
    subsequentImagesModel: normalizeProvider(saved.subsequentImagesModel),
  };
}

// One <select> of image providers; unconfigured ones stay visible but disabled.
function ProviderSelect({ id, value, onChange, disabled, setup }) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
      {IMAGE_PROVIDERS.map((p) => {
        const ok = setup.configured(p);
        return (
          <option key={p} value={p} disabled={!ok}>
            {PROVIDER_LABELS[p]}
            {ok ? "" : ` — add ${setup.envFor(p)} to enable`}
          </option>
        );
      })}
    </Select>
  );
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
  const [advancedOpen, setAdvancedOpen] = useState(false);
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
    ["first", settings.firstImageModel],
    ...(images.length > 1 ? [["other", settings.subsequentImagesModel]] : []),
  ].filter(([, p]) => !setup.configured(p));

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
      <SetupBanner feature={packFeature} title="Prompt packs" />
      <div className="grid gap-6 px-4 py-5 md:px-6 lg:grid-cols-[minmax(0,1fr)_340px]">
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
        <aside className="self-start space-y-5 rounded border border-line bg-surface p-4 lg:sticky lg:top-4">
          <section aria-labelledby="shape-heading">
            <h2 id="shape-heading" className="text-sm font-medium text-fg">
              Pipeline shape
            </h2>
            {shape ? (
              <>
                <p className="mt-1 text-base text-fg">{SHAPE_INFO[shape].label}</p>
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
              </>
            ) : (
              <p className="mt-1 text-sm text-fg-muted">
                {source.trim() ? "Fix the pack to see its shape." : "Paste a pack to detect its shape."}
              </p>
            )}
          </section>

          <Segmented
            name="aspect"
            legend="Aspect ratio"
            value={settings.aspectRatio}
            onChange={set("aspectRatio")}
            options={[
              { value: "16:9", label: "16:9" },
              { value: "9:16", label: "9:16" },
            ]}
          />
          {settings.aspectRatio === "9:16" && usesVeo && (
            <Hint className="-mt-3 text-fg-secondary">
              Veo 3.1 is documented as 16:9 only; portrait may be rejected.
              {isExtend ? " Grok supports portrait." : ""}
            </Hint>
          )}

          {isExtend ? (
            <>
              <Segmented
                name="engine"
                legend="Video engine"
                value={settings.videoEngine}
                onChange={set("videoEngine")}
                options={[
                  { value: "grok", label: "Grok", detail: "720p" },
                  { value: "veo", label: "Veo 3.1 Fast", detail: "1080p" },
                ]}
              />
              {settings.videoEngine === "grok" ? (
                <Segmented
                  name="shot"
                  legend="Clip length"
                  value={String(settings.shotDuration)}
                  onChange={(v) => set("shotDuration")(Number(v))}
                  options={[6, 10, 15].map((d) => ({ value: String(d), label: `${d}s` }))}
                />
              ) : (
                <Hint className="mt-0">Veo clips are 8 seconds each.</Hint>
              )}
            </>
          ) : (
            mode === "frame" && (
              <div>
                <p className="text-sm font-medium text-fg">Video engine</p>
                <p className="mt-0.5 text-sm text-fg-secondary">Veo 3.1, first → last frame, 8s per clip</p>
              </div>
            )
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="first-image-model">First image</Label>
              <ProviderSelect
                id="first-image-model"
                value={settings.firstImageModel}
                onChange={set("firstImageModel")}
                setup={setup}
              />
            </div>
            <div>
              <Label htmlFor="subsequent-image-model">Other images</Label>
              <ProviderSelect
                id="subsequent-image-model"
                value={settings.subsequentImagesModel}
                onChange={set("subsequentImagesModel")}
                disabled={images.length === 1}
                setup={setup}
              />
            </div>
          </div>
          {unconfiguredSlots.length > 0 ? (
            <Hint className="-mt-3">
              {unconfiguredSlots
                .map(([slot, p]) => `${PROVIDER_LABELS[p]} (${slot} image) isn't configured — add ${setup.envFor(p)}`)
                .join("; ")}
              . Those images fall back to the next configured provider.
            </Hint>
          ) : (
            <Hint className="-mt-3">If a provider fails, images fall back SnapGen → Kie.ai → fal.ai.</Hint>
          )}

          <div className="border-t border-line pt-4">
            <button
              type="button"
              aria-expanded={advancedOpen}
              aria-controls="pack-advanced"
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
              <div id="pack-advanced" className="mt-3">
                <Label htmlFor="run-name">Run name</Label>
                <Input
                  id="run-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={autoName || "Generated from the first image prompt"}
                  aria-describedby="run-name-hint"
                />
                <Hint id="run-name-hint">Leave empty to use the generated name.</Hint>
              </div>
            )}
          </div>

          <div className="border-t border-line pt-4">
            <Estimate query={estimateQuery} />
          </div>

          <div className="border-t border-line pt-4">
            <Button variant="primary" size="lg" className="w-full justify-center" disabled={!canLaunch} pending={launching} onClick={launch}>
              Launch run
            </Button>
            {notReadyReason ? (
              <Hint>Launch is disabled: {notReadyReason}</Hint>
            ) : (
              finalName && mode && <p className="mt-2 truncate text-xs text-fg-muted" title={finalName}>{finalName}</p>
            )}
          </div>
        </aside>
      </div>
    </>
  );
}
