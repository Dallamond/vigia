/* Vista «Clips»: listado de vídeos guardados con reproductor. */
import { $, bytes, esc } from './nucleo.js';
import { verEvento } from './detalle.js';

export function iniciarClips(fuenteDatos) {
  const lista = $('clipList');
  let activo = false;
  let cargando = false;

  async function cargar() {
    if (cargando || !lista) return;
    cargando = true;
    try {
      const clips = await fuenteDatos.clips();
      const total = $('clipsTotal');
      if (total) total.textContent = `${(clips || []).length} clips`;
      lista._clips = Array.isArray(clips) ? clips : [];
      lista.innerHTML = lista._clips.length ? lista._clips.map((c, i) => `
        <button type="button" class="row" data-clip="${i}">
          <span class="g"><b>${esc(c.fichero)}</b>
            <small>${bytes(c.tamano)} · ${esc(c.cuando || '')}</small></span>
          <span class="tag">▶ reproducir</span>
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
      const fila = e.target.closest('[data-clip]');
      if (!fila) return;
      const clip = (lista._clips || [])[Number(fila.dataset.clip)];
      if (clip) {
        verEvento({ tipo: 'clip', detalle: clip.fichero, clip: clip.fichero, cuando: clip.cuando });
      }
    });
  }
  const refrescar = $('btnRefrescarClips');
  if (refrescar) refrescar.addEventListener('click', cargar);

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
