import { lazy, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Toaster } from "./components/ui/toaster";
import PasswordGate from "./components/PasswordGate";
import AppShell from "./components/AppShell";
import { PipelinesProvider } from "./hooks/usePipelines";
import { SetupProvider } from "./hooks/useSetup";
import { clearToken, getToken, onUnauthorized, setToken } from "./lib/api";
import Queue from "./pages/Queue";
import NotFound from "./pages/NotFound";

// Editors and the run page load on demand to keep the first load small.
const NewPack = lazy(() => import("./pages/NewPack"));
const ScriptStudio = lazy(() => import("./pages/ScriptStudio"));
const RunDetail = lazy(() => import("./pages/RunDetail"));

// Links from before the /run/:id route existed.
function LegacyRunRedirect() {
  const { id } = useParams();
  return <Navigate to={`/run/${id}`} replace />;
}

export default function App() {
  const [token, setTokenState] = useState(getToken);

  useEffect(() => onUnauthorized(() => setTokenState(null)), []);

  const signIn = (value) => {
    setToken(value);
    setTokenState(value);
  };
  const signOut = () => {
    clearToken();
    setTokenState(null);
  };

  return (
    <>
      {token ? (
        <BrowserRouter>
          <PipelinesProvider>
            <SetupProvider>
              <Routes>
                <Route element={<AppShell onSignOut={signOut} />}>
                  <Route index element={<Queue />} />
                  <Route path="new" element={<NewPack />} />
                  <Route path="script" element={<ScriptStudio />} />
                  <Route path="run/:id" element={<RunDetail />} />
                  <Route path="pipeline/:id" element={<LegacyRunRedirect />} />
                  <Route path="*" element={<NotFound />} />
                </Route>
              </Routes>
            </SetupProvider>
          </PipelinesProvider>
        </BrowserRouter>
      ) : (
        <PasswordGate onSignedIn={signIn} />
      )}
      <Toaster />
    </>
  );
}
