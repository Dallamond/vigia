/* Vista «En vivo»: vídeo, actividad reciente, personas ahora y salud. */
import { $, bytes, claseEvento, duracion, esc } from './nucleo.js';

const TAMANOS = {
  s: 'min(100%,420px)',
  m: 'min(100%,760px)',
  l: 'min(100%,1024px)',
  xl: 'min(100%,1400px)',
  fit: '100%',
};

let vivoReciente = 0;

function tarjetaPersona(p) {
  const nombre = p.nombre || `Persona #${p.id}`;
  const inicial = (p.nombre || String(p.id)).trim().charAt(0).toUpperCase();
  const act = p.actividad && p.actividad !== 'desconocida' ? p.actividad : 'sin clasificar';
  const conf = Number.isFinite(p.conf) ? `<span class="pc-conf" title="confianza de detección">${Math.round(p.conf * 100)}%</span>` : '';
  const sub = [p.zona ? `zona ${esc(p.zona)}` : '', Number.isFinite(p.desde_seg) ? `desde hace ${esc(duracion(p.desde_seg))}` : '']
    .filter(Boolean).join(' · ');
  return `<div class="pcard ${p.nombre ? 'conocida' : ''}">
    <span class="pc-av">${esc(inicial)}</span>
    <span class="pc-main"><b>${esc(nombre)}</b><small>${sub || '&nbsp;'}</small></span>
    <span class="pc-act">${esc(act)}</span>${conf}
  </div>`;
}

function pintarPersonasVivo(d) {
  const personas = (d && d.personas) || [];
  vivoReciente = Date.now();
  const html = personas.length ? personas.map(tarjetaPersona).join('') : '<div class="empty">sin personas</div>';
  for (const id of ['acts', 'actsPersonas']) {
    const el = $(id);
    if (el) el.innerHTML = html;
  }
  const chip = $('pPersChip');
  if (chip) chip.textContent = personas.length;
}

export function iniciarVivo(fuenteDatos) {
  window.addEventListener('vigia:vivo', (e) => pintarPersonasVivo(e.detail));
  let guardado = null;
  try { guardado = localStorage.getItem('vigia.video'); } catch (e) { /* modo privado */ }
  aplicarTamano(guardado || 'fit');

  document.querySelectorAll('#segSize button').forEach((b) => {
    b.addEventListener('click', () => aplicarTamano(b.dataset.s));
  });
  const full = $('btnFull');
  if (full) {
    full.addEventListener('click', () => {
      const img = $('video');
      if (document.fullscreenElement) document.exitFullscreen();
      else if (img && img.requestFullscreen) img.requestFullscreen();
    });
  }

  fuenteDatos.al('estado', ({ estado, eventos, actividades }) => pintar(eventos, actividades, estado));
  fuenteDatos.al('salud', pintarSalud);

  return {
    entrar() {
      const img = $('video');
      if (img && !img.getAttribute('src')) img.src = '/video.mjpg';
      if (fuenteDatos.salud) pintarSalud(fuenteDatos.salud);
    },
    salir() {
      // Corta el MJPEG al cambiar de vista: evita descargar vídeo de fondo.
      const img = $('video');
      if (img) img.removeAttribute('src');
    },
  };
}

export function aplicarTamano(clave) {
  const k = TAMANOS[clave] ? clave : 'm';
  document.documentElement.style.setProperty('--vw', TAMANOS[k]);
  document.body.classList.toggle('wide', k === 'xl');
  document.querySelectorAll('#segSize button').forEach((b) => {
    const on = b.dataset.s === k;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  });
  try { localStorage.setItem('vigia.video', k); } catch (e) { /* modo privado */ }
}

function pintar(eventos, actividades, estado) {
  const feed = $('feed');
  if (feed) {
    feed.innerHTML = eventos.length ? eventos.map((e) => {
      const tipo = e.tipo || '';
      return `<div class="ev ${claseEvento(tipo)}">
        <span class="t">${esc(e.hora || '')}</span>
        <span class="n"><b>${esc(tipo)}</b><small>${esc(e.etiqueta || '')}</small></span>
      </div>`;
    }).join('') : '<div class="empty">sin eventos todavía</div>';
  }
  const cuenta = $('actCount');
  if (cuenta) cuenta.textContent = eventos.length;

  if (Date.now() - vivoReciente < 3000) {
    const cuenta2 = $('actCount');
    if (cuenta2) cuenta2.textContent = eventos.length;
    return;
  }
  const claves = Object.keys(actividades || {});
  const html = claves.length
    ? claves.map((k) => `<div class="act-chip"><b>#${esc(k)}</b><span>${esc(actividades[k])}</span></div>`).join('')
    : '<div class="empty">sin personas</div>';
  const acts = $('acts');
  if (acts) acts.innerHTML = html;
  const actsPersonas = $('actsPersonas');
  if (actsPersonas) actsPersonas.innerHTML = html;
  const chip = $('pPersChip');
  if (chip) chip.textContent = claves.length;

  if (estado) {
    const ahora = $('tlAhora');
    if (ahora) {
      ahora.textContent = estado.personas === 1 ? '1 persona ahora' : `${estado.personas || 0} personas ahora`;
    }
    const stage = $('stage');
    if (stage) stage.classList.toggle('offline', !estado.camara_ok);
  }
}

export function pintarSalud(s) {
  const caja = $('salud');
  const version = $('saludVersion');
  const kpis = $('kpisSalud');
  if (!s) {
    if (caja) caja.innerHTML = '<span>salud: no disponible</span>';
    return;
  }
  if (caja) {
    caja.innerHTML = `
      <span>uptime <b>${esc(duracion(s.uptime_seg))}</b></span>
      <span>BD <b>${bytes(s.db_bytes)}</b> · ${esc(s.eventos ?? 0)} eventos</span>
      <span>clips <b>${esc(s.clips?.n ?? 0)}</b> (${bytes(s.clips?.bytes || 0)})</span>
      <span>disco libre <b>${(s.disco_libre_gb ?? 0).toFixed(1)} GB</b></span>
      <span>v${esc(s.version || '')}</span>`;
  }
  if (version) version.textContent = 'v' + (s.version || '');
  if (kpis) {
    kpis.innerHTML = `
      <div class="kpi"><small>Estado cámara</small><b>${s.camara_ok ? 'OK' : 'caída'}</b></div>
      <div class="kpi"><small>FPS</small><b>${(s.fps || 0).toFixed(1)}</b></div>
      <div class="kpi"><small>Uptime</small><b>${esc(duracion(s.uptime_seg))}</b></div>
      <div class="kpi"><small>Eventos</small><b>${esc(s.eventos ?? 0)}</b></div>
      <div class="kpi"><small>Base de datos</small><b>${bytes(s.db_bytes)}</b></div>
      <div class="kpi"><small>Clips</small><b>${esc(s.clips?.n ?? 0)} · ${bytes(s.clips?.bytes || 0)}</b></div>
      <div class="kpi"><small>Miniaturas</small><b>${esc(s.clips?.miniaturas ?? 0)}</b></div>
      <div class="kpi"><small>Disco libre</small><b>${(s.disco_libre_gb ?? 0).toFixed(1)} GB</b></div>
      <div class="kpi"><small>Último evento</small><b style="font-size:.9rem">${esc(s.ultimo_evento || '—')}</b></div>`;
  }
}
