import type { Ref } from "react";

type PillState = "loading" | "ok" | "warn" | "error";

export type SettingsPageProps = {
  dashStatus: {
    api: PillState;
    llm: PillState;
    jobs: PillState;
    schedule: PillState;
    profile: PillState;
    auth: PillState;
  };
  statusHints: Record<string, string>;
  statusCheckedAt: Date | null;
  refreshDashboardStatus: () => Promise<void>;
  discordNotificationsEnabled: boolean | null;
  discordToggleSaving: boolean;
  saveDiscordNotifications: (enabled: boolean) => Promise<void>;
  llmDisabled: boolean;
  agentDiagnostics: {
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
  } | null;
  agentTestResults: {
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
  } | null;
  agentTestLoading: boolean;
  runAgentModelTests: () => Promise<void>;
  profile: {
    has_resume: boolean;
    resume_chars: number;
    overrides_updated_at: string | null;
    last_summary: string;
    override_counts: Record<string, number>;
  } | null;
  profileMsg: string | null;
  resumeFileRef: Ref<HTMLInputElement>;
  resumeFileName: string | null;
  setResumeFileName: (name: string | null) => void;
  uploadResumeFile: () => Promise<void>;
  reviewResumeProfile: () => Promise<void>;
  clearRankerOverrides: () => Promise<void>;
  resumeUploading: boolean;
  reviewBusy: boolean;
  scrapeSources: Array<{ category: string; name: string; base_url: string }> | null;
};

function categoryLabel(cat: string): string {
  if (cat === "crypto") return "Crypto / Web3";
  if (cat === "cruise") return "Cruise / maritime IT";
  return "General / remote";
}

export function SettingsPage(props: SettingsPageProps) {
  const {
    dashStatus,
    statusHints,
    statusCheckedAt,
    refreshDashboardStatus,
    discordNotificationsEnabled,
    discordToggleSaving,
    saveDiscordNotifications,
    llmDisabled,
    agentDiagnostics,
    agentTestResults,
    agentTestLoading,
    runAgentModelTests,
    profile,
    profileMsg,
    resumeFileRef,
    resumeFileName,
    setResumeFileName,
    uploadResumeFile,
    reviewResumeProfile,
    clearRankerOverrides,
    resumeUploading,
    reviewBusy,
    scrapeSources,
  } = props;

  return (
    <div className="settings-page">
      <p className="settings-page-lead">
        Service health, LLM models, resume-based ranker overrides, Discord, and the job sites scraped by{" "}
        <code className="schedule-env">main.py</code>.
      </p>

      <section
        className="dashboard-status settings-section"
        role="region"
        aria-label="Service status"
        aria-live="polite"
      >
        <h2 className="settings-section-title">System status</h2>
        <div className="dashboard-status-inner">
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
      </section>

      <section className="sources-panel settings-section" aria-label="Scraped websites">
        <h2 className="settings-section-title">Scraped websites</h2>
        <p className="sources-lead">
          These sources run in parallel during each scrape (same registry as <code className="schedule-env">scrape_all_jobs</code>{" "}
          in <code className="schedule-env">main.py</code>).
        </p>
        {!scrapeSources && <p className="llm-agents-muted">Loading…</p>}
        {scrapeSources && scrapeSources.length === 0 && (
          <p className="llm-agents-muted">No sources returned.</p>
        )}
        {scrapeSources && scrapeSources.length > 0 ? (
          <table className="sources-table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Source</th>
                <th>Base URL</th>
              </tr>
            </thead>
            <tbody>
              {scrapeSources.map((s, i) => (
                <tr key={`${s.name}-${i}`}>
                  <td>{categoryLabel(s.category)}</td>
                  <td>{s.name}</td>
                  <td>
                    {s.base_url ? (
                      <a href={s.base_url} target="_blank" rel="noreferrer noopener" className="sources-link">
                        {s.base_url}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      <section className="llm-agents-panel settings-section" aria-label="LLM models and connectivity">
        <h2 className="settings-section-title">LLM agents &amp; models</h2>
        <div className="llm-agents-head">
          <span className="llm-agents-head-spacer" />
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

      <section className="profile-panel settings-section" aria-label="Resume and search profile">
        <h2 className="settings-section-title">Resume &amp; search ranking</h2>
        <p className="profile-lead">
          Upload your CV as PDF. An LLM compares it to the built-in JobRanker lists in{" "}
          <code className="schedule-env">main.py</code> and saves <strong>additional</strong> title and keyword phrases to{" "}
          <code className="schedule-env">data/ranker_overrides.json</code>. The next scrape uses those extras so matches align
          better with your background. After upload, run <strong>Update ranker from resume</strong> — until then, “Extra phrases”
          stays none.
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
          <button type="button" className="profile-btn-secondary" onClick={() => void clearRankerOverrides()}>
            Clear overrides
          </button>
        </div>
        {llmDisabled && (
          <p className="profile-hint">Set OPENROUTER_API_KEY or OPENAI_API_KEY to run resume review.</p>
        )}
        {profile && (
          <p className="profile-meta">
            Resume: {profile.has_resume ? `${profile.resume_chars} characters stored` : "none"} · Extra phrases:{" "}
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
    </div>
  );
}
