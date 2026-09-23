import { useState } from "react";
import { api, errorMessage } from "../lib/api";
import { Button } from "./ui/button";
import { FieldError, Input, Label } from "./ui/field";

export default function PasswordGate({ onSignedIn }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!password) return;
    setPending(true);
    setError("");
    try {
      const res = await api.login(password);
      if (!res?.token) throw new Error("The server didn't return a session token.");
      onSignedIn(res.token);
    } catch (err) {
      if (err.status === 401) setError("Wrong password.");
      else if (err.status === 429) setError(errorMessage(err, "Too many attempts. Wait a minute and try again."));
      else setError(errorMessage(err, "Couldn't sign in."));
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <form onSubmit={submit} className="w-full max-w-xs" noValidate>
        <h1 className="mb-6 text-md font-semibold text-fg">AI Video Production Hub</h1>
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={!!error || undefined}
          aria-describedby={error ? "password-error" : undefined}
        />
        <FieldError id="password-error">{error}</FieldError>
        <Button type="submit" variant="primary" className="mt-4 w-full justify-center" pending={pending} disabled={!password}>
          Sign in
        </Button>
      </form>
    </main>
  );
}
