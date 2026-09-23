import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../lib/api";
import { isActive, pipelineName } from "../lib/format";
import { usePolling } from "./usePolling";

const PipelinesContext = createContext(null);
const BASE_TITLE = "AI Video Production Hub";

const ACTIVE_INTERVAL = 4000;
const IDLE_INTERVAL = 30000;
const ERROR_INTERVAL = 10000;

/**
 * App-wide pipeline list. Polls fast while anything is active and slowly
 * otherwise, and announces runs that finish while the app is open (toast,
 * title badge while the tab is hidden, and a desktop notification when the
 * user has granted permission).
 */
export function PipelinesProvider({ children }) {
  const navigate = useNavigate();
  const polling = usePolling(
    (signal) => api.listPipelines(signal),
    (data, error) => {
      if (error) return ERROR_INTERVAL;
      return data?.some((p) => isActive(p.status)) ? ACTIVE_INTERVAL : IDLE_INTERVAL;
    },
    "pipelines",
  );

  const previous = useRef(null);
  const [unseen, setUnseen] = useState({ count: 0, failed: false });

  useEffect(() => {
    const list = polling.data;
    if (!list) return;
    const before = previous.current;
    previous.current = new Map(list.map((p) => [p.id, p.status]));
    if (!before) return; // first load: nothing "transitioned"

    for (const p of list) {
      const was = before.get(p.id);
      if (!was || !isActive(was)) continue;
      if (p.status !== "completed" && p.status !== "failed") continue;
      const name = pipelineName(p);
      const failed = p.status === "failed";
      const open = () => navigate(`/run/${p.id}`);

      if (failed) toast.error(`${name} failed`, { action: { label: "Open", onClick: open } });
      else toast.success(`${name} finished`, { action: { label: "Open", onClick: open } });

      if (document.hidden) {
        setUnseen((u) => ({ count: u.count + 1, failed: u.failed || failed }));
      }
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        try {
          const n = new Notification(failed ? "Run failed" : "Run finished", { body: name, tag: p.id });
          n.onclick = () => {
            window.focus();
            open();
          };
        } catch {
          // Some browsers only allow notifications from a service worker.
        }
      }
    }
  }, [polling.data, navigate]);

  useEffect(() => {
    document.title = unseen.count
      ? `(${unseen.count}) ${unseen.failed ? "✕" : "✓"} ${BASE_TITLE}`
      : BASE_TITLE;
  }, [unseen]);

  useEffect(() => {
    const clear = () => {
      if (!document.hidden) setUnseen({ count: 0, failed: false });
    };
    document.addEventListener("visibilitychange", clear);
    return () => document.removeEventListener("visibilitychange", clear);
  }, []);

  return <PipelinesContext.Provider value={polling}>{children}</PipelinesContext.Provider>;
}

export function usePipelines() {
  const ctx = useContext(PipelinesContext);
  if (!ctx) throw new Error("usePipelines must be used inside PipelinesProvider");
  return ctx;
}
