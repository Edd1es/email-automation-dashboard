import { useEffect, useState } from 'react';

import { api } from './api.js';
import { count, headline, percent, statusLabel } from './format.js';
import Funnel from './Funnel.jsx';
import TrendChart from './TrendChart.jsx';

export function RangePicker({ days, onChange }) {
  return (
    <div className="range" role="group" aria-label="Date range">
      {[14, 30, 90].map((option) => (
        <button key={option} type="button" aria-pressed={days === option} onClick={() => onChange(option)}>
          {option} days
        </button>
      ))}
    </div>
  );
}

function NewCampaign({ onCancel, onApiError }) {
  const [form, setForm] = useState({ name: '', goal: '', subject_hint: '', sheet_id: '', sheet_range: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const campaign = await api.createCampaign(form);
      window.location.hash = `#/campaigns/${campaign.id}`;
    } catch (problem) {
      onApiError(problem);
      setError(problem.message);
      setBusy(false);
    }
  }

  return (
    <form className="new-campaign" onSubmit={submit}>
      <label>Campaign name
        <input value={form.name} maxLength={120} required autoFocus onChange={set('name')} />
      </label>
      <label>What should the email achieve?
        <textarea value={form.goal} maxLength={2000} rows={3} required onChange={set('goal')}
          placeholder="Invite operators to a 20-minute demo of our new reporting feature." />
      </label>
      <label>Subject guidance (optional)
        <input value={form.subject_hint} maxLength={150} onChange={set('subject_hint')} />
      </label>
      <div className="field-pair">
        <label>Google Sheet ID (optional)
          <input value={form.sheet_id} onChange={set('sheet_id')} />
        </label>
        <label>Sheet range (optional)
          <input value={form.sheet_range} placeholder="Leads!A:F" onChange={set('sheet_range')} />
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="primary" disabled={busy}>Create campaign</button>
        <button type="button" className="text-button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function OverviewPage({ version, onApiError }) {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.overview(), api.campaigns(), api.trend(days)])
      .then(([overview, campaigns, trend]) => {
        if (!cancelled) {
          setData({ overview, campaigns, trend });
          setError('');
        }
      })
      .catch((problem) => {
        onApiError(problem);
        if (!cancelled) setError(problem.message);
      });
    return () => { cancelled = true; };
  }, [days, version, onApiError]);

  if (error && !data) return <p className="error" role="alert">{error}</p>;
  if (!data) return <p className="page-loading">Loading campaigns</p>;
  const { overview, campaigns, trend } = data;

  return (
    <>
      {overview.sample_campaigns > 0 && (
        <p className="sample-note">
          This view includes {count(overview.sample_campaigns)} sample campaigns with made-up results.
          To remove them, run <code>npm run py -- -m app.cli clear-samples</code>.
        </p>
      )}
      {error && <p className="error" role="alert">Showing older numbers. {error}</p>}
      <h1 className="headline">{headline(overview)}</h1>
      <Funnel metrics={overview} />

      <section>
        <div className="section-head">
          <h2>Engagement by day</h2>
          <RangePicker days={days} onChange={setDays} />
        </div>
        <TrendChart rows={trend} />
      </section>

      <section>
        <div className="section-head">
          <h2>Campaigns</h2>
          {!creating && <button type="button" className="primary" onClick={() => setCreating(true)}>New campaign</button>}
        </div>
        {creating && <NewCampaign onCancel={() => setCreating(false)} onApiError={onApiError} />}
        {campaigns.length === 0 && !creating && <p>Create a campaign to import recipients and write your first messages.</p>}
        {campaigns.length > 0 && (
          <div className="table-scroll">
            <table className="campaigns">
              <thead>
                <tr>
                  <th scope="col">Campaign</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">Sent</th>
                  <th scope="col" className="num">Delivered</th>
                  <th scope="col" className="num">Clicked</th>
                  <th scope="col" className="num">Replied</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((campaign) => (
                  <tr key={campaign.id}>
                    <th scope="row">
                      <a href={`#/campaigns/${campaign.id}`}>{campaign.name}</a>
                      {campaign.is_sample === 1 && <span className="tag">Sample</span>}
                    </th>
                    <td>{statusLabel(campaign.status)}</td>
                    <td className="num">{count(campaign.sent)}</td>
                    <td className="num">{campaign.sent ? percent(campaign.delivery_rate) : 'None yet'}</td>
                    <td className="num">{percent(campaign.clicked_rate)}</td>
                    <td className="num">{percent(campaign.replied_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
