import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { apiFetch, setToken } from "./api";
import Login, { fetchAuthStatus, fetchSessionValid } from "./Login";
import { SettingsPage } from "./SettingsPage";

export type AppProps = {
  initialView?: "dashboard" | "settings";
};

/** Opens the dedicated settings HTML page in a new tab (separate document). */
function openSettingsInNewTab() {
  const base = import.meta.env.BASE_URL.replace(/\/?$/, "/");
  const url = new URL(`${base}settings.html`, window.location.origin).href;
  window.open(url, "_blank", "noopener,noreferrer");
}

type ScraperResult = {
  name: string;
  category: string;
  status: string;
  jobs_found: number;
  error: string | null;
};

type ScrapeStatus = {
  running?: boolean;
  phase?: string | null;
  scrapers_total?: number;
  scrapers_done?: number;
  scraper_results?: ScraperResult[];
  totals?: { scraped: number; blacklisted: number; skipped: number; new: number } | null;
  error?: string | null;
};

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
  agent_results?: Record<string, Record<string, unknown>>;
};

type ApplicationStatus = "applied" | "not_applied";

type ApplicationEntry = {
  url: string;
  title?: string;
  company?: string;
  status: ApplicationStatus;
  date?: string;
  updated?: string;
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

// SIMPLIFIED — only fit agent remains.
const FALLBACK_AGENTS: AgentMeta[] = [
  {
    id: "fit",
    label: "Role fit",
    description: "Scores 1-10 vs DevOps / infra / platform targets.",
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
  "AI fit scoring on top jobs…",
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

export default function App({ initialView = "dashboard" }: AppProps) {
  const route = initialView;
  const [authPhase, setAuthPhase] = useState<"loading" | "login" | "ready">("loading");
  const [requireLogin, setRequireLogin] = useState(false);
  const [monthCursor, setMonthCursor] = useState(() => startOfMonth(new Date()));
  const [serverToday, setServerToday] = useState<string | null>(null);
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
  const [scrapeSources, setScrapeSources] = useState<
    Array<{ category: string; name: string; base_url: string }> | null
  >(null);
  const [agents, setAgents] = useState<AgentMeta[]>([]);
  const [agentBusy, setAgentBusy] = useState<string | null>(null);
  const [agentResults, setAgentResults] = useState<Record<string, Record<string, AgentResultEntry>>>(
    {}
  );
  const [expandedDesc, setExpandedDesc] = useState<Record<string, boolean>>({});
  const [feedback, setFeedback] = useState<Record<string, "good" | "bad">>({});
  const [applicationsMap, setApplicationsMap] = useState<Record<string, ApplicationStatus>>({});
  const [applicationsList, setApplicationsList] = useState<ApplicationEntry[]>([]);
  const [scrapeBusy, setScrapeBusy] = useState(false);
  const [scrapeMsg, setScrapeMsg] = useState<string | null>(null);
  const [scrapePolling, setScrapePolling] = useState(false);
  const [scrapePhaseIdx, setScrapePhaseIdx] = useState(0);
  const [scrapeDebugOpen, setScrapeDebugOpen] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<"all" | "crypto" | "cruise" | "general">("all");
  const [scrapeStatus, setScrapeStatus] = useState<ScrapeStatus>({});
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
        setScrapeSources(null);
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
      setScrapeSources(null);
      setStatusCheckedAt(new Date());
      return;
    }

    const llmOk = healthJson?.agent === true;
    hint("llm", llmOk ? "API key set — agents enabled" : "No OPENROUTER_API_KEY / OPENAI_API_KEY");

    const [agentsR, datesR, schedR, profR, sessR, notifR, diagR, sourcesR, feedbackR, appsR] = await Promise.all([
      apiFetch("/api/agents"),
      apiFetch("/api/dates"),
      apiFetch("/api/schedule"),
      apiFetch("/api/profile"),
      requireLogin ? apiFetch("/api/auth/session") : Promise.resolve(null as Response | null),
      apiFetch("/api/settings/notifications"),
      apiFetch("/api/agents/diagnostics"),
      apiFetch("/api/sources"),
      apiFetch("/api/feedback"),
      apiFetch("/api/applications"),
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

    if (sourcesR.ok) {
      try {
        const src = (await sourcesR.json()) as {
          sources?: Array<{ category: string; name: string; base_url: string }>;
        };
        setScrapeSources(src.sources ?? []);
      } catch {
        setScrapeSources(null);
      }
    } else {
      setScrapeSources(null);
    }

    if (feedbackR.ok) {
      try {
        const fj = (await feedbackR.json()) as { feedback?: Record<string, string> };
        setFeedback((fj.feedback ?? {}) as Record<string, "good" | "bad">);
      } catch {
        // non-fatal
      }
    }

    if (appsR.ok) {
      try {
        const aj = (await appsR.json()) as {
          map?: Record<string, ApplicationStatus>;
          applications?: ApplicationEntry[];
        };
        setApplicationsMap((aj.map ?? {}) as Record<string, ApplicationStatus>);
        setApplicationsList((aj.applications ?? []) as ApplicationEntry[]);
      } catch {
        // non-fatal
      }
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
    // Sync "today" to server date (avoids client/server timezone mismatch)
    apiFetch("/api/today")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { date?: string } | null) => {
        if (d?.date) {
          setServerToday(d.date);
          setSelectedDate(d.date);
          setMonthCursor(startOfMonth(parseISODate(d.date)));
        }
      })
      .catch(() => undefined);
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
        const d = (await r.json()) as ScrapeStatus;
        setScrapeStatus(d);
        if (!d.running) {
          setScrapePolling(false);
          setScrapeBusy(false);
          if (d.error) {
            setScrapeMsg(`Error: ${d.error}`);
          } else {
            const t = d.totals;
            setScrapeMsg(
              t
                ? `Done — ${t.scraped} scraped · ${t.new} new · ${t.blacklisted} filtered · ${t.skipped} duplicates`
                : "Scrape finished. Calendar updated."
            );
          }
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
          // Pre-populate agentResults from stored agent_results in each job
          setAgentResults((prev) => {
            const next = { ...prev };
            for (const job of list) {
              if (job.agent_results) {
                const jobKey = job.url || job.title;
                next[jobKey] = { ...(next[jobKey] || {}) };
                for (const [agId, result] of Object.entries(job.agent_results)) {
                  if (!next[jobKey][agId]) {
                    next[jobKey][agId] = { data: result as Record<string, unknown> };
                  }
                }
              }
            }
            return next;
          });
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

  async function reviewFeedbackProfile() {
    setReviewBusy(true);
    setProfileMsg(null);
    try {
      const r = await apiFetch("/api/profile/review-feedback", { method: "POST" });
      const data = (await r.json().catch(() => ({}))) as { detail?: string; summary?: string };
      if (!r.ok) {
        const det = data.detail;
        const msg = typeof det === "string" ? det : r.statusText;
        throw new Error(msg || r.statusText);
      }
      const successMsg = data.summary || "Ranker rules successfully updated from your liked jobs!";
      setProfileMsg(successMsg);
      // Give the user an unmistakable popup that it worked
      window.alert(`✅ Success!\n\n${successMsg}`);
      await loadProfile();
    } catch (e: unknown) {
      const errorMsg = e instanceof Error ? e.message : "Review failed";
      setProfileMsg(errorMsg);
      window.alert(`❌ Failed: ${errorMsg}`);
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
    console.log("[agent] calling /api/agent/evaluate", { agentId, title: job.title });
    try {
      // 1. Submit — returns immediately with a task_id
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
        const text = await r.text();
        let msg = `${r.status}: ${r.statusText}`;
        try {
          const err = JSON.parse(text);
          if (typeof err.detail === "string") msg = `${r.status}: ${err.detail}`;
        } catch { /* Cloudflare HTML or non-JSON */ }
        throw new Error(msg);
      }
      const submit = await r.json();
      const taskId = submit.task_id as string;
      if (!taskId) {
        if (submit.result) {
          setAgentResults((prev) => ({
            ...prev,
            [jobKey]: { ...(prev[jobKey] || {}), [agentId]: { data: submit.result } },
          }));
          return;
        }
        throw new Error("No task_id returned");
      }

      // 2. Poll for result every 2s (up to 2 minutes)
      const maxPolls = 60;
      for (let i = 0; i < maxPolls; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        const pr = await apiFetch(`/api/agent/result/${taskId}`);
        if (!pr.ok) throw new Error(`Poll failed: ${pr.status}`);
        const poll = await pr.json();
        console.log("[agent] poll", i, poll.status, poll.elapsed_seconds ?? "");
        if (poll.status === "pending") continue;
        if (poll.status === "error") throw new Error(poll.error || "Agent failed");
        if (poll.status === "done" && poll.result) {
          setAgentResults((prev) => ({
            ...prev,
            [jobKey]: { ...(prev[jobKey] || {}), [agentId]: { data: poll.result } },
          }));
          return;
        }
        throw new Error("Unexpected poll response");
      }
      throw new Error("Agent timed out after 2 minutes");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Agent failed";
      console.error("[agent] error:", msg);
      setAgentResults((prev) => ({
        ...prev,
        [jobKey]: { ...(prev[jobKey] || {}), [agentId]: { error: msg } },
      }));
    } finally {
      setAgentBusy(null);
    }
  }

  async function saveFeedback(job: JobRow, value: "good" | "bad" | "none") {
    const url = job.url;
    setFeedback((prev) => {
      const next = { ...prev };
      if (value === "none") {
        delete next[url];
      } else {
        next[url] = value;
      }
      return next;
    });
    try {
      await apiFetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, title: job.title, company: job.company, feedback: value }),
      });
    } catch {
      // non-fatal
    }
  }

  async function saveApplication(job: JobRow, status: ApplicationStatus) {
    const url = job.url;
    setApplicationsMap((prev) => ({ ...prev, [url]: status }));
    // Keep the settings list reasonably fresh client-side (API is source of truth)
    setApplicationsList((prev) => {
      const next = prev.filter((e) => e.url !== url);
      next.unshift({ url, title: job.title, company: job.company, status });
      return next;
    });
    try {
      await apiFetch("/api/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, title: job.title, company: job.company, status }),
      });
    } catch {
      // non-fatal
    }
  }

  const displayAgents = agents.length > 0 ? agents : FALLBACK_AGENTS;
  const scrapePhaseText = useMemo(() => {
    const phase = scrapeStatus.phase;
    const total = scrapeStatus.scrapers_total ?? 0;
    const done = scrapeStatus.scrapers_done ?? 0;
    if (phase === "scrapers" && total > 0) return `Running scrapers… (${done}/${total} done)`;
    if (phase === "ranking") return "Ranking & deduplicating jobs…";
    if (phase === "saving") return "Saving history…";
    if (phase === "ai_eval") return "AI scoring top jobs…";
    if (phase === "discord") return "Sending Discord notification…";
    if (phase === "done") return "Done.";
    if (phase === "error") return `Error: ${scrapeStatus.error ?? "unknown"}`;
    // fallback: cycling status messages
    const base = SCRAPE_PHASE_MESSAGES[scrapePhaseIdx];
    if (scrapePhaseIdx === SCRAPE_PHASE_MESSAGES.length - 1) {
      const names = displayAgents.map((a) => a.label).join(" · ");
      return names ? `${base} (${names})` : base;
    }
    return base;
  }, [scrapeStatus, scrapePhaseIdx, displayAgents]);

  const scrapeProgressPct = useMemo(() => {
    const phase = scrapeStatus.phase;
    if (!phase || phase === "starting" || phase === "loading") return 5;
    if (phase === "scrapers") {
      const total = scrapeStatus.scrapers_total ?? 1;
      const done = scrapeStatus.scrapers_done ?? 0;
      return Math.round(10 + (done / total) * 70);
    }
    if (phase === "ranking") return 82;
    if (phase === "saving") return 88;
    if (phase === "ai_eval") return 92;
    if (phase === "discord") return 96;
    return 100;
  }, [scrapeStatus]);
  /** Only disable LLM actions when health explicitly reports no API key — not while loading or on fetch failure. */
  const llmDisabled = health.agent === false;

  const todayIso = serverToday ?? isoDate(new Date());

  const filteredJobs = useMemo(
    () => categoryFilter === "all" ? jobs : jobs.filter((j) => (j.category || "crypto") === categoryFilter),
    [jobs, categoryFilter]
  );

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
            <h1 style={{ display: "flex", alignItems: "baseline", gap: "0.5rem" }}>
              Job Scraper
              <span 
                title={`Build time: ${__APP_BUILD_TIME__}`}
                style={{ 
                  fontSize: "0.45em", 
                  color: "var(--muted, #888)", 
                  fontWeight: "normal" 
                }}
              >
                v3.0 (built {new Date(__APP_BUILD_TIME__).toLocaleDateString()} {new Date(__APP_BUILD_TIME__).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})})
              </span>
            </h1>
            <nav className="app-nav" aria-label="Main pages">
              {route === "dashboard" ? (
                <>
                  <span className="active" aria-current="page">
                    Jobs
                  </span>
                  <button
                    type="button"
                    className="app-nav-settings-btn"
                    title="Opens settings in a new browser tab"
                    onClick={openSettingsInNewTab}
                  >
                    Settings
                  </button>
                </>
              ) : (
                <>
                  <a href={import.meta.env.BASE_URL}>Jobs</a>
                  <span className="active" aria-current="page">
                    Settings
                  </span>
                </>
              )}
            </nav>
            {route === "dashboard" ? (
              <>
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
              </>
            ) : (
              <p className="header-route-hint">
                LLM models, resume ranker, notifications, Discord, and the list of scraped sites. Use{" "}
                <strong>Jobs</strong> above to return to the calendar.
              </p>
            )}
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
        {route === "dashboard" && scrapePolling && (
          <div className="scrape-progress" aria-busy="true">
            <div className="scrape-progress-track">
              <div className="scrape-progress-bar" style={{ width: `${scrapeProgressPct}%` }} />
            </div>
            <p className="scrape-progress-label">{scrapePhaseText}</p>
          </div>
        )}
        {route === "dashboard" && scrapeMsg && !scrapePolling && (
          <div className={`scrape-msg${scrapeStatus.error ? " scrape-msg-error" : ""}`}>
            <span>{scrapeMsg}</span>
            {(scrapeStatus.scraper_results ?? []).length > 0 && (
              <button
                type="button"
                className="debug-toggle"
                onClick={() => setScrapeDebugOpen((v) => !v)}
              >
                {scrapeDebugOpen ? "Hide debug ▲" : "Show debug ▼"}
              </button>
            )}
          </div>
        )}
        {route === "dashboard" && scrapeMsg && scrapePolling && (
          <p className="scrape-msg scrape-msg-muted">{scrapeMsg}</p>
        )}
        {route === "dashboard" && !scrapePolling && scrapeDebugOpen && (scrapeStatus.scraper_results ?? []).length > 0 && (
          <div className="scrape-debug">
            {(scrapeStatus.scraper_results ?? []).map((s) => {
              const icon = s.status === "done" && s.jobs_found > 0 ? "✓" : s.status === "done" ? "○" : s.status === "error" ? "✗" : s.status === "running" ? "●" : "·";
              const cls = s.status === "done" && s.jobs_found > 0 ? "dbg-ok" : s.status === "error" ? "dbg-err" : s.status === "done" ? "dbg-warn" : "dbg-muted";
              return (
                <div key={s.name} className={`dbg-row ${cls}`}>
                  <span className="dbg-icon">{icon}</span>
                  <span className="dbg-name">{s.name}</span>
                  <span className="dbg-cat">{s.category}</span>
                  <span className="dbg-count">{s.status === "error" ? s.error ?? "error" : `${s.jobs_found} jobs`}</span>
                </div>
              );
            })}
          </div>
        )}
      </header>

      {route === "settings" ? (
        <SettingsPage
          dashStatus={dashStatus}
          statusHints={statusHints}
          statusCheckedAt={statusCheckedAt}
          refreshDashboardStatus={refreshDashboardStatus}
          discordNotificationsEnabled={discordNotificationsEnabled}
          discordToggleSaving={discordToggleSaving}
          saveDiscordNotifications={saveDiscordNotifications}
          llmDisabled={llmDisabled}
          agentDiagnostics={agentDiagnostics}
          agentTestResults={agentTestResults}
          agentTestLoading={agentTestLoading}
          runAgentModelTests={runAgentModelTests}
          profile={profile}
          profileMsg={profileMsg}
          resumeFileRef={resumeFileRef}
          resumeFileName={resumeFileName}
          setResumeFileName={setResumeFileName}
          uploadResumeFile={uploadResumeFile}
          reviewResumeProfile={reviewResumeProfile}
          reviewFeedbackProfile={reviewFeedbackProfile}
          clearRankerOverrides={clearRankerOverrides}
          resumeUploading={resumeUploading}
          reviewBusy={reviewBusy}
          scrapeSources={scrapeSources}
          applications={applicationsList}
        />
      ) : null}

      {route === "dashboard" ? (
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
              <span>
                {" "}· Agents: set OPENROUTER_API_KEY or OPENAI_API_KEY —{" "}
                <a
                  href="/api/agent/debug"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--danger)", textDecoration: "underline" }}
                >
                  debug info
                </a>
              </span>
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
              {loading ? "Loading…" : `${filteredJobs.length}${categoryFilter !== "all" ? `/${jobs.length}` : ""} job${filteredJobs.length === 1 ? "" : "s"}`}
            </span>
          </div>

          <div className="cat-filters">
            {(["all", "crypto", "cruise", "general"] as const).map((cat) => (
              <button
                key={cat}
                type="button"
                className={`cat-filter-btn${categoryFilter === cat ? " active" : ""}`}
                onClick={() => setCategoryFilter(cat)}
              >
                {cat === "all" ? "All" : cat.charAt(0).toUpperCase() + cat.slice(1)}
              </button>
            ))}
          </div>

          {jobsErr && <p className="err">{jobsErr}</p>}
          {!loading && !jobsErr && jobs.length === 0 && (
            <p className="empty">
              No jobs stored for this day yet. History is written when the scraper finds{" "}
              <strong>new</strong> listings (use <strong>Scrape now</strong> above, or wait for the
              scheduled run). Days before this change have no archive.
            </p>
          )}
          {!loading && !jobsErr && jobs.length > 0 && filteredJobs.length === 0 && (
            <p className="empty">No {categoryFilter} jobs for this day.</p>
          )}

          {filteredJobs.map((job) => {
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
                  <div className="apply-btns">
                    <button
                      type="button"
                      className={`apply-btn${applicationsMap[job.url] === "applied" ? " active applied" : ""}`}
                      title="Mark as applied"
                      onClick={() => saveApplication(job, "applied")}
                    >
                      Apply
                    </button>
                    <button
                      type="button"
                      className={`apply-btn${applicationsMap[job.url] === "not_applied" ? " active not-applied" : ""}`}
                      title="Mark as not applied"
                      onClick={() => saveApplication(job, "not_applied")}
                    >
                      Not apply
                    </button>
                  </div>
                  <div className="feedback-btns">
                    <button
                      type="button"
                      className={`fb-btn${feedback[job.url] === "good" ? " fb-good active" : " fb-good"}`}
                      title="Good match"
                      onClick={() => saveFeedback(job, feedback[job.url] === "good" ? "none" : "good")}
                    >👍</button>
                    <button
                      type="button"
                      className={`fb-btn${feedback[job.url] === "bad" ? " fb-bad active" : " fb-bad"}`}
                      title="Not interested"
                      onClick={() => saveFeedback(job, feedback[job.url] === "bad" ? "none" : "bad")}
                    >👎</button>
                  </div>
                </div>
                <div className="agent-row">
                    <span className="agent-row-label">AI analysis</span>
                    {llmDisabled ? (
                      <span className="agent-no-key">
                        Set <code>OPENROUTER_API_KEY</code> in .env to enable
                      </span>
                    ) : (
                      displayAgents.map((a) => {
                        const busyKey = `${key}::${a.id}`;
                        const isBusy = agentBusy === busyKey;
                        const anyBusy = agentBusy !== null && agentBusy.startsWith(`${key}::`);
                        return (
                          <button
                            key={a.id}
                            type="button"
                            className="agent-btn"
                            disabled={anyBusy}
                            onClick={() => runAgent(job, a.id)}
                          >
                            <span className="agent-btn-label">{isBusy ? "Thinking…" : a.label}</span>
                            {!isBusy && a.description && (
                              <span className="agent-btn-desc">{a.description}</span>
                            )}
                          </button>
                        );
                      })
                    )}
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
      ) : null}
    </div>
  );
}
