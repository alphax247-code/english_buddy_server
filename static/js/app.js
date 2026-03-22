/* ===== English Buddy – shared JS ===== */

const API = {
  base: '',

  async request(method, path, body = null) {
    const token = localStorage.getItem('token');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(this.base + path, opts);
    const data = await res.json().catch(() => ({}));

    if (!res.ok) throw { status: res.status, detail: data.detail || 'Request failed' };
    return data;
  },

  get:    (p)    => API.request('GET', p),
  post:   (p, b) => API.request('POST', p, b),
  put:    (p, b) => API.request('PUT', p, b),
  delete: (p)    => API.request('DELETE', p),
};

/* ---- helpers ---- */
function showAlert(containerId, message, type = 'danger') {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
  setTimeout(() => { el.innerHTML = ''; }, 6000);
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  btn.dataset.originalText = btn.dataset.originalText || btn.textContent;
  btn.textContent = loading ? 'Please wait…' : btn.dataset.originalText;
}

function formatDate(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString();
}

function formatAmount(n) {
  return Number(n).toLocaleString('pt-MZ', { style: 'currency', currency: 'MZN' });
}

function authGuard() {
  const token = localStorage.getItem('token');
  if (!token) { window.location.href = '/'; return false; }
  return true;
}

function logout() {
  localStorage.removeItem('token');
  window.location.href = '/';
}
