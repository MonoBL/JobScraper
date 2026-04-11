import { useState, type FormEvent } from "react";
import { getToken, setToken } from "./api";

type AuthStatus = { login_required: boolean };

export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const d = data.detail;
        throw new Error(typeof d === "string" ? d : "Login failed");
      }
      const token = data.token as string | null | undefined;
      if (data.login_required && token) {
        setToken(token);
        onLoggedIn();
      } else {
        setToken(null);
        onLoggedIn();
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <h1>Job Scraper</h1>
        <p className="login-lead">Enter the access code to open the dashboard.</p>
        <form onSubmit={(e) => void submit(e)}>
          <label className="login-label" htmlFor="access-code">
            Access code
          </label>
          <input
            id="access-code"
            type="password"
            name="access-code"
            autoComplete="current-password"
            className="login-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="••••••••"
          />
          {err && <p className="err login-err">{err}</p>}
          <button type="submit" className="login-submit" disabled={busy || !code.trim()}>
            {busy ? "Signing in…" : "Continue"}
          </button>
        </form>
      </div>
    </div>
  );
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const r = await fetch("/api/auth/status");
  if (!r.ok) return { login_required: true };
  return r.json() as Promise<AuthStatus>;
}

export async function fetchSessionValid(): Promise<boolean> {
  const t = getToken();
  const h: Record<string, string> = {};
  if (t) h.Authorization = `Bearer ${t}`;
  const r = await fetch("/api/auth/session", { headers: h });
  if (!r.ok) return false;
  const d = await r.json();
  return !!(d as { valid?: boolean }).valid;
}
