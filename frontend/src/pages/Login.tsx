import { useState } from "react";
import { api } from "../api";
import { Button, ErrorBox, Input } from "../components/ui";

export default function Login({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [demo, setDemo] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (mode === "login") await api.post("/api/auth/login", { email, password });
      else await api.post("/api/auth/register", { email, password, display_name: name, with_demo: demo });
      onDone();
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="min-h-full flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <svg viewBox="0 0 32 32" className="h-10 w-10 mx-auto"><rect width="32" height="32" rx="7" fill="var(--panel2)" /><path d="M6 22l6-7 5 4 9-11" stroke="var(--accent)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <h1 className="mt-3 text-xl font-semibold">Investment Intelligence</h1>
          <p className="text-sm text-muted">AI portfolio command center</p>
        </div>
        <form onSubmit={submit} className="rounded-xl border border-line bg-panel p-5 space-y-3">
          <Input label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          {mode === "register" && <Input label="Display name" value={name} onChange={(e) => setName(e.target.value)} />}
          <Input label="Password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode === "register" ? 10 : 1}
            value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "register" && (
            <>
              <p className="text-xs text-muted">At least 10 characters, mixing upper/lower case and digits.</p>
              <label className="flex items-start gap-2 text-xs text-muted">
                <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} className="mt-0.5" />
                <span>Load the SAMPLE universe and a demo paper portfolio (synthetic data, clearly labelled) so every module works before data providers are configured.</span>
              </label>
            </>
          )}
          <ErrorBox error={err} />
          <Button type="submit" variant="primary" disabled={busy} className="w-full">{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</Button>
          <button type="button" className="w-full text-xs text-muted hover:text-fg" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "No account yet? Create one" : "Have an account? Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
