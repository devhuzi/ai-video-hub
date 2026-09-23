import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../lib/api";
import { PROVIDER_ENV } from "../lib/providers";

const SetupContext = createContext(null);

/**
 * Server configuration status from /api/setup, fetched once per session.
 * While loading (or if the request fails) everything is treated as usable so
 * the forms never lock up on a transient error — the create endpoints still
 * reject missing keys with a clear message.
 */
export function SetupProvider({ children }) {
  const [state, setState] = useState({ status: "loading", data: null });

  useEffect(() => {
    const controller = new AbortController();
    api
      .getSetup(controller.signal)
      .then((data) => setState({ status: "ok", data }))
      .catch((error) => {
        if (error?.name !== "AbortError") setState({ status: "error", data: null });
      });
    return () => controller.abort();
  }, []);

  return <SetupContext.Provider value={state}>{children}</SetupContext.Provider>;
}

export function useSetup() {
  const state = useContext(SetupContext);
  if (!state) throw new Error("useSetup must be used inside SetupProvider");
  const { data } = state;
  const envVars = { ...PROVIDER_ENV, ...(data?.env_vars || {}) };
  return {
    loaded: state.status === "ok",
    // Unknown until loaded: assume configured.
    configured: (provider) => (data ? !!data.providers?.[provider] : true),
    envFor: (provider) => envVars[provider] || provider,
    feature: (name) => data?.features?.[name] || { ready: true, missing: [] },
  };
}
