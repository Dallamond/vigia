/* Router por hash: #/vivo, #/eventos, #/clips, #/personas, #/zonas,
 * #/sistema, #/ajustes. Cada vista avisa al entrar y al salir para poder
 * parar sondeos y temporizadores que no hacen falta. */

export const VISTAS = {
  resumen: { titulo: 'Resumen' },
  vivo: { titulo: 'En vivo' },
  eventos: { titulo: 'Eventos' },
  clips: { titulo: 'Clips' },
  personas: { titulo: 'Personas' },
  zonas: { titulo: 'Zonas y reglas' },
  sistema: { titulo: 'Sistema' },
  ajustes: { titulo: 'Ajustes' },
};

const entrada = { vista: null, params: new URLSearchParams() };
const alEntrar = new Map();
const alSalir = new Map();

export function alEntrarVista(vista, fn) {
  if (!alEntrar.has(vista)) alEntrar.set(vista, new Set());
  alEntrar.get(vista).add(fn);
}

export function alSalirVista(vista, fn) {
  if (!alSalir.has(vista)) alSalir.set(vista, new Set());
  alSalir.get(vista).add(fn);
}

export function paramsActuales() {
  return new URLSearchParams(entrada.params);
}

export function vistaActual() {
  return entrada.vista;
}

function leerHash() {
  const hash = location.hash || '#/vivo';
  const limpio = hash.replace(/^#\/?/, '');
  const [nombre, consulta = ''] = limpio.split('?');
  const vista = Object.hasOwn(VISTAS, nombre) ? nombre : 'vivo';
  return { vista, params: new URLSearchParams(consulta) };
}

function pintar(vista) {
  document.querySelectorAll('[data-vista]').forEach((seccion) => {
    seccion.hidden = seccion.dataset.vista !== vista;
  });
  document.querySelectorAll('.nav a[data-nav]').forEach((a) => {
    if (a.dataset.nav === vista) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  document.title = `Vigia — ${VISTAS[vista].titulo}`;
  const titulo = document.getElementById('tituloVista');
  if (titulo) titulo.textContent = VISTAS[vista].titulo;
}

function aplicar() {
  const { vista, params } = leerHash();
  const anterior = entrada.vista;
  if (anterior === vista) {
    entrada.params = params;
    const fns = alEntrar.get(vista);
    if (fns) fns.forEach((fn) => fn(params));
    return;
  }
  if (anterior && alSalir.has(anterior)) {
    alSalir.get(anterior).forEach((fn) => fn());
  }
  entrada.vista = vista;
  entrada.params = params;
  pintar(vista);
  const fns = alEntrar.get(vista);
  if (fns) fns.forEach((fn) => fn(params));
  const principal = document.querySelector(`[data-vista="${vista}"] h2`);
  if (principal) principal.setAttribute('tabindex', '-1');
  window.scrollTo({ top: 0, behavior: 'instant' });
}

export function navegar(vista, params) {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  location.hash = `#/${vista}${q}`;
}

export function iniciarRouter() {
  window.addEventListener('hashchange', aplicar);
  aplicar();
}
