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
    agent?: boolean;
    agents_enabled?: boolean;
    agents?: AgentMeta[];
  }>({});
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

  const loadHealth = useCallback(async () => {
    try {
      const r = await fetch("/api/health");
      if (r.ok) setHealth(await r.json());
    } catch {
      /* ignore */
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const r = await apiFetch("/api/agents");
      if (r.ok) {
        const data = await r.json();
        setAgents(data.agents ?? []);
      }
    } catch {
      setAgents([]);
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
    loadHealth();
    loadAgents();
    loadSummaries().catch(() => setSummaries([]));
    void loadSchedule();
    void loadProfile();
  }, [authPhase, loadSummaries, loadHealth, loadAgents, loadSchedule, loadProfile]);

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
  const agentsReady = health.agent === true || health.agents_enabled === true;

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

      <section className="profile-panel" aria-label="Resume and search profile">
        <h3 className="profile-heading">Resume &amp; search ranking</h3>
        <p className="profile-lead">
          Upload your CV as PDF. An LLM compares it to the built-in JobRanker lists in{" "}
          <code className="schedule-env">main.py</code> and saves <strong>additional</strong> title and
          keyword phrases to{" "}
          <code className="schedule-env">data/ranker_overrides.json</code>. The next scrape uses those
          extras so matches align better with your background.
        </p>
        <div className="profile-row">
          <input
            ref={resumeFileRef}
            type="file"
            accept="application/pdf,.pdf"
            className="profile-file"
          />
          <button type="button" disabled={resumeUploading} onClick={() => void uploadResumeFile()}>
            {resumeUploading ? "Uploading…" : "Upload PDF"}
          </button>
          <button
            type="button"
            className="profile-review-btn"
            disabled={reviewBusy || !profile?.has_resume || health.agent === false}
            onClick={() => void reviewResumeProfile()}
          >
            {reviewBusy ? "Updating ranker…" : "Update ranker from resume"}
          </button>
          <button type="button" className="sign-out-btn" onClick={() => void clearRankerOverrides()}>
            Clear overrides
          </button>
        </div>
        {health.agent === false && (
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
            {agentsReady && (
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
                    return (
                      <button
                        key={a.id}
                        type="button"
                        className="agent-btn"
                        disabled={!agentsReady || agentBusy === busyKey}
                        title={
                          a.model
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
