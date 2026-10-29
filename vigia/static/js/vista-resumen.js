/* Vista «Resumen»: KPIs de hoy y estadísticas de los últimos días.
 * Usa GET /resumen (estado de un vistazo) y GET /estadisticas?dias=N. Gráficas
 * hechas con divs y variables CSS (sin librerías), así heredan el tema. */
import { api } from './api.js';
import { $, bytes, duracion, esc, media } from './nucleo.js';

const COLOR_TIPO = {
  persona_entra: 'var(--ok)',
  persona_salio: 'var(--bad)',
  permanencia: 'var(--warn)',
  actividad: 'var(--violet)',
  objeto_aparece: 'var(--cyan)',
  objeto_desaparece: 'var(--cyan)',
  persona_reconocida: 'var(--ok)',
  cara_desconocida: 'var(--orange)',
  persona_desconocida: 'var(--orange)',
  zona_restringida: 'var(--orange)',
  demasiadas_personas: 'var(--orange)',
  merodeo: 'var(--orange)',
  persona_inmovil: 'var(--orange)',
};
const colorTipo = (t) => COLOR_TIPO[t] || 'var(--accent)';
const etiqueta = (t) => String(t || '').replace(/_/g, ' ');

function kpi(titulo, valor, pie = '', estado = '') {
  return `<div class="kpi-card ${estado}"><small>${esc(titulo)}</small>
    <b>${valor}</b><span>${pie}</span></div>`;
}

function barrasHoras(porHora) {
  const horas = Array.isArray(porHora) && porHora.length === 24 ? porHora : new Array(24).fill(0);
  const max = Math.max(1, ...horas);
  const ahora = new Date().getHours();
  const cols = horas.map((n, h) => {
    const alto = n ? Math.max(4, Math.round((n / max) * 100)) : 0;
    return `<div class="col-h${h === ahora ? ' ahora' : ''}" title="${String(h).padStart(2, '0')}:00 · ${n} evento${n === 1 ? '' : 's'}">
      <i style="height:${alto}%"></i><span>${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span></div>`;
  }).join('');
  return `<div class="hbars" role="img" aria-label="Eventos por hora del día">${cols}</div>`;
}

function barrasH(mapa, { formato = (v) => v, color = colorTipo, vacio = 'sin datos' } = {}) {
  const pares = Object.entries(mapa || {}).sort((a, b) => b[1] - a[1]);
  if (!pares.length) return `<div class="empty">${vacio}</div>`;
  const max = Math.max(...pares.map(([, v]) => v), 1);
  return `<div class="rbars">${pares.map(([k, v]) => `
    <div class="rb"><span class="rb-l" title="${esc(k)}">${esc(etiqueta(k))}</span>
      <span class="rb-t"><i style="width:${Math.max(2, (v / max) * 100)}%;background:${color(k)}"></i></span>
      <span class="rb-v">${esc(formato(v))}</span></div>`).join('')}</div>`;
}

function barrasDias(porDia) {
  if (!Array.isArray(porDia) || !porDia.length) return '<div class="empty">sin datos</div>';
  const max = Math.max(1, ...porDia.map((d) => d.n));
  return `<div class="hbars dias">${porDia.map((d) => `
    <div class="col-h" title="${esc(d.dia)} · ${d.n} eventos">
      <i style="height:${d.n ? Math.max(4, Math.round((d.n / max) * 100)) : 0}%"></i>
      <span>${esc(d.dia.slice(8))}</span></div>`).join('')}</div>`;
}

export function iniciarResumen() {
  let activo = false;
  let dias = 7;
  let timer = null;
  let cargando = false;

  async function cargar() {
    if (cargando || !activo) return;
    cargando = true;
    try {
      const [r, e] = await Promise.all([
        api('/resumen', { tolerante: true }),
        api(`/estadisticas?dias=${dias}`, { tolerante: true }),
      ]);
      pintar(r, e);
    } catch (err) {
      if (err.estado !== 401) {
        const k = $('resKpis');
        if (k) k.innerHTML = `<div class="empty">no se pudo cargar el resumen: ${esc(err.message)}</div>`;
      }
    } finally {
      cargando = false;
    }
  }

  function pintar(r, e) {
    const kp = $('resKpis');
    if (kp) {
      if (!r) {
        kp.innerHTML = '<div class="empty">el servidor no expone /resumen todavía</div>';
      } else {
        const cam = r.camara || {};
        const arm = r.armado || {};
        const tg = r.telegram || {};
        const disco = r.disco || {};
        kp.innerHTML = [
          kpi('Personas ahora', esc(r.aforo_actual ?? cam.personas ?? 0),
            `${cam.personas ?? 0} en cámara`),
          kpi('Eventos hoy', esc(r.eventos_hoy?.total ?? 0),
            `${esc(r.total_eventos ?? 0)} en total`),
          kpi('Cámara', cam.ok ? 'Activa' : 'Caída',
            `${(cam.fps || 0).toFixed(1)} FPS · ${esc(cam.ancho || '–')}×${esc(cam.alto || '–')}`,
            cam.ok ? 'ok' : 'bad'),
          kpi('Armado', arm.armado ? 'Armado' : 'Desarmado',
            arm.auto ? 'automático por franja' : 'manual', arm.armado ? 'ok' : ''),
          kpi('Disco libre', `${(disco.libre_gb ?? 0).toFixed(0)} GB`,
            `BD ${bytes(r.db_bytes)} · uptime ${esc(duracion(r.uptime_seg))}`,
            (disco.libre_gb ?? 99) < 5 ? 'bad' : ''),
          kpi('Telegram', tg.configurado ? (tg.ultimo_error ? 'Con error' : 'Operativo') : 'Sin configurar',
            tg.pendientes ? `${tg.pendientes} pendientes` : esc(tg.ultimo_error || 'sin cola'),
            tg.configurado ? (tg.ultimo_error ? 'bad' : 'ok') : ''),
        ].join('');
      }
    }

    const ult = $('resUltimo');
    if (ult) {
      const u = r?.ultimo_evento;
      if (!u) {
        ult.innerHTML = '<div class="empty">sin eventos todavía</div>';
      } else {
        const img = u.miniatura ? `<img class="thumb-l" src="${media(u.miniatura)}" alt="">` : '';
        ult.innerHTML = `<div class="ult">${img}<div>
          <span class="tag ${esc(u.tipo)}">${esc(etiqueta(u.tipo))}</span>
          <div class="hint ult-t">${esc(u.marca_sistema || u.marca || '')}</div>
          <div>${esc(u.detalle || u.etiqueta || '')}</div></div></div>`;
      }
    }

    const hoy = $('resPorTipoHoy');
    if (hoy) hoy.innerHTML = barrasH(r?.eventos_hoy?.por_tipo, { vacio: 'nada registrado hoy' });

    if (!e) {
      for (const id of ['resHoras', 'resTipos', 'resActividad', 'resDias', 'resPicos']) {
        const el = $(id);
        if (el) el.innerHTML = '<div class="empty">el servidor no expone /estadisticas todavía</div>';
      }
      return;
    }
    const set = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
    set('resHoras', barrasHoras(e.por_hora));
    set('resTipos', barrasH(e.por_tipo, { vacio: 'sin eventos en el periodo' }));
    set('resActividad', barrasH(e.actividades, {
      formato: (v) => duracion(v), color: () => 'var(--violet)', vacio: 'aún no hay actividad registrada',
    }));
    set('resDias', barrasDias(e.por_dia));
    const p = e.picos || {};
    set('resPicos', `<dl class="kv">
      <dt>Eventos</dt><dd>${esc(e.total ?? 0)} en ${esc(e.dias)} día${e.dias === 1 ? '' : 's'}</dd>
      <dt>Personas</dt><dd>${esc(e.personas_distintas ?? 0)} distintas</dd>
      <dt>Ocupación</dt><dd>media ${(e.ocupacion_media ?? 0).toFixed(2)} personas · máx. ${esc(e.ocupacion_max ?? 0)} a la vez</dd>
      <dt>Hora punta</dt><dd>${p.hora ? `${String(p.hora.hora).padStart(2, '0')}:00 (${p.hora.n})` : '—'}</dd>
      <dt>Día punta</dt><dd>${p.dia ? `${esc(p.dia.dia)} (${p.dia.n})` : '—'}</dd>
    </dl>`);
  }

  document.querySelectorAll('#resRango button').forEach((b) => {
    b.addEventListener('click', () => {
      dias = Number(b.dataset.d) || 7;
      document.querySelectorAll('#resRango button').forEach((x) => {
        const on = x === b;
        x.classList.toggle('on', on);
        x.setAttribute('aria-pressed', String(on));
      });
      cargar();
    });
  });
  $('resRefrescar')?.addEventListener('click', cargar);

  return {
    entrar() {
      activo = true;
      cargar();
      if (!timer) timer = setInterval(cargar, 10000);
    },
    salir() {
      activo = false;
      if (timer) { clearInterval(timer); timer = null; }
    },
    cargar,
  };
}
