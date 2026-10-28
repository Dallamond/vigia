/* Capa HTTP única. Un 401 dispara el evento `vigia:401` (pantalla de login). */

export class ErrorApi extends Error {
  constructor(mensaje, estado = 0, datos = null) {
    super(mensaje);
    this.name = 'ErrorApi';
    this.estado = estado;
    this.datos = datos;
  }
}

function mensajeDe(datos, porDefecto) {
  if (datos && typeof datos === 'object') {
    return datos.detail || datos.detalle || datos.error || datos.mensaje || porDefecto;
  }
  return porDefecto;
}

/**
 * fetch + JSON con errores legibles. `opciones.silencioso` evita el evento de
 * login (para sondeos opcionales) y `opciones.tolerante` devuelve null si el
 * endpoint aún no existe (404), en lugar de lanzar.
 */
export async function api(url, opciones = {}) {
  const { silencioso, tolerante, ...fetchOpts } = opciones;
  let respuesta;
  try {
    respuesta = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...fetchOpts,
    });
  } catch (e) {
    throw new ErrorApi('sin conexión con el servidor', 0);
  }
  let datos = null;
  const tipo = respuesta.headers.get('content-type') || '';
  if (tipo.includes('application/json')) {
    try { datos = await respuesta.json(); } catch (e) { datos = null; }
  }
  if (respuesta.status === 401) {
    if (!silencioso) window.dispatchEvent(new CustomEvent('vigia:401'));
    throw new ErrorApi(mensajeDe(datos, 'no autenticado'), 401, datos);
  }
  if (respuesta.status === 404 && tolerante) return null;
  if (!respuesta.ok) {
    throw new ErrorApi(mensajeDe(datos, `error ${respuesta.status}`),
                       respuesta.status, datos);
  }
  return datos;
}

/** Cuerpo JSON para POST/PUT. */
export const enJson = (metodo, cuerpo) => ({
  method: metodo,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(cuerpo),
});
