/* Vista «Clips»: galería de vídeos guardados. Cada clip se cruza con su evento
 * (miniatura, tipo, detalle) cuando existe; los huérfanos salen sin imagen. */
import { api } from './api.js';
import { $, bytes, esc, media } from './nucleo.js';
import { verEvento } from './detalle.js';

/** El tipo del evento va en el nombre: 20261028_104033_0063_objeto_desaparece.webm */
function tipoDeFichero(f) {
  const m = /^\d{8}_\d{6}(?:_\d+)?(?:_c\w+)?_([a-z_]+)\.\w+$/.exec(f || '');
  return m ? m[1] : 'clip';
}

export function iniciarClips(fuenteDatos) {
  const lista = $('clipList');
  let activo = false;
  let cargando = false;

  async function cargar() {
    if (cargando || !lista) return;
    cargando = true;
    try {
      const [clips, ev] = await Promise.all([
        fuenteDatos.clips(),
        api('/eventos?limite=500', { tolerante: true }).catch(() => null),
      ]);
      const eventos = Array.isArray(ev) ? ev : (ev && ev.eventos) || [];
      const porClip = new Map(eventos.filter((e) => e.clip).map((e) => [e.clip, e]));
      const items = (Array.isArray(clips) ? clips : []).map((c) => {
        const e = porClip.get(c.fichero);
        return { clip: c, evento: e || { tipo: tipoDeFichero(c.fichero), detalle: c.fichero, clip: c.fichero, cuando: c.cuando } };
      });
      const total = $('clipsTotal');
      if (total) total.textContent = `${items.length} clips`;
      lista._items = items;
      lista.innerHTML = items.length ? items.map(({ clip, evento }, i) => `
        <button type="button" class="clip-card" data-clip="${i}">
          <span class="clip-img">
            ${evento.miniatura ? `<img src="${media(evento.miniatura)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
            <span class="clip-play" aria-hidden="true"></span>
          </span>
          <span class="clip-meta">
            <span class="tag ${esc(evento.tipo)}">${esc(String(evento.tipo).replace(/_/g, ' '))}</span>
            <b>${esc(evento.detalle && evento.detalle !== clip.fichero ? evento.detalle : (clip.cuando || ''))}</b>
            <small>${esc(clip.cuando || '')} · ${bytes(clip.tamano)}</small>
          </span>
        </button>`).join('')
        : '<div class="empty">sin clips todavía</div>';
    } catch (e) {
      if (e.estado !== 401) lista.innerHTML = '<div class="empty">no se pudo cargar la lista de clips</div>';
    } finally {
      cargando = false;
    }
  }

  if (lista && !lista._enlazado) {
    lista._enlazado = true;
    lista.addEventListener('click', (e) => {
      const tarjeta = e.target.closest('[data-clip]');
      if (!tarjeta) return;
      const item = (lista._items || [])[Number(tarjeta.dataset.clip)];
      if (item) verEvento(item.evento);
    });
  }
  $('btnRefrescarClips')?.addEventListener('click', cargar);

  let huella = '';
  fuenteDatos.al('estado', ({ eventos }) => {
    if (!activo) return;
    const nueva = eventos.length ? eventos[0].hora + eventos[0].tipo + eventos[0].etiqueta : '';
    if (nueva !== huella && huella !== '') cargar();
    huella = nueva;
  });

  return {
    entrar() { activo = true; cargar(); },
    salir() { activo = false; },
    cargar,
  };
}
