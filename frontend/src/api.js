export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'dashboard',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(data?.error ?? `The request failed with status ${response.status}.`, response.status);
  }
  return data;
}

export const api = {
  me: () => request('/auth/me'),
  login: (username, password) => request('/auth/login', { method: 'POST', body: { username, password } }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  status: () => request('/api/status'),
  overview: () => request('/api/overview'),
  trend: (days, campaignId) =>
    request(`/api/trend?days=${days}${campaignId ? `&campaign_id=${campaignId}` : ''}`),
  campaigns: () => request('/api/campaigns'),
  campaign: (id) => request(`/api/campaigns/${id}`),
  messages: (id, offset = 0, limit = 25) =>
    request(`/api/campaigns/${id}/messages?limit=${limit}&offset=${offset}`),
  createCampaign: (body) => request('/api/campaigns', { method: 'POST', body }),
  action: (id, name, body = {}) => request(`/api/campaigns/${id}/${name}`, { method: 'POST', body }),
};
