/* Tema: auto (prefers-color-scheme), claro u oscuro. Se recuerda en localStorage. */
const CLAVE = 'vigia.tema';
const ORDEN = ['auto', 'claro', 'oscuro'];
const ICONO = { auto: '◐', claro: '☀', oscuro: '☾' };
const NOMBRE = { auto: 'tema automático', claro: 'tema claro', oscuro: 'tema oscuro' };

function aplicar(tema) {
  const valor = ORDEN.includes(tema) ? tema : 'auto';
  document.documentElement.dataset.tema = valor;
  const boton = document.getElementById('btnTema');
  if (boton) {
    boton.textContent = ICONO[valor];
    boton.title = `${NOMBRE[valor]} (t)`;
    boton.setAttribute('aria-label', `Cambiar tema; ahora ${NOMBRE[valor]}`);
  }
  return valor;
}

export function temaActual() {
  return document.documentElement.dataset.tema || 'auto';
}

export function iniciarTema() {
  let guardado = 'auto';
  try { guardado = localStorage.getItem(CLAVE) || 'auto'; } catch (e) { /* modo privado */ }
  aplicar(guardado);
  const boton = document.getElementById('btnTema');
  if (boton) {
    boton.addEventListener('click', () => {
      const siguiente = ORDEN[(ORDEN.indexOf(temaActual()) + 1) % ORDEN.length];
      aplicar(siguiente);
      try { localStorage.setItem(CLAVE, siguiente); } catch (e) { /* nada */ }
    });
  }
}

export function alternarTema() {
  const siguiente = ORDEN[(ORDEN.indexOf(temaActual()) + 1) % ORDEN.length];
  aplicar(siguiente);
  try { localStorage.setItem(CLAVE, siguiente); } catch (e) { /* nada */ }
}
