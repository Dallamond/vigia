/* Vista «Eventos»: historial filtrable con detalle y clip. */
import { $ } from './nucleo.js';
import { enlazarFilas, pintarFilas } from './detalle.js';

export function iniciarEventos(fuenteDatos) {
  const hist = $('hist');
  enlazarFilas(hist);
  let activo = false;
  let cargando = false;
  let huella = '';

  async function cargar() {
    if (cargando) return;
    cargando = true;
    const aviso = $('evAviso');
    if (aviso) aviso.textContent = '';
    try {
      const eventos = await fuenteDatos.historial({
        tipo: $('ftipo')?.value || '',
        desde: $('fdesde')?.value || '',
        limite: $('flimite')?.value || 50,
      });
      pintarFilas(hist, Array.isArray(eventos) ? eventos : [], 'sin eventos con esos filtros');
      const total = $('evTotal');
      if (total) total.textContent = `${(eventos || []).length} evento${(eventos || []).length === 1 ? '' : 's'}`;
    } catch (e) {
      if (e.estado !== 401) {
        if (hist) hist.innerHTML = '<div class="empty">no se pudo cargar el historial</div>';
        if (aviso) aviso.textContent = e.message;
      }
    } finally {
      cargando = false;
    }
  }

  ['ftipo', 'fdesde', 'flimite'].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('change', cargar);
  });
  const refrescar = $('btnRefrescar');
  if (refrescar) refrescar.addEventListener('click', cargar);

  fuenteDatos.al('estado', ({ eventos }) => {
    if (!activo) return;
    const nueva = eventos.length ? eventos[0].hora + eventos[0].tipo + eventos[0].etiqueta : '';
    if (nueva !== huella && huella !== '') cargar();
    huella = nueva;
  });

  return {
    entrar(params) {
      activo = true;
      if (params && params.get('tipo')) {
        const tipo = $('ftipo');
        if (tipo) tipo.value = params.get('tipo');
      }
      cargar();
    },
    salir() { activo = false; },
    cargar,
  };
}
