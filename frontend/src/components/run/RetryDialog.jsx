import { useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "../ui/dialog";
import { SelectField } from "../ui/select";
import { Combobox } from "../ui/combobox";
import { useRemoteList } from "../../hooks/useRemoteList";
import { useSetup } from "../../hooks/useSetup";
import { api, errorMessage } from "../../lib/api";
import { pipelineKind, pipelineName } from "../../lib/format";
import {
  CREDITS_NOTE,
  LADDER_HINT,
  durationOptions,
  effectiveVideoModel,
  imageModelOptions,
  resolutionOptions,
  snapVideoSettings,
  videoModelHint,
  videoModelOptions,
} from "../../lib/models";

// The run's current settings. Queue rows are slim, so fetch the detail when needed.
function useRunDetail(pipeline) {
  const hasModels = !!pipeline?.image_model;
  const [detail, setDetail] = useState(hasModels ? pipeline : null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (hasModels || !pipeline) return undefined;
    const controller = new AbortController();
    api
      .getPipeline(pipeline.id, controller.signal)
      .then(setDetail)
      .catch((err) => err?.name !== "AbortError" && setError(err));
    return () => controller.abort();
  }, [pipeline, hasModels]);
  return { detail, error };
}

function ImageModelField({ id, value, onChange, models, setup, label = "Image model" }) {
  const options =
    models.status === "ok"
      ? imageModelOptions(models.items, setup)
      : [{ value, label: models.status === "loading" ? "Loading models…" : value }];
  return (
    <SelectField
      id={id}
      label={label}
      value={value}
      onChange={onChange}
      options={options}
      disabled={models.status !== "ok"}
      error={models.status === "error" ? `Couldn't load image models: ${errorMessage(models.error)}` : null}
      hint={`${LADDER_HINT} ${CREDITS_NOTE}`}
    />
  );
}

function PackSettings({ detail, onChange }) {
  const setup = useSetup();
  const imageModels = useRemoteList(api.getImageModels);
  const videoModels = useRemoteList(api.getVideoModels);
  const isExtend = detail.video_system === "veo_extend";
  const aspect = detail.aspect_ratio || "16:9";
  const [sel, setSel] = useState({
    imageModel: detail.image_model,
    videoModel: detail.video_model,
    videoResolution: detail.video_resolution,
    videoDuration: detail.video_duration,
  });
  const catalog = videoModels.status === "ok" ? videoModels.items : [];
  const model = catalog.length
    ? effectiveVideoModel(catalog, videoModels.defaultId, sel.videoModel, { isExtend, aspect })
    : null;

  useEffect(() => {
    if (model) setSel((s) => snapVideoSettings(s, model, { lockAspect: true }));
  }, [model]);

  useEffect(() => {
    const body = {};
    if (sel.imageModel && sel.imageModel !== detail.image_model) body.image_model = sel.imageModel;
    const videoChanged =
      sel.videoModel !== detail.video_model ||
      (model?.resolutions.length && sel.videoResolution !== detail.video_resolution) ||
      Number(sel.videoDuration) !== Number(detail.video_duration);
    if (videoChanged && model) {
      body.video_model = model.id;
      if (model.resolutions.length) body.video_resolution = sel.videoResolution;
      body.video_duration = Number(sel.videoDuration);
    }
    onChange(body);
  }, [sel, model, detail, onChange]);

  const set = (key) => (value) => setSel((s) => ({ ...s, [key]: value }));
  return (
    <div className="mt-4 space-y-3">
      <ImageModelField
        id="retry-image-model"
        value={sel.imageModel}
        onChange={set("imageModel")}
        models={imageModels}
        setup={setup}
      />
      <SelectField
        id="retry-video-model"
        label="Video model"
        labelHint="SnapGen"
        value={model?.id || sel.videoModel}
        onChange={set("videoModel")}
        options={
          catalog.length
            ? videoModelOptions(catalog, { isExtend, aspect })
            : [{ value: sel.videoModel, label: sel.videoModel }]
        }
        disabled={!catalog.length}
        hint={videoModelHint(model, isExtend ? "extend" : "frame")}
      />
      {model?.resolutions.length > 0 && (
        <SelectField
          id="retry-video-resolution"
          label="Resolution"
          value={sel.videoResolution}
          onChange={set("videoResolution")}
          options={resolutionOptions(model)}
        />
      )}
      <SelectField
        id="retry-video-duration"
        label="Clip length"
        value={String(sel.videoDuration)}
        onChange={(v) => set("videoDuration")(Number(v))}
        options={durationOptions(model, sel.videoDuration)}
      />
    </div>
  );
}

function ScriptSettings({ detail, onChange }) {
  const setup = useSetup();
  const imageModels = useRemoteList(api.getImageModels);
  const llmModels = useRemoteList(api.getLlmModels);
  const voices = useRemoteList(api.getTtsVoices);
  const [sel, setSel] = useState({
    imageModel: detail.image_model || detail.image_gen_model,
    aiModel: detail.ai_model || "",
    voiceId: detail.tts_voice_id || "",
  });

  useEffect(() => {
    const body = {};
    if (sel.imageModel && sel.imageModel !== (detail.image_model || detail.image_gen_model))
      body.image_model = sel.imageModel;
    if (sel.aiModel.trim() && sel.aiModel.trim() !== detail.ai_model) body.ai_model = sel.aiModel.trim();
    if (sel.voiceId && sel.voiceId !== detail.tts_voice_id) body.tts_voice_id = sel.voiceId;
    onChange(body);
  }, [sel, detail, onChange]);

  const llmOptions = useMemo(
    () =>
      llmModels.items.map((m) => ({
        value: m.id,
        label: m.name || m.id,
        detail: m.id,
        group: m.id.includes("/") ? m.id.split("/")[0] : "other",
      })),
    [llmModels.items],
  );
  const voiceOptions = voices.items
    .map((v) => ({ value: v.id || v.voice_id, label: v.name || v.id || v.voice_id }))
    .filter((o) => o.value);
  if (sel.voiceId && !voiceOptions.some((o) => o.value === sel.voiceId)) {
    voiceOptions.unshift({ value: sel.voiceId, label: sel.voiceId });
  }
  const set = (key) => (value) => setSel((s) => ({ ...s, [key]: value }));

  return (
    <div className="mt-4 space-y-3">
      <ImageModelField
        id="retry-image-model"
        value={sel.imageModel}
        onChange={set("imageModel")}
        models={imageModels}
        setup={setup}
      />
      <SelectField
        id="retry-llm-model"
        label="Language model"
        labelHint="Scene split and image prompts"
        hint="Used only if the scene split or image prompts haven't been written yet."
        control={(props) => (
          <Combobox
            {...props}
            value={sel.aiModel}
            onChange={set("aiModel")}
            options={llmOptions}
            allowCustom={llmModels.status !== "ok"}
            placeholder="Search models"
            emptyText="No models match."
          />
        )}
      />
      <SelectField
        id="retry-voice"
        label="Voice"
        value={sel.voiceId}
        onChange={set("voiceId")}
        options={voiceOptions}
        disabled={voices.status !== "ok"}
        hint="Used only if the narration hasn't been generated yet."
      />
    </div>
  );
}

function RetryForm({ pipeline, onChange }) {
  const { detail, error } = useRunDetail(pipeline);
  if (error)
    return <p className="mt-3 text-sm text-status-failed">Couldn&apos;t load the run: {errorMessage(error)}</p>;
  if (!detail) return <p className="mt-3 text-sm text-fg-muted">Loading the run&apos;s current models…</p>;
  return pipelineKind(pipeline) === "script" ? (
    <ScriptSettings detail={detail} onChange={onChange} />
  ) : (
    <PackSettings detail={detail} onChange={onChange} />
  );
}

/**
 * Retry / resume a run, optionally with new models. Prefilled with the run's
 * current selection; only changed values are sent. Completed items are kept.
 * `request`: { action: "retry" | "resume", pipeline } or null.
 */
export default function RetryDialog({ request, pending, onClose, onConfirm }) {
  const [body, setBody] = useState({});
  const pipeline = request?.pipeline || null;
  const resume = request?.action === "resume";
  useEffect(() => setBody({}), [request]);
  const changed = Object.keys(body).length > 0;
  const verb = resume ? "Resume" : "Retry";

  return (
    <ConfirmDialog
      open={!!request}
      onOpenChange={(open) => !open && onClose()}
      pending={pending}
      title={`${verb} this run?`}
      confirmLabel={changed ? `${verb} with these settings` : verb}
      description={
        <>
          <p>
            <span className="font-medium text-fg">{pipeline ? pipelineName(pipeline) : ""}</span> continues from the
            last completed step. Finished images and clips are kept; everything still to do uses the models below.
          </p>
          {pipeline && <RetryForm key={pipeline.id} pipeline={pipeline} onChange={setBody} />}
        </>
      }
      onConfirm={() => onConfirm(changed ? body : undefined)}
    />
  );
}
