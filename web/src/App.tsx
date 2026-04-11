import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiFetch, setToken } from "./api";
import Login, { fetchAuthStatus, fetchSessionValid } from "./Login";

type JobRow = {
  title: string;
  company: string;
  url: string;
  description: string;
  source: string;
  priority: string;
  priority_reason: string;
  posted_date?: string | null;
  category?: string;
};

type DateSummary = {
  date: string;
  count: number;
  perfect: number;
  good: number;
  weak: number;
};

type AgentMeta = {
  id: string;
  label: string;
  description: string;
  model: string;
};

type AgentResultEntry = {
  data?: Record<string, unknown>;
  error?: string;
};

const FALLBACK_AGENTS: AgentMeta[] = [
  {
    id: "fit",
    label: "Role fit",
    description: "Scores vs DevOps / infra / platform targets.",
    model: "",
  },
  {
    id: "critique",
    label: "Posting critique",
    description: "Skeptical read of the listing.",
    model: "",
  },
  {
    id: "checklist",
    label: "Before you apply",
    description: "Prep and questions before applying.",
    model: "",
  },
];

function renderValue(v: unknown): ReactNode {
  if (v === null || v === undefined) return null;
  if (Array.isArray(v)) {
    return (
      <ul>
        {v.map((x, i) => (
          <li key={i}>{String(x)}</li>
        ))}
      </ul>
    );
  }
  if (typeof v === "object") {
    return (
      <pre style={{ margin: "0.35rem 0 0", fontSize: "0.78rem", overflow: "auto" }}>
        {JSON.stringify(v, null, 2)}
      </pre>
    );
  }
  return <span>{String(v)}</span>;
}

function AgentPayload({
  agentId,
  data,
}: {
  agentId: string;
  data: Record<string, unknown>;
}) {
  if (agentId === "fit" && typeof data.score === "number") {
    const fit = typeof data.fit === "string" ? data.fit : "";
    return (
      <>
        <div className="score">
          Score: {data.score}/10{fit ? ` — ${fit}` : ""}
        </div>
        {data.strengths && Array.isArray(data.strengths) && data.strengths.length > 0 && (
          <div>
            <strong>Strengths</strong>
            {renderValue(data.strengths)}
          </div>
        )}
        {data.concerns && Array.isArray(data.concerns) && data.concerns.length > 0 && (
          <div>
            <strong>Concerns</strong>
            {renderValue(data.concerns)}
          </div>
        )}
      </>
    );
  }

  const skip = new Set(["strengths", "concerns", "fit", "score"]);
  const entries = Object.entries(data).filter(([k]) => !skip.has(k));

  return (
    <>
      {entries.map(([k, v]) => (
        <div key={k} className="agent-kv">
          <strong>{k.replace(/_/g, " ")}:</strong>
          {renderValue(v)}
        </div>
      ))}
    </>
  );
}

const PRI_ORDER: Record<string, number> = {
  PERFECT_MATCH: 0,
  GOOD_MATCH: 1,
  WEAK_MATCH: 2,
};

/** Status lines while a manual scrape runs (scrapers + ranker; AI agents run on job cards after). */
const SCRAPE_PHASE_MESSAGES = [
  "Starting Playwright & scrapers…",
  "Crypto / Web3 job boards…",
  "Cruise & maritime sources…",
  "General & remote listings…",
  "Ranking with JobRanker…",
  "Saving history & Discord…",
  "AI agents (fit / critique / checklist) work on cards next…",
];

function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m < 60) return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm > 0 ? `${h}h ${mm}m` : `${h}h`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseISODate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export default function App() {
  const [authPhase, setAuthPhase] = useState<"loading" | "login" | "ready">("loading");
  const [requireLogin, setRequireLogin] = useState(false);
  const [monthCursor, setMonthCursor] = useState(() => startOfMonth(new Date()));
  const [selectedDate, setSelectedDate] = useState(() => isoDate(new Date()));
  const [summaries, setSummaries] = useState<DateSummary[]>([]);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [jobsErr, setJobsErr] = useState<string | null>(null);
  const [health, setHealth] = useState<{
    ok?: boolean;
    service?: string;
    agent?: boolean;
    agents_enabled?: boolean;
    agents?: AgentMeta[];
    login_required?: boolean;
    llm_agent_count?: number;
  }>({});
  type PillState = "loading" | "ok" | "warn" | "error";
  const [dashStatus, setDashStatus] = useState<{
    api: PillState;
    llm: PillState;
    jobs: PillState;
    schedule: PillState;
    profile: PillState;
    auth: PillState;
  }>({
    api: "loading",
    llm: "loading",
    jobs: "loading",
    schedule: "loading",
    profile: "loading",
    auth: "loading",
  });
  const [statusCheckedAt, setStatusCheckedAt] = useState<Date | null>(null);
  const [statusHints, setStatusHints] = useState<Record<string, string>>({});
  const [discordNotificationsEnabled, setDiscordNotificationsEnabled] = useState<boolean | null>(null);
  const [discordToggleSaving, setDiscordToggleSaving] = useState(false);
  const [agentDiagnostics, setAgentDiagnostics] = useState<{
    configured?: boolean;
    provider?: string | null;
    base_url?: string | null;
    agents?: Array<{
      id: string;
      label: string;
      model: string;
      model_source: string;
      env_key: string;
    }>;
    resume_profile?: {
      id: string;
      label: string;
      model: string;
      model_source: string;
      env_key: string;
    } | null;
  } | null>(null);
  const [agentTestResults, setAgentTestResults] = useState<{
    overall_ok?: boolean;
    tested_at?: string;
    results?: Array<{
      id: string;
      name: string;
      model: string;
      ok: boolean;
      latency_ms?: number;
      error?: string;
      response_preview?: string;
    }>;
    error?: string;
  } | null>(null);
  const [agentTestLoading, setAgentTestLoading] = useState(false);
  const [agents, setAgents] = useState<AgentMeta[]>([]);
  const [agentBusy, setAgentBusy] = useState<string | null>(null);
  const [agentResults, setAgentResults] = useState<Record<string, Record<string, AgentResultEntry>>>(
    {}
  );
  const [expandedDesc, setExpandedDesc] = useState<Record<string, boolean>>({});
  const [scrapeBusy, setScrapeBusy] = useState(false);
  const [scrapeMsg, setScrapeMsg] = useState<string | null>(null);
  const [scrapePolling, setScrapePolling] = useState(false);
  const [scrapePhaseIdx, setScrapePhaseIdx] = useState(0);
  const [nextRunAtMs, setNextRunAtMs] = useState<number | null>(null);
  const [scheduleTimeLabel, setScheduleTimeLabel] = useState("09:00");
  const [countdownSec, setCountdownSec] = useState(0);
  const [scheduleLoaded, setScheduleLoaded] = useState(false);
  const [jobsRefresh, setJobsRefresh] = useState(0);
  const [profile, setProfile] = useState<{
    has_resume: boolean;
    resume_chars: number;
    overrides_updated_at: string | null;
    last_summary: string;
    override_counts: Record<string, number>;
  } | null>(null);
  const [resumeUploading, setResumeUploading] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [profileMsg, setProfileMsg] = useState<string | null>(null);
  const [resumeFileName, setResumeFileName] = useState<string | null>(null);
  const resumeFileRef = useRef<HTMLInputElement>(null);

  const countByDate = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of summaries) {
      m.set(s.date, s.count);
    }
    return m;
  }, [summaries]);

  const loadSummaries = useCallback(async () => {
    const r = await apiFetch("/api/dates");
    if (r.status === 401) throw new Error("Session expired — sign in again.");
    if (!r.ok) throw new Error("Failed to load calendar data");
    const data = await r.json();
    setSummaries(data.dates ?? []);
  }, []);

  const refreshDashboardStatus = useCallback(async () => {
    const hint = (key: string, msg: string) =>
      setStatusHints((prev) => ({ ...prev, [key]: msg }));

    setDashStatus({
      api: "loading",
      llm: "loading",
      jobs: "loading",
      schedule: "loading",
      profile: "loading",
      auth: "loading",
    });

    let healthJson: {
      ok?: boolean;
      agent?: boolean;
      agents?: AgentMeta[];
      login_required?: boolean;
    } | null = null;

    try {
      const hRes = await apiFetch("/api/health");
      if (!hRes.ok) {
        hint("api", `${hRes.status} ${hRes.statusText}`);
        setHealth({});
        setAgents([]);
        setDashStatus({
          api: "error",
          llm: "error",
          jobs: "error",
          schedule: "error",
          profile: "error",
          auth: "error",
        });
        setDiscordNotificationsEnabled(null);
        setAgentDiagnostics(null);
        setStatusCheckedAt(new Date());
        return;
      }
      const parsed = await hRes.json();
      healthJson = parsed;
      setHealth(
        parsed as {
          ok?: boolean;
          service?: string;
          agent?: boolean;
          agents_enabled?: boolean;
          agents?: AgentMeta[];
          login_required?: boolean;
          llm_agent_count?: number;
        }
      );
      hint("api", "Backend reachable");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Network error";
      hint("api", msg);
      setHealth({});
      setAgents([]);
      setDashStatus({
        api: "error",
        llm: "error",
        jobs: "error",
        schedule: "error",
        profile: "error",
        auth: "error",
      });
      setDiscordNotificationsEnabled(null);
      setAgentDiagnostics(null);
      setStatusCheckedAt(new Date());
      return;
    }

    const llmOk = healthJson?.agent === true;
    hint("llm", llmOk ? "API key set — agents enabled" : "No OPENROUTER_API_KEY / OPENAI_API_KEY");

    const [agentsR, datesR, schedR, profR, sessR, notifR, diagR] = await Promise.all([
      apiFetch("/api/agents"),
      apiFetch("/api/dates"),
      apiFetch("/api/schedule"),
      apiFetch("/api/profile"),
      requireLogin ? apiFetch("/api/auth/session") : Promise.resolve(null as Response | null),
      apiFetch("/api/settings/notifications"),
      apiFetch("/api/agents/diagnostics"),
    ]);

    if (agentsR.ok) {
      try {
        const data = await agentsR.json();
        setAgents(data.agents ?? []);
      } catch {
        setAgents(healthJson?.agents ?? []);
      }
    } else {
      setAgents(healthJson?.agents ?? []);
      hint(
        "llm",
        llmOk
          ? `LLM OK · /api/agents ${agentsR.status} (using agent list from /api/health)`
          : "No LLM API key on server"
      );
    }

    let jobsP: PillState = datesR.ok ? "ok" : "error";
    if (!datesR.ok) {
      hint("jobs", datesR.status === 401 ? "Sign in again" : `${datesR.status} ${datesR.statusText}`);
    } else {
      hint("jobs", "Calendar / job history API OK");
    }

    let schedP: PillState = schedR.ok ? "ok" : "error";
    if (!schedR.ok) {
      hint("schedule", `${schedR.status} ${schedR.statusText}`);
    } else {
      hint("schedule", "Next-run schedule available");
    }

    let profP: PillState = profR.ok ? "ok" : "error";
    if (!profR.ok) {
      hint("profile", `${profR.status} ${profR.statusText}`);
    } else {
      hint("profile", "Resume / ranker profile API OK");
    }

    let authP: PillState = "ok";
    if (requireLogin) {
      if (!sessR || !sessR.ok) {
        authP = "error";
        hint("auth", sessR ? `${sessR.status} session` : "No session response");
      } else {
        try {
          const sj = (await sessR.json()) as { valid?: boolean };
          authP = sj.valid ? "ok" : "error";
          hint("auth", sj.valid ? "JWT session valid" : "Session expired — sign in again");
        } catch {
          authP = "error";
          hint("auth", "Invalid session response");
        }
      }
    } else {
      hint("auth", "Dashboard login disabled (dev)");
    }

    if (notifR.ok) {
      try {
        const nj = (await notifR.json()) as { discord_notifications_enabled?: boolean };
        setDiscordNotificationsEnabled(nj.discord_notifications_enabled !== false);
      } catch {
        setDiscordNotificationsEnabled(true);
      }
    } else {
      setDiscordNotificationsEnabled(null);
    }

    if (diagR.ok) {
      try {
        setAgentDiagnostics(await diagR.json());
      } catch {
        setAgentDiagnostics(null);
      }
    } else {
      setAgentDiagnostics(null);
    }

    setDashStatus({
      api: "ok",
      llm: llmOk ? "ok" : "warn",
      jobs: jobsP,
      schedule: schedP,
      profile: profP,
      auth: authP,
    });
    setStatusCheckedAt(new Date());
  }, [requireLogin]);

  const runAgentModelTests = useCallback(async () => {
    setAgentTestLoading(true);
    setAgentTestResults(null);
    try {
      const r = await apiFetch("/api/agents/test-models", { method: "POST" });
      const data = (await r.json().catch(() => ({}))) as {
        detail?: string | { msg?: string }[];
        overall_ok?: boolean;
        tested_at?: string;
        results?: Array<{
          id: string;
          name: string;
          model: string;
          ok: boolean;
          latency_ms?: number;
          error?: string;
          response_preview?: string;
        }>;
        error?: string;
      };
      if (!r.ok) {
        const det = data.detail;
        const msg =
          typeof det === "string"
            ? det
            : Array.isArray(det)
              ? det.map((x: { msg?: string }) => x.msg).filter(Boolean).join(", ")
              : r.statusText;
        setAgentTestResults({
          overall_ok: false,
          error: msg || r.statusText,
        });
        return;
      }
      setAgentTestResults(data);
    } catch (e: unknown) {
      setAgentTestResults({
        overall_ok: false,
        error: e instanceof Error ? e.message : "Request failed",
      });
    } finally {
      setAgentTestLoading(false);
    }
  }, []);

  const saveDiscordNotifications = useCallback(async (enabled: boolean) => {
    setDiscordToggleSaving(true);
    try {
      const r = await apiFetch("/api/settings/notifications", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discord_notifications_enabled: enabled }),
      });
      if (r.ok) {
        const d = (await r.json()) as { discord_notifications_enabled?: boolean };
        setDiscordNotificationsEnabled(d.discord_notifications_enabled !== false);
      }
    } catch {
      /* keep previous toggle */
    } finally {
      setDiscordToggleSaving(false);
    }
  }, []);

  const loadProfile = useCallback(async () => {
    try {
      const r = await apiFetch("/api/profile");
      if (!r.ok) return;
      setProfile(await r.json());
    } catch {
      setProfile(null);
    }
  }, []);

  const loadSchedule = useCallback(async () => {
    try {
      const r = await apiFetch("/api/schedule");
      if (!r.ok) return;
      const data = (await r.json()) as {
        schedule_time?: string;
        next_run_iso?: string;
        seconds_until_next?: number;
      };
      if (typeof data.schedule_time === "string") {
        setScheduleTimeLabel(data.schedule_time);
      }
      if (data.next_run_iso) {
        const ms = Date.parse(data.next_run_iso);
        if (!Number.isNaN(ms)) {
          setNextRunAtMs(ms);
        } else if (typeof data.seconds_until_next === "number") {
          setNextRunAtMs(Date.now() + data.seconds_until_next * 1000);
        }
      } else if (typeof data.seconds_until_next === "number") {
        setNextRunAtMs(Date.now() + data.seconds_until_next * 1000);
      }
      setScheduleLoaded(true);
    } catch {
      /* ignore */
    }
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setAuthPhase("loading");
    void fetchAuthStatus()
      .then((st) => {
        if (!st.login_required) setAuthPhase("ready");
        else setAuthPhase("login");
      })
      .catch(() => setAuthPhase("login"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const st = await fetchAuthStatus();
        if (cancelled) return;
        setRequireLogin(!!st.login_required);
        if (!st.login_required) {
          setAuthPhase("ready");
          return;
        }
        const ok = await fetchSessionValid();
        if (cancelled) return;
        setAuthPhase(ok ? "ready" : "login");
      } catch {
        if (!cancelled) setAuthPhase("login");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (authPhase !== "ready") return;
    void refreshDashboardStatus();
    loadSummaries().catch(() => setSummaries([]));
    void loadSchedule();
    void loadProfile();
  }, [authPhase, loadSummaries, refreshDashboardStatus, loadSchedule, loadProfile]);

  useEffect(() => {
    if (authPhase !== "ready") return;
    const id = window.setInterval(() => void refreshDashboardStatus(), 90_000);
    return () => window.clearInterval(id);
  }, [authPhase, refreshDashboardStatus]);

  useEffect(() => {
    if (authPhase !== "ready") return;
    const id = window.setInterval(() => void loadSchedule(), 60_000);
    return () => window.clearInterval(id);
  }, [authPhase, loadSchedule]);

  useEffect(() => {
    if (nextRunAtMs == null) return;
    const tick = () => {
      setCountdownSec(Math.max(0, Math.floor((nextRunAtMs - Date.now()) / 1000)));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [nextRunAtMs]);

  useEffect(() => {
    if (!scrapePolling) return;
    setScrapePhaseIdx(0);
    const id = window.setInterval(() => {
      setScrapePhaseIdx((p) => (p + 1) % SCRAPE_PHASE_MESSAGES.length);
    }, 4000);
    return () => window.clearInterval(id);
  }, [scrapePolling]);

  useEffect(() => {
    if (!scrapePolling) return;
    const poll = async () => {
      try {
        const r = await apiFetch("/api/scrape-status");
        if (!r.ok) return;
        const d = (await r.json()) as { running?: boolean };
        if (!d.running) {
          setScrapePolling(false);
          setScrapeBusy(false);
          setScrapeMsg("Scrape finished. Calendar updated.");
          await loadSummaries().catch(() => undefined);
          await loadSchedule().catch(() => undefined);
          setJobsRefresh((x) => x + 1);
        }
      } catch {
        /* ignore */
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), 1500);
    return () => window.clearInterval(id);
  }, [scrapePolling, loadSummaries, loadSchedule]);

  useEffect(() => {
    if (authPhase !== "ready") return;
    let cancelled = false;
    setLoading(true);
    setJobsErr(null);
    apiFetch(`/api/jobs/${selectedDate}`)
      .then(async (r) => {
        if (!r.ok) throw new Error("Could not load jobs for that day");
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          const list: JobRow[] = data.jobs ?? [];
          list.sort(
            (a, b) =>
              (PRI_ORDER[a.priority] ?? 9) - (PRI_ORDER[b.priority] ?? 9) ||
              a.title.localeCompare(b.title)
          );
          setJobs(list);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setJobsErr(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDate, jobsRefresh, authPhase]);

  async function uploadResumeFile() {
    const input = resumeFileRef.current;
    const f = input?.files?.[0];
    if (!f) {
      setProfileMsg("Choose a PDF file first.");
      return;
    }
    setResumeUploading(true);
    setProfileMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await apiFetch("/api/profile/resume", { method: "POST", body: fd });
      const data = (await r.json().catch(() => ({}))) as { detail?: string; char_count?: number };
      if (!r.ok) {
        const det = data.detail;
        const msg = typeof det === "string" ? det : r.statusText;
        throw new Error(msg || r.statusText);
      }
      setProfileMsg(`Resume saved (${data.char_count ?? "?"} characters extracted). Run “Update ranker from resume”.`);
      if (input) input.value = "";
      setResumeFileName(null);
      await loadProfile();
    } catch (e: unknown) {
      setProfileMsg(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setResumeUploading(false);
    }
  }

  async function reviewResumeProfile() {
    setReviewBusy(true);
    setProfileMsg(null);
    try {
      const r = await apiFetch("/api/profile/review-resume", { method: "POST" });
      const data = (await r.json().catch(() => ({}))) as { detail?: string; summary?: string };
      if (!r.ok) {
        const det = data.detail;
        const msg = typeof det === "string" ? det : r.statusText;
        throw new Error(msg || r.statusText);
      }
      setProfileMsg(data.summary || "Ranker overrides updated for the next scrape.");
      await loadProfile();
    } catch (e: unknown) {
      setProfileMsg(e instanceof Error ? e.message : "Review failed");
    } finally {
      setReviewBusy(false);
    }
  }

  async function clearRankerOverrides() {
    if (
      !window.confirm(
        "Clear all LLM-added ranker phrases? Built-in rules in main.py are unchanged."
      )
    ) {
      return;
    }
    try {
      const r = await apiFetch("/api/profile/overrides", { method: "DELETE" });
      if (!r.ok) throw new Error("Could not clear overrides");
      setProfileMsg("Ranker overrides cleared.");
      await loadProfile();
    } catch (e: unknown) {
      setProfileMsg(e instanceof Error ? e.message : "Failed");
    }
  }

  async function triggerScrapeNow() {
    setScrapeMsg(null);
    setScrapeBusy(true);
    try {
      const r = await apiFetch("/api/scrape-now", {
        method: "POST",
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const det = data.detail;
        const msg =
          typeof det === "string"
            ? det
            : Array.isArray(det)
              ? det.map((x: { msg?: string }) => x.msg).filter(Boolean).join(", ")
              : r.statusText;
        throw new Error(msg || r.statusText);
      }
      setScrapeMsg(typeof data.detail === "string" ? data.detail : "Scrape running…");
      setScrapePolling(true);
    } catch (e: unknown) {
      setScrapeMsg(e instanceof Error ? e.message : "Request failed");
      setScrapeBusy(false);
    }
  }

  const monthLabel = monthCursor.toLocaleString("default", {
    month: "long",
    year: "numeric",
  });

  const calendarCells = useMemo(() => {
    const first = startOfMonth(monthCursor);
    const startWeekday = first.getDay();
    const daysInMonth = new Date(
      monthCursor.getFullYear(),
      monthCursor.getMonth() + 1,
      0
    ).getDate();

    const cells: { date: Date | null; inMonth: boolean }[] = [];
    for (let i = 0; i < startWeekday; i++) {
      cells.push({ date: null, inMonth: false });
    }
    for (let day = 1; day <= daysInMonth; day++) {
      cells.push({
        date: new Date(monthCursor.getFullYear(), monthCursor.getMonth(), day),
        inMonth: true,
      });
    }
    while (cells.length % 7 !== 0) {
      cells.push({ date: null, inMonth: false });
    }
    return cells;
  }, [monthCursor]);

  async function runAgent(job: JobRow, agentId: string) {
    const jobKey = job.url || job.title;
    const busyKey = `${jobKey}::${agentId}`;
    setAgentBusy(busyKey);
    try {
      const r = await apiFetch("/api/agent/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: agentId,
          title: job.title,
          company: job.company,
          description: job.description,
          url: job.url,
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        const detail = err.detail;
        const msg = Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join(", ")
          : typeof detail === "string"
            ? detail
            : r.statusText;
        throw new Error(msg || r.statusText);
      }
      const data = await r.json();
      const result = data.result as Record<string, unknown>;
      setAgentResults((prev) => ({
        ...prev,
        [jobKey]: {
          ...(prev[jobKey] || {}),
          [agentId]: { data: result },
        },
      }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Agent failed";
      setAgentResults((prev) => ({
        ...prev,
        [jobKey]: {
          ...(prev[jobKey] || {}),
          [agentId]: { error: msg },
        },
      }));
    } finally {
      setAgentBusy(null);
    }
  }

  const displayAgents = agents.length > 0 ? agents : FALLBACK_AGENTS;
  const scrapePhaseText = useMemo(() => {
    const base = SCRAPE_PHASE_MESSAGES[scrapePhaseIdx];
    if (scrapePhaseIdx === SCRAPE_PHASE_MESSAGES.length - 1) {
      const names = displayAgents.map((a) => a.label).join(" · ");
      return names ? `${base} (${names})` : base;
    }
    return base;
  }, [scrapePhaseIdx, displayAgents]);
  /** Only disable LLM actions when health explicitly reports no API key — not while loading or on fetch failure. */
  const llmDisabled = health.agent === false;

  const todayIso = isoDate(new Date());

  if (authPhase === "loading") {
    return (
      <div className="login-screen">
        <p className="login-lead">Loading…</p>
      </div>
    );
  }

  if (authPhase === "login") {
    return <Login onLoggedIn={() => setAuthPhase("ready")} />;
  }

  return (
    <div>
      <header className="app-header">
        <div className="app-header-top">
          <div>
            <h1>Job Scraper</h1>
            <p>
              Today’s matches and past days on the calendar. Ranking fixes live in{" "}
              <code style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "0.88em" }}>
                main.py
              </code>{" "}
              (JobRanker).
            </p>
            <p className="schedule-hint">
              Next automatic scrape in{" "}
              <strong>{scheduleLoaded ? formatCountdown(countdownSec) : "…"}</strong>
              {" · "}
              scheduled at <strong>{scheduleTimeLabel}</strong> (server time,{" "}
              <code className="schedule-env">SCRAPE_SCHEDULE_TIME</code>)
            </p>
          </div>
          <div className="scrape-now">
            {requireLogin && (
              <button type="button" className="sign-out-btn" onClick={signOut}>
                Sign out
              </button>
            )}
            <button type="button" disabled={scrapeBusy} onClick={() => void triggerScrapeNow()}>
              {scrapeBusy ? "Scraping…" : "Scrape now"}
            </button>
          </div>
        </div>
        <div
          className="dashboard-status"
          role="region"
          aria-label="Service status"
          aria-live="polite"
        >
          <div className="dashboard-status-inner">
            <span className="dashboard-status-title">System status</span>
            {(
              [
                ["api", "API"],
                ["llm", "LLM"],
                ["jobs", "Jobs"],
                ["schedule", "Schedule"],
                ["profile", "Profile"],
                ["auth", "Login"],
              ] as const
            ).map(([key, label]) => (
              <span
                key={key}
                className={`status-pill status-pill--${dashStatus[key]}`}
                title={statusHints[key] ?? label}
              >
                <span className="status-pill-dot" aria-hidden />
                {label}
              </span>
            ))}
            <button
              type="button"
              className="status-refresh-btn"
              onClick={() => void refreshDashboardStatus()}
            >
              Refresh status
            </button>
            <button
              type="button"
              className="status-hard-refresh-btn"
              title="Reload the entire dashboard (full page reload)"
              onClick={() => window.location.reload()}
            >
              Hard refresh
            </button>
            <label
              className="discord-toggle"
              title="When off, scrapes still run but no messages are sent to Discord (saved in data/app_settings.json on the server)."
            >
              <input
                type="checkbox"
                role="switch"
                checked={discordNotificationsEnabled === true}
                disabled={discordNotificationsEnabled === null || discordToggleSaving}
                onChange={(e) => void saveDiscordNotifications(e.target.checked)}
              />
              <span className="discord-toggle-text">Discord messages</span>
            </label>
            {statusCheckedAt ? (
              <time className="status-checked-at" dateTime={statusCheckedAt.toISOString()}>
                Checked {statusCheckedAt.toLocaleTimeString()}
              </time>
            ) : null}
          </div>
        </div>
        {scrapePolling && (
          <div className="scrape-progress" aria-busy="true">
            <div className="scrape-progress-bar indeterminate" />
            <p className="scrape-progress-label">{scrapePhaseText}</p>
          </div>
        )}
        {scrapeMsg && !scrapePolling && <p className="scrape-msg">{scrapeMsg}</p>}
        {scrapeMsg && scrapePolling && (
          <p className="scrape-msg scrape-msg-muted">{scrapeMsg}</p>
        )}
      </header>

      <section className="llm-agents-panel" aria-label="LLM models and connectivity">
        <div className="llm-agents-head">
          <h3 className="llm-agents-title">LLM agents &amp; models</h3>
          <button
            type="button"
            className="llm-agents-test-btn"
            disabled={llmDisabled || agentTestLoading}
            title={
              llmDisabled
                ? "Set OPENROUTER_API_KEY or OPENAI_API_KEY on the server"
                : "Send a tiny ping to each model (can take ~15–60s)"
            }
            onClick={() => void runAgentModelTests()}
          >
            {agentTestLoading ? "Testing…" : "Test models"}
          </button>
        </div>
        {!agentDiagnostics?.configured && (
          <p className="llm-agents-muted">Set an API key to see resolved models and run connectivity tests.</p>
        )}
        {agentDiagnostics?.configured && (
          <>
            <p className="llm-agents-meta">
              Provider <code className="schedule-env">{agentDiagnostics.provider}</code> · API{" "}
              <code className="llm-agents-url">{agentDiagnostics.base_url}</code>
            </p>
            <table className="llm-agents-table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Model</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {(agentDiagnostics.agents ?? []).map((a) => (
                  <tr key={a.id}>
                    <td>{a.label}</td>
                    <td>
                      <code>{a.model}</code>
                    </td>
                    <td>
                      {a.model_source === "env" ? (
                        <>
                          env (<code className="schedule-env">{a.env_key}</code>)
                        </>
                      ) : (
                        "default"
                      )}
                    </td>
                  </tr>
                ))}
                {agentDiagnostics.resume_profile ? (
                  <tr>
                    <td>{agentDiagnostics.resume_profile.label}</td>
                    <td>
                      <code>{agentDiagnostics.resume_profile.model}</code>
                    </td>
                    <td>
                      {agentDiagnostics.resume_profile.model_source === "env" ? (
                        <>
                          env (
                          <code className="schedule-env">{agentDiagnostics.resume_profile.env_key}</code>)
                        </>
                      ) : (
                        "default"
                      )}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </>
        )}
        {agentTestResults?.error ? (
          <p className="err llm-agents-test-msg">{agentTestResults.error}</p>
        ) : null}
        {agentTestResults?.results && agentTestResults.results.length > 0 ? (
          <div className="llm-agents-test-results">
            <p className="llm-agents-test-summary">
              {agentTestResults.overall_ok ? (
                <span className="llm-test-ok">All model checks passed.</span>
              ) : (
                <span className="llm-test-bad">Some model checks failed.</span>
              )}
              {agentTestResults.tested_at ? (
                <span className="llm-agents-muted"> {agentTestResults.tested_at}</span>
              ) : null}
            </p>
            <ul className="llm-test-list">
              {agentTestResults.results.map((r) => (
                <li key={r.id} className={r.ok ? "llm-test-item ok" : "llm-test-item bad"}>
                  <strong>{r.name}</strong> — <code>{r.model}</code>
                  {r.ok ? (
                    <>
                      {" "}
                      · OK ({r.latency_ms ?? "?"} ms)
                      {r.response_preview ? ` · «${r.response_preview}»` : ""}
                    </>
                  ) : (
                    <>
                      {" "}
                      · <span className="err">{r.error ?? "Failed"}</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="profile-panel" aria-label="Resume and search profile">
        <h3 className="profile-heading">Resume &amp; search ranking</h3>
        <p className="profile-lead">
          Upload your CV as PDF. An LLM compares it to the built-in JobRanker lists in{" "}
          <code className="schedule-env">main.py</code> and saves <strong>additional</strong> title and
          keyword phrases to{" "}
          <code className="schedule-env">data/ranker_overrides.json</code>. The next scrape uses those
          extras so matches align better with your background. After upload, run{" "}
          <strong>Update ranker from resume</strong> — until then, “Extra phrases” stays none.
        </p>
        <div className="profile-row">
          <div className="profile-file-picker">
            <input
              ref={resumeFileRef}
              id="resume-pdf-input"
              type="file"
              accept="application/pdf,.pdf"
              className="profile-file-input"
              onChange={(e) => setResumeFileName(e.target.files?.[0]?.name ?? null)}
            />
            <label htmlFor="resume-pdf-input" className="profile-file-label">
              Choose PDF
            </label>
            <span className="profile-file-name" title={resumeFileName ?? undefined}>
              {resumeFileName ?? "No file chosen"}
            </span>
          </div>
          <button
            type="button"
            className="profile-btn-secondary"
            disabled={resumeUploading}
            onClick={() => void uploadResumeFile()}
          >
            {resumeUploading ? "Uploading…" : "Upload PDF"}
          </button>
          <button
            type="button"
            className="profile-review-btn"
            disabled={reviewBusy || !profile?.has_resume || llmDisabled}
            onClick={() => void reviewResumeProfile()}
          >
            {reviewBusy ? "Updating ranker…" : "Update ranker from resume"}
          </button>
          <button
            type="button"
            className="profile-btn-secondary"
            onClick={() => void clearRankerOverrides()}
          >
            Clear overrides
          </button>
        </div>
        {llmDisabled && (
          <p className="profile-hint">Set OPENROUTER_API_KEY or OPENAI_API_KEY to run resume review.</p>
        )}
        {profile && (
          <p className="profile-meta">
            Resume: {profile.has_resume ? `${profile.resume_chars} characters stored` : "none"} · Extra
            phrases:{" "}
            {(profile.override_counts?.perfect_titles ?? 0) +
              (profile.override_counts?.good_titles ?? 0) +
              (profile.override_counts?.good_keywords ?? 0) >
            0
              ? `${profile.override_counts.perfect_titles} perfect titles, ${profile.override_counts.good_titles} good titles, ${profile.override_counts.good_keywords} keywords`
              : "none"}
            {profile.overrides_updated_at ? ` · updated ${profile.overrides_updated_at}` : ""}
          </p>
        )}
        {profile?.last_summary ? (
          <p className="profile-summary">
            <strong>Last review:</strong> {profile.last_summary}
          </p>
        ) : null}
        {profileMsg ? <p className="profile-msg">{profileMsg}</p> : null}
      </section>

      <div className="layout">
        <div className="panel">
          <h2>Calendar</h2>
          <div className="calendar-nav">
            <button
              type="button"
              aria-label="Previous month"
              onClick={() => setMonthCursor((m) => addMonths(m, -1))}
            >
              ←
            </button>
            <span className="month-label">{monthLabel}</span>
            <button
              type="button"
              aria-label="Next month"
              onClick={() => setMonthCursor((m) => addMonths(m, 1))}
            >
              →
            </button>
          </div>
          <div className="calendar-grid">
            {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
              <div key={d} className="cal-dow">
                {d}
              </div>
            ))}
            {calendarCells.map((cell, i) => {
              if (!cell.date) {
                return <div key={`e-${i}`} className="cal-day muted" />;
              }
              const iso = isoDate(cell.date);
              const n = countByDate.get(iso);
              const hasJobs = (n ?? 0) > 0;
              const sel = iso === selectedDate;
              return (
                <button
                  key={iso}
                  type="button"
                  className={`cal-day ${hasJobs ? "has-jobs" : ""} ${sel ? "selected" : ""}`}
                  onClick={() => setSelectedDate(iso)}
                >
                  <span className="n">{cell.date.getDate()}</span>
                  {hasJobs && <span className="badge">{n}</span>}
                </button>
              );
            })}
          </div>
          <div className="status-bar">
            <button
              type="button"
              className="desc-toggle"
              onClick={() => setSelectedDate(todayIso)}
            >
              Jump to today
            </button>
            {health.agent === false && (
              <span> · Agents: set OPENROUTER_API_KEY or OPENAI_API_KEY</span>
            )}
            {!llmDisabled && (health.agent === true || health.agents_enabled === true) && (
              <span>
                {" "}
                · {displayAgents.length} agent{displayAgents.length === 1 ? "" : "s"} configured
              </span>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="jobs-head">
            <h2>{parseISODate(selectedDate).toLocaleDateString("default", {
              weekday: "long",
              month: "long",
              day: "numeric",
              year: "numeric",
            })}</h2>
            <span className="sub">
              {loading ? "Loading…" : `${jobs.length} job${jobs.length === 1 ? "" : "s"}`}
            </span>
          </div>

          {jobsErr && <p className="err">{jobsErr}</p>}
          {!loading && !jobsErr && jobs.length === 0 && (
            <p className="empty">
              No jobs stored for this day yet. History is written when the scraper finds{" "}
              <strong>new</strong> listings (use <strong>Scrape now</strong> above, or wait for the
              scheduled run). Days before this change have no archive.
            </p>
          )}

          {jobs.map((job) => {
            const key = job.url || job.title;
            const pri =
              job.priority === "PERFECT_MATCH"
                ? "perfect"
                : job.priority === "GOOD_MATCH"
                  ? "good"
                  : "weak";
            const priLabel =
              job.priority === "PERFECT_MATCH"
                ? "Perfect"
                : job.priority === "GOOD_MATCH"
                  ? "Good"
                  : "Weak";
            const perAgent = agentResults[key] || {};
            const descOpen = expandedDesc[key];

            return (
              <article key={key} className="job-card">
                <header>
                  <span className={`prio ${pri}`}>{priLabel}</span>
                  <span className="cat">{job.category || "crypto"} · {job.source}</span>
                </header>
                <h3 className="job-title">{job.title}</h3>
                <p className="job-co">{job.company}</p>
                <p className="job-meta">
                  {job.posted_date ? `Posted: ${job.posted_date} · ` : null}
                  Ranker: {job.priority_reason}
                </p>
                <div className="job-actions">
                  <a href={job.url} target="_blank" rel="noreferrer noopener">
                    Open listing
                  </a>
                  <button
                    type="button"
                    className="desc-toggle"
                    onClick={() =>
                      setExpandedDesc((p) => ({ ...p, [key]: !p[key] }))
                    }
                  >
                    {descOpen ? "Hide description" : "Description"}
                  </button>
                </div>
                <div className="agent-row">
                  {displayAgents.map((a) => {
                    const busyKey = `${key}::${a.id}`;
                    const thisJobAgentLoading =
                      agentBusy !== null && agentBusy.startsWith(`${key}::`);
                    return (
                      <button
                        key={a.id}
                        type="button"
                        className="agent-btn"
                        disabled={llmDisabled || thisJobAgentLoading}
                        title={
                          llmDisabled
                            ? "Set OPENROUTER_API_KEY or OPENAI_API_KEY on the API server"
                            : a.model
                              ? `${a.description || a.label} — ${a.model}`
                              : a.description || a.label
                        }
                        onClick={() => runAgent(job, a.id)}
                      >
                        {agentBusy === busyKey ? "…" : a.label}
                      </button>
                    );
                  })}
                </div>
                {descOpen && job.description && (
                  <div className="desc-block">{job.description}</div>
                )}
                {displayAgents.map((a) => {
                  const entry = perAgent[a.id];
                  if (!entry?.data && !entry?.error) return null;
                  return (
                    <div key={a.id} className="agent-box">
                      <div className="agent-subcard">
                        <div className="agent-subcard-title">
                          {a.label}
                          {a.model ? (
                            <span style={{ fontWeight: 400, opacity: 0.75 }}>
                              {" "}
                              · {a.model}
                            </span>
                          ) : null}
                        </div>
                        {entry.error && <div className="err">{entry.error}</div>}
                        {entry.data && (
                          <AgentPayload agentId={a.id} data={entry.data} />
                        )}
                      </div>
                    </div>
                  );
                })}
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
