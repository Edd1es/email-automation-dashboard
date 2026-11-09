import { Fragment, useCallback, useEffect, useState } from 'react';

import { api } from './api.js';
import { count, dateTime, generatedLabel, headline, statusLabel } from './format.js';
import Funnel from './Funnel.jsx';
import { RangePicker } from './OverviewPage.jsx';
import TrendChart from './TrendChart.jsx';

const PAGE_SIZE = 25;

const n = (value, one, many = `${one}s`) => `${count(value)} ${value === 1 ? one : many}`;

function summarize(action, result) {
  if (action === 'import') {
    const extra = result.problems?.length ? ` ${n(result.problems.length, 'row needs', 'rows need')} fixing: ${result.problems.slice(0, 3).join(' ')}` : '';
    return `Imported ${n(result.added, 'new recipient')} from ${count(result.found)} found.${extra}`;
  }
  if (action === 'generate') return `Wrote ${n(result.generated, 'message')}. ${count(result.failed)} failed.`;
  if (action === 'send') {
    const remaining = result.remaining ? ` ${n(result.remaining, 'is', 'are')} waiting for the next send.` : '';
    return `Sent ${n(result.sent, 'email')}. ${count(result.failed)} failed, ${count(result.skipped)} skipped.${remaining}`;
  }
  if (action === 'sync') return `Checked ${n(result.checked, 'thread')}. Found ${n(result.replied, 'new reply', 'new replies')} and ${n(result.bounced, 'new bounce')}.`;
  return '';
}

function Workflow({ campaign, status, onRun, busy }) {
  const [source, setSource] = useState('sheet');
  const [query, setQuery] = useState('');
  const [confirming, setConfirming] = useState(false);
  const google = status?.google.configured;
  const ready = campaign.queued;
  const importBody = source === 'gmail' ? { source, query } : { source };

  return (
    <section>
      <h2>Run this campaign</h2>
      {status && !google && (
        <p className="notice">Google isn't connected, so import, send and sync are off. Run <code>npm run py -- -m app.cli google-auth</code>, then reload.</p>
      )}
      {status && !status.groq.configured && (
        <p className="notice">Groq isn't connected, so messages use a plain template. Add <code>GROQ_API_KEY</code> to <code>.env</code> to personalize them.</p>
      )}
      <ol className="workflow">
        <li>
          <h3>Import recipients</h3>
          <div className="workflow-body">
            <label>Source
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="sheet">Google Sheet</option>
                <option value="gmail">Gmail search</option>
              </select>
            </label>
            {source === 'gmail' && (
              <label>Gmail search
                <input value={query} placeholder="label:leads" onChange={(e) => setQuery(e.target.value)} />
              </label>
            )}
            <button type="button" disabled={!google || busy} onClick={() => onRun('import', importBody)}>Import recipients</button>
          </div>
        </li>
        <li>
          <h3>Write messages</h3>
          <div className="workflow-body">
            <button type="button" disabled={busy || !campaign.total} onClick={() => onRun('generate')}>Write messages</button>
            <span className="hint">Review them in the list below before sending.</span>
          </div>
        </li>
        <li>
          <h3>Send</h3>
          <div className="workflow-body">
            {!confirming && (
              <button type="button" disabled={!google || busy || !ready} onClick={() => setConfirming(true)}>Send emails</button>
            )}
            {confirming && (
              <>
                <span>Send up to {n(Math.min(ready, status?.max_send_per_run ?? ready), 'real email')} now?</span>
                <button type="button" className="primary" onClick={() => { setConfirming(false); onRun('send'); }}>Confirm and send</button>
                <button type="button" className="text-button" onClick={() => setConfirming(false)}>Cancel</button>
              </>
            )}
          </div>
        </li>
        <li>
          <h3>Check for replies and bounces</h3>
          <div className="workflow-body">
            <button type="button" disabled={!google || busy || !campaign.sent} onClick={() => onRun('sync')}>Check Gmail</button>
          </div>
        </li>
      </ol>
    </section>
  );
}

function Insight({ insight, onGenerate, busy, error }) {
  return (
    <section className="insight">
      <div className="section-head">
        <h2>What to do next</h2>
        <button type="button" disabled={busy} onClick={onGenerate}>{insight ? 'Refresh insight' : 'Generate insight'}</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {insight ? (
        <>
          <ul>{insight.observations.map((item) => <li key={item}>{item}</li>)}</ul>
          <p className="hint">{generatedLabel(insight.generated_by)}, generated {dateTime(insight.created_at)}. Based on this campaign's totals only.</p>
        </>
      ) : (
        <p>Generate an insight to compare this campaign with your account's averages.</p>
      )}
    </section>
  );
}

function engagementSummary(message) {
  if (message.bounced) return 'Bounced';
  const parts = [message.replied && 'Replied', message.clicked && 'Clicked', message.opened && 'Opened'].filter(Boolean);
  return parts[0] ?? (message.status === 'sent' ? 'No activity' : '');
}

function Messages({ id, version, refreshKey, onApiError }) {
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.messages(id, page * PAGE_SIZE, PAGE_SIZE)
      .then((result) => { if (!cancelled) setData(result); })
      .catch(onApiError);
    return () => { cancelled = true; };
  }, [id, page, version, refreshKey, onApiError]);

  if (!data) return null;
  if (!data.total) return <section><h2>Messages</h2><p>Import recipients to start this campaign.</p></section>;
  const pages = Math.ceil(data.total / PAGE_SIZE);

  return (
    <section>
      <h2>Messages</h2>
      <div className="table-scroll">
        <table className="messages">
          <thead>
            <tr><th scope="col">Recipient</th><th scope="col">Company</th><th scope="col">Status</th><th scope="col">Activity</th><th scope="col"><span className="visually-hidden">Preview</span></th></tr>
          </thead>
          <tbody>
            {data.items.map((message) => (
              <Fragment key={message.id}>
                <tr>
                  <th scope="row">{[message.first_name, message.last_name].filter(Boolean).join(' ') || message.email}<div className="hint">{message.email}</div></th>
                  <td>{message.company}</td>
                  <td>{statusLabel(message.status)}{message.error && <div className="hint">{message.error}</div>}</td>
                  <td>{engagementSummary(message)}</td>
                  <td>
                    {message.body && (
                      <button type="button" className="text-button" aria-expanded={open === message.id} onClick={() => setOpen(open === message.id ? null : message.id)}>
                        {open === message.id ? 'Hide' : 'Read'}
                      </button>
                    )}
                  </td>
                </tr>
                {open === message.id && (
                  <tr className="message-preview">
                    <td colSpan={5}>
                      <strong>{message.subject}</strong>
                      <pre>{message.body}</pre>
                      <p className="hint">{generatedLabel(message.generated_by)}</p>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pager">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span>Page {page + 1} of {pages}</span>
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </section>
  );
}

export default function CampaignPage({ id, version, onApiError }) {
  const [campaign, setCampaign] = useState(null);
  const [trend, setTrend] = useState([]);
  const [status, setStatus] = useState(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  const [insightBusy, setInsightBusy] = useState(false);
  const [insightError, setInsightError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(() => Promise.all([api.campaign(id), api.trend(days, id)])
    .then(([detail, rows]) => {
      setCampaign(detail);
      setTrend(rows);
      setError('');
    })
    .catch((problem) => {
      onApiError(problem);
      setError(problem.message);
    }), [id, days, onApiError]);

  useEffect(() => { load(); }, [load, version, refreshKey]);
  useEffect(() => { api.status().then(setStatus).catch(onApiError); }, [onApiError]);

  async function run(action, body) {
    setBusy(true);
    setResult('');
    setError('');
    try {
      setResult(summarize(action, await api.action(id, action, body)));
    } catch (problem) {
      onApiError(problem);
      setError(problem.message);
    } finally {
      setBusy(false);
      setRefreshKey((key) => key + 1);
    }
  }

  async function generateInsight() {
    setInsightBusy(true);
    setInsightError('');
    try {
      const insight = await api.action(id, 'insights');
      setCampaign((current) => ({ ...current, insight }));
    } catch (problem) {
      onApiError(problem);
      setInsightError(problem.message);
    } finally {
      setInsightBusy(false);
    }
  }

  if (error && !campaign) {
    return <><a href="#/">All campaigns</a><p className="error" role="alert">{error}</p></>;
  }
  if (!campaign) return <p className="page-loading">Loading campaign</p>;

  return (
    <>
      <a className="back" href="#/">All campaigns</a>
      <p className="campaign-name">
        {campaign.name}
        {campaign.is_sample === 1 && <span className="tag">Sample</span>}
      </p>
      {campaign.is_sample === 1 && <p className="sample-note">These results are made up, and this campaign can't send email.</p>}
      <h1 className="headline">{headline(campaign)}</h1>
      <Funnel metrics={campaign} />

      <Insight insight={campaign.insight} onGenerate={generateInsight} busy={insightBusy} error={insightError} />

      {campaign.is_sample !== 1 && (
        <>
          {error && <p className="error" role="alert">{error}</p>}
          {result && <p className="result" role="status">{result}</p>}
          <Workflow campaign={campaign} status={status} onRun={run} busy={busy} />
        </>
      )}

      <section>
        <div className="section-head">
          <h2>Engagement by day</h2>
          <RangePicker days={days} onChange={setDays} />
        </div>
        <TrendChart rows={trend} />
      </section>

      <Messages id={id} version={version} refreshKey={refreshKey} onApiError={onApiError} />
    </>
  );
}
