/* Detalle de evento y filas reutilizables (historial, clips, timeline). */
import { $, abrirModal, cuandoDe, esc, horaDe, media } from './nucleo.js';

export function filaEvento(e, indice) {
  const mini = e.miniatura
    ? `<img class="thumb" src="${media(e.miniatura)}" alt="" loading="lazy" onerror="this.remove()">`
    : '';
  return `<button type="button" class="row" data-evento="${indice}">
    ${mini}
    <span class="g">
      <b>${esc(horaDe(e))} · <span class="tag ${esc(e.tipo)}">${esc(e.tipo)}</span></b>
      <small>${esc(e.detalle || '—')}${e.id_tracking != null ? ' · id ' + esc(e.id_tracking) : ''}</small>
    </span>
    ${e.clip ? '<span class="tag">▶ clip</span>' : ''}
  </button>`;
}

/** Pinta `eventos` en el contenedor y enlaza el clic con el detalle. */
export function pintarFilas(contenedor, eventos, textoVacio = 'sin eventos') {
  if (!contenedor) return;
  contenedor._eventos = eventos;
  contenedor.innerHTML = eventos.length
    ? eventos.map((e, i) => filaEvento(e, i)).join('')
    : `<div class="empty">${esc(textoVacio)}</div>`;
}

export function enlazarFilas(contenedor) {
  if (!contenedor || contenedor._enlazado) return;
  contenedor._enlazado = true;
  contenedor.addEventListener('click', (e) => {
    const fila = e.target.closest('[data-evento]');
    if (!fila) return;
    const evento = (contenedor._eventos || [])[Number(fila.dataset.evento)];
    if (evento) verEvento(evento);
  });
}

export function verEvento(e) {
  if (!e) return;
  const titulo = $('dTitulo');
  if (titulo) titulo.textContent = `${e.tipo || 'Evento'}${e.detalle ? ' — ' + e.detalle : ''}`;

  let html = '';
  if (e.miniatura) {
    html += `<img class="detail-media" src="${media(e.miniatura)}" alt="Miniatura del evento" onerror="this.remove()">`;
  }
  if (e.clip) {
    html += `<video class="detail-media" controls preload="metadata" playsinline src="${media(e.clip)}" data-f="${esc(e.clip)}"></video>`;
  }
  if (!e.miniatura && !e.clip) {
    html += '<p class="empty">Este evento no tiene captura guardada.</p>';
  }
  html += `<dl class="kv">
    <dt>Cuándo</dt><dd>${esc(cuandoDe(e) || '—')}</dd>
    <dt>Tipo</dt><dd><span class="tag ${esc(e.tipo)}">${esc(e.tipo)}</span></dd>
    <dt>Detalle</dt><dd>${esc(e.detalle || '—')}</dd>
    <dt>Persona</dt><dd>${e.id_tracking != null ? '#' + esc(e.id_tracking) : '—'}</dd>
    ${e.camara_id != null ? `<dt>Cámara</dt><dd>${esc(e.camara_id)}</dd>` : ''}
  </dl>`;

  const cuerpo = $('dCuerpo');
  if (!cuerpo) return;
  cuerpo.innerHTML = html;
  cuerpo.querySelectorAll('video').forEach((v) => {
    // el clip puede estar escribiéndose todavía (1-2 s): se reintenta un poco
    let intentos = 0;
    v.addEventListener('error', () => {
      if (intentos++ < 3) {
        setTimeout(() => {
          v.src = media(v.dataset.f) + '?r=' + intentos;
          v.load();
        }, 1500);
      } else {
        const p = document.createElement('p');
        p.className = 'empty';
        p.innerHTML = 'No se puede reproducir este clip (formato antiguo). ' +
          'Ejecuta <code>python -m vigia.mantener --convertir</code> para recodificarlo.';
        v.replaceWith(p);
      }
    });
  });
  abrirModal('mDetalle');
}
