import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

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

  const countByDate = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of summaries) {
      m.set(s.date, s.count);
    }
    return m;
  }, [summaries]);

  const loadSummaries = useCallback(async () => {
    const r = await fetch("/api/dates");
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
      const r = await fetch("/api/agents");
      if (r.ok) {
        const data = await r.json();
        setAgents(data.agents ?? []);
      }
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    loadHealth();
    loadAgents();
    loadSummaries().catch(() => setSummaries([]));
  }, [loadSummaries, loadHealth, loadAgents]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setJobsErr(null);
    fetch(`/api/jobs/${selectedDate}`)
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
  }, [selectedDate]);

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
      const r = await fetch("/api/agent/evaluate", {
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
  const agentsReady = health.agent === true || health.agents_enabled === true;

  const todayIso = isoDate(new Date());

  return (
    <div>
      <header className="app-header">
        <h1>Job Scraper</h1>
        <p>
          Today’s matches and past days on the calendar. Ranking fixes live in{" "}
          <code style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "0.88em" }}>
            main.py
          </code>{" "}
          (JobRanker).
        </p>
      </header>

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
              <strong>new</strong> listings (run <code>python main.py --once</code>). Days before
              this change have no archive.
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
