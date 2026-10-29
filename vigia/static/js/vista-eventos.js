/* Vista «Eventos»: historial filtrable y paginado, con detalle y clip.
 * Usa GET /eventos con parámetros (total + siguiente_antes_id); si el servidor
 * es antiguo y devuelve una lista, cae al /historial de siempre. */
import { api } from './api.js';
import { $ } from './nucleo.js';
import { enlazarFilas, filaEvento } from './detalle.js';

export function iniciarEventos(fuenteDatos) {
  const hist = $('hist');
  enlazarFilas(hist);
  let activo = false;
  let cargando = false;
  let huella = '';
  let lista = [];
  let siguiente = null;
  let total = null;

  function filtros() {
    const q = new URLSearchParams();
    q.set('limite', $('flimite')?.value || 50);
    const tipo = $('ftipo')?.value;
    const desde = $('fdesde')?.value;
    const persona = $('fpersona')?.value;
    if (tipo) q.set('tipo', tipo);
    if (desde) q.set('desde', desde);
    if (persona) q.set('persona', persona);
    return q;
  }

  function pintar() {
    if (!hist) return;
    hist._eventos = lista;
    hist.innerHTML = lista.length
      ? lista.map((e, i) => filaEvento(e, i)).join('')
      : '<div class="empty">sin eventos con esos filtros</div>';
    const t = $('evTotal');
    if (t) {
      const n = total ?? lista.length;
      const de = total != null && total > lista.length ? ` de ${total}` : '';
      t.textContent = `${lista.length}${de} evento${n === 1 ? '' : 's'}`;
    }
    const mas = $('evMas');
    if (mas) mas.hidden = siguiente == null || (total != null && lista.length >= total);
  }

  async function cargar(masAntiguos = false) {
    if (cargando) return;
    cargando = true;
    const aviso = $('evAviso');
    if (aviso) aviso.textContent = '';
    try {
      const q = filtros();
      if (masAntiguos && siguiente != null) q.set('antes_id', siguiente);
      const r = await api('/eventos?' + q.toString());
      if (Array.isArray(r)) {
        // servidor antiguo: sin paginación
        const h = await fuenteDatos.historial({
          tipo: q.get('tipo') || '', desde: q.get('desde') || '', limite: q.get('limite'),
        });
        lista = Array.isArray(h) ? h : [];
        siguiente = null;
        total = lista.length;
      } else {
        const nuevos = r.eventos || [];
        lista = masAntiguos ? lista.concat(nuevos) : nuevos;
        siguiente = nuevos.length ? (r.siguiente_antes_id ?? null) : null;
        total = r.total ?? lista.length;
      }
      pintar();
    } catch (e) {
      if (e.estado !== 401) {
        if (hist && !masAntiguos) hist.innerHTML = '<div class="empty">no se pudo cargar el historial</div>';
        if (aviso) aviso.textContent = e.message;
      }
    } finally {
      cargando = false;
    }
  }

  ['ftipo', 'fdesde', 'flimite', 'fpersona'].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('change', () => cargar());
  });
  $('btnRefrescar')?.addEventListener('click', () => cargar());
  $('evMas')?.addEventListener('click', () => cargar(true));

  // un evento nuevo recarga la primera página (solo si no se está paginando)
  const nuevoEvento = () => {
    if (activo && lista.length <= Number($('flimite')?.value || 50)) cargar();
  };
  fuenteDatos.al('evento', nuevoEvento);
  fuenteDatos.al('estado', ({ eventos }) => {
    if (!activo) return;
    const nueva = eventos.length ? eventos[0].hora + eventos[0].tipo + eventos[0].etiqueta : '';
    if (nueva !== huella && huella !== '') nuevoEvento();
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
    cargar: () => cargar(),
  };
}
