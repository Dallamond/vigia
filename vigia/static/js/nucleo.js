/* Utilidades comunes del panel: DOM, formato, avisos y modales. */

export const $ = (id) => document.getElementById(id);
export const $$ = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));

export const esc = (texto) => String(texto ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const pad2 = (n) => String(n).padStart(2, '0');

/** Fecha local en formato SQL (AAAA-MM-DD HH:MM:SS), sin pasar por UTC. */
export function fmtSQL(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
         `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

export function hoyLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** "AAAA-MM-DD HH:MM:SS" (o ISO) -> Date local; null si no se entiende. */
export function parseFecha(texto) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(texto || '');
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
}

export function duracion(segundos) {
  const s = Math.max(0, Math.round(segundos || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${m % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export function bytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(0)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

export const media = (fichero) => '/media/' + encodeURIComponent(fichero);

export function horaDe(e) {
  if (!e) return '';
  if (e.marca_pantalla) return e.marca_pantalla;
  if (e.hora) return e.hora;
  return (e.marca_sistema || '').slice(11) || '';
}

export const cuandoDe = (e) => (e && (e.marca_sistema || e.cuando)) || '';

export function claseEvento(tipo) {
  const t = tipo || '';
  if (t.includes('entra') || t.includes('aparece') || t === 'persona_reconocida') return 'entra';
  if (t.includes('salio') || t.includes('desaparece')) return 'salio';
  if (t === 'permanencia' || t === 'cara_desconocida' || t === 'persona_desconocida'
      || t === 'zona_restringida' || t === 'demasiadas_personas'
      || t === 'merodeo' || t === 'persona_inmovil') return 'aviso';
  if (t === 'actividad') return 'act';
  return '';
}

/* ---------- avisos flotantes ---------- */
export function toast(mensaje, ok = true) {
  const caja = $('toasts');
  if (!caja) return;
  const d = document.createElement('div');
  d.className = 'toast' + (ok ? '' : ' err');
  d.textContent = mensaje;
  caja.appendChild(d);
  setTimeout(() => d.remove(), 4600);
}

/* ---------- avisos de la cabecera ---------- */
export function aviso(id, texto, tipo = 'warn') {
  const caja = $(id);
  if (!caja) return;
  const txt = caja.querySelector('span[id]');
  if (txt) txt.textContent = texto;
  caja.classList.toggle('bad', tipo === 'bad');
  caja.hidden = !texto;
}

/* ---------- modales ---------- */
export function abrirModal(id) {
  const m = $(id);
  if (!m) return;
  m.classList.add('open');
  const foco = m.querySelector('input, button, select, textarea, video');
  if (foco) foco.focus({ preventScroll: true });
}

export function cerrarModal(m) {
  if (typeof m === 'string') m = $(m);
  if (!m) return;
  m.classList.remove('open');
  // al cerrar el detalle se vacía: el clip deja de sonar en segundo plano
  if (m.id === 'mDetalle') {
    const cuerpo = $('dCuerpo');
    if (cuerpo) cuerpo.innerHTML = '';
  }
}

export function enlazarModales() {
  $$('[data-close]').forEach((b) => {
    b.addEventListener('click', () => cerrarModal(b.dataset.close));
  });
  $$('.modal').forEach((m) => {
    m.addEventListener('click', (e) => { if (e.target === m) cerrarModal(m); });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $$('.modal.open').forEach(cerrarModal);
  });
}

/* evita que un clic en un enlace con hash recargue la página */
export function esEscribiendo(e) {
  const t = e.target;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
               t.tagName === 'SELECT' || t.isContentEditable);
}
