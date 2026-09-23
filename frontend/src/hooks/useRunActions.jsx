import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "../components/ui/dialog";
import RetryDialog from "../components/run/RetryDialog";
import { api, errorMessage } from "../lib/api";
import { pipelineName } from "../lib/format";

// Which actions make sense for a run in a given status.
export function availableActions(status) {
  return {
    pause: status === "running",
    resume: status === "paused",
    retry: status === "failed" || status === "cancelled",
    cancel: ["queued", "pending", "running", "paused"].includes(status),
    delete: status !== "running",
  };
}

const ACTIONS = {
  pause: { call: api.pausePipeline, done: "Pausing. The run stops at the next checkpoint.", verb: "pause" },
  resume: { call: api.retryPipeline, done: "Resumed from the last completed step.", verb: "resume" },
  retry: { call: api.retryPipeline, done: "Retrying from the last completed step.", verb: "retry" },
  cancel: { call: api.cancelPipeline, done: "Run cancelled.", verb: "cancel" },
  delete: { call: api.deletePipeline, done: "Run deleted.", verb: "delete" },
};

const NEEDS_CONFIRM = new Set(["cancel", "delete"]);
// Retry / resume open a dialog that can change the models for the remaining work.
const PICKS_MODELS = new Set(["retry", "resume"]);

/**
 * Run lifecycle actions with confirmation for cancel/delete and a per-run
 * pending state. Render `dialog` somewhere in the component tree.
 */
export function useRunActions({ onChanged, onDeleted } = {}) {
  const [pending, setPending] = useState(null); // { id, action }
  const [confirm, setConfirm] = useState(null); // { action, pipeline }
  const [retry, setRetry] = useState(null); // { action, pipeline }

  const execute = async (action, pipeline, body) => {
    const def = ACTIONS[action];
    setPending({ id: pipeline.id, action });
    try {
      await def.call(pipeline.id, body);
      toast.success(def.done);
      setConfirm(null);
      setRetry(null);
      if (action === "delete") onDeleted?.(pipeline);
      else onChanged?.(pipeline);
    } catch (err) {
      toast.error(`Couldn't ${def.verb} the run: ${errorMessage(err)}`);
    } finally {
      setPending(null);
    }
  };

  const request = (action, pipeline) => {
    if (NEEDS_CONFIRM.has(action)) setConfirm({ action, pipeline });
    else if (PICKS_MODELS.has(action)) setRetry({ action, pipeline });
    else execute(action, pipeline);
  };

  const isPending = (id, action) =>
    !!pending && pending.id === id && (action ? pending.action === action : true);

  const name = confirm ? pipelineName(confirm.pipeline) : "";
  const dialog = (
    <>
      <RetryDialog
        request={retry}
        pending={!!retry && isPending(retry.pipeline.id, retry.action)}
        onClose={() => setRetry(null)}
        onConfirm={(body) => retry && execute(retry.action, retry.pipeline, body)}
      />
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(open) => !open && setConfirm(null)}
        destructive
        pending={!!confirm && isPending(confirm.pipeline.id, confirm.action)}
        title={confirm?.action === "delete" ? "Delete this run?" : "Cancel this run?"}
        confirmLabel={confirm?.action === "delete" ? "Delete run" : "Cancel run"}
        description={
          confirm?.action === "delete" ? (
            <p>
              <span className="font-medium text-fg">{name}</span> and all of its generated images, clips and final video
              will be removed. This can&apos;t be undone.
            </p>
          ) : (
            <p>
              <span className="font-medium text-fg">{name}</span> stops at the next checkpoint. Assets generated so far
              are kept and you can retry the run later.
            </p>
          )
        }
        onConfirm={() => confirm && execute(confirm.action, confirm.pipeline)}
      />
    </>
  );

  return { request, isPending, dialog };
}
