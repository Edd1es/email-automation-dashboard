const numberFormat = new Intl.NumberFormat('en-US');

export const count = (value) => numberFormat.format(value ?? 0);

export function percent(value) {
  if (value === null || value === undefined) return 'None yet';
  return `${(value * 100).toFixed(1)}%`;
}

const plural = (n, one, many) => `${count(n)} ${n === 1 ? one : many}`;

/** The plain-language headline for a set of metrics. */
export function headline(metrics) {
  if (!metrics?.sent) return 'Nothing has been sent yet.';
  const arrived = metrics.delivered === metrics.sent && metrics.sent > 1
    ? `all ${count(metrics.delivered)} arrived`
    : `${count(metrics.delivered)} arrived`;
  const replies = metrics.replied === 0
    ? 'no one has replied yet'
    : `${plural(metrics.replied, 'person', 'people')} replied`;
  return `Of ${plural(metrics.sent, 'email', 'emails')} sent, ${arrived} and ${replies}.`;
}

export function funnelStages(metrics) {
  return [
    { key: 'sent', label: 'Sent', value: metrics.sent, note: 'Accepted by Gmail' },
    { key: 'delivered', label: 'Delivered', value: metrics.delivered, rate: metrics.delivery_rate, note: 'of sent, with no bounce' },
    { key: 'opened', label: 'Opened', value: metrics.opened, rate: metrics.opened_rate, note: 'of delivered. Mail privacy tools can inflate this' },
    { key: 'clicked', label: 'Clicked', value: metrics.clicked, rate: metrics.clicked_rate, note: 'of delivered' },
    { key: 'replied', label: 'Replied', value: metrics.replied, rate: metrics.replied_rate, note: 'of delivered' },
  ];
}

export function shortDate(day) {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
  });
}

export function dateTime(value) {
  if (!value) return '';
  return new Date(value).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

const GENERATED_LABELS = { template: 'Plain template', rules: 'Rule-based summary' };
export const generatedLabel = (value) => GENERATED_LABELS[value] ?? `Groq, ${value}`;

const STATUS_LABELS = {
  pending: 'Not written yet',
  ready: 'Ready to send',
  generation_failed: 'Writing failed',
  sending: 'Sending',
  sent: 'Sent',
  failed: 'Send failed',
  skipped: 'Skipped',
  draft: 'Draft',
};
export const statusLabel = (value) => STATUS_LABELS[value] ?? value;
