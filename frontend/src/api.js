// Lightweight API client. The browser sends the auth cookie automatically.

const BASE = ''; // same-origin

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, opts = {}) {
  const res = await fetch(BASE + path, {
    credentials: 'include',
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `שגיאה (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) msg = j.detail;
    } catch (_) {}
    throw new ApiError(msg, res.status);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export const api = {
  // auth
  authStatus: () => request('/api/auth/status'),
  login: (password) => request('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  // categories + providers
  categories: () => request('/api/categories'),
  providers: () => request('/api/providers'),

  // recipes
  listRecipes: () => request('/api/recipes'),
  getRecipe: (id) => request(`/api/recipes/${id}`),
  createRecipe: (body) => request('/api/recipes', { method: 'POST', body: JSON.stringify(body) }),
  updateRecipe: (id, body) => request(`/api/recipes/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRecipe: (id) => request(`/api/recipes/${id}`, { method: 'DELETE' }),
  uploadImage: async (id, file) => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`/api/recipes/${id}/image`, { method: 'POST', credentials: 'include', body: fd });
    if (!res.ok) {
      let msg = `שגיאה (${res.status})`;
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new ApiError(msg, res.status);
    }
    return res.json();
  },
  addStepImage: async (id, file, caption = '') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('caption', caption);
    const res = await fetch(`/api/recipes/${id}/step-images`, { method: 'POST', credentials: 'include', body: fd });
    if (!res.ok) { let msg = `שגיאה (${res.status})`; try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {} throw new ApiError(msg, res.status); }
    return res.json();
  },
  deleteStepImage: (id, index) => request(`/api/recipes/${id}/step-images/${index}`, { method: 'DELETE' }),
  recapture: (id) => request(`/api/recipes/${id}/recapture`, { method: 'POST' }),
  uploadPdf: async (id, file) => {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`/api/recipes/${id}/pdf`, { method: 'POST', credentials: 'include', body: fd });
    if (!res.ok) {
      let msg = `שגיאה (${res.status})`;
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new ApiError(msg, res.status);
    }
    return res.json();
  },
  deleteCapture: (id) => request(`/api/recipes/${id}/capture`, { method: 'DELETE' }),
  batchExtract: (providers, mode) => request('/api/recipes/batch-extract', {
    method: 'POST',
    body: JSON.stringify({ providers, mode }),
  }),
  createShare: (id) => request(`/api/recipes/${id}/share`, { method: 'POST' }),
  revokeShare: (id) => request(`/api/recipes/${id}/share`, { method: 'DELETE' }),

  // extraction
  extract: (url, providers, mode = 'fallback') => request('/api/extract', {
    method: 'POST',
    body: JSON.stringify({ url, providers, mode, capture: true }),
  }),
  extractFile: async (file, providers, mode = 'fallback') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('providers', JSON.stringify(providers));
    fd.append('mode', mode);
    const res = await fetch('/api/extract/file', { method: 'POST', credentials: 'include', body: fd });
    if (!res.ok) {
      let msg = `שגיאה (${res.status})`;
      try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
      throw new ApiError(msg, res.status);
    }
    return res.json();
  },

  // share (public, no auth needed but sent with credentials anyway is harmless)
  shareGet: (token) => fetch(`/api/share/${token}`).then(r => {
    if (!r.ok) throw new ApiError('הקישור לא תקף או הוסר', r.status);
    return r.json();
  }),
};

export { ApiError };
