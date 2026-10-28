/* Autenticación: contrato /auth/estado · /auth/login · /auth/logout.
 * Si el backend todavía no tiene /auth (404) se asume "sin auth". */
import { api, enJson } from './api.js';
import { $, aviso, toast } from './nucleo.js';

export const auth = {
  requerida: false,
  autenticado: true,
  revisada: false,
};

const LOCAL = /^(localhost|127\.|\[::1\]|::1|.*\.local)$/i;

export async function consultarAuth() {
  try {
    const datos = await api('/auth/estado', { silencioso: true, tolerante: true });
    if (datos && typeof datos.auth_requerida === 'boolean') {
      auth.requerida = datos.auth_requerida;
      auth.autenticado = datos.autenticado !== false;
    } else {
      // 404 (o respuesta rara): backend sin autenticación todavía
      auth.requerida = false;
      auth.autenticado = true;
    }
  } catch (e) {
    auth.requerida = e.estado === 401;
    auth.autenticado = !auth.requerida;
  }
  auth.revisada = true;
  pintarSesion();
  comprobarAvisoHttp();
  return auth;
}

export function mostrarLogin(mensaje = '') {
  const caja = $('login');
  if (!caja) return;
  caja.hidden = false;
  const error = $('loginError');
  if (error) error.textContent = mensaje;
  const campo = $('loginToken');
  if (campo) {
    campo.value = '';
    setTimeout(() => campo.focus(), 30);
  }
  document.body.classList.add('con-login');
}

export function ocultarLogin() {
  const caja = $('login');
  if (caja) caja.hidden = true;
  document.body.classList.remove('con-login');
}

function pintarSesion() {
  const salir = $('btnSalir');
  if (salir) salir.hidden = !auth.requerida;
  const info = $('accesoInfo');
  if (info) {
    info.textContent = !auth.requerida
      ? 'Sin token configurado: solo accesible desde este equipo (127.0.0.1).'
      : auth.autenticado
        ? 'Sesión iniciada con token. La cookie es HttpOnly y SameSite=Lax.'
        : 'Sesión no iniciada: introduce el token.';
  }
  const boton = $('btnLoginSesion');
  if (boton) boton.hidden = !auth.requerida || auth.autenticado;
}

function comprobarAvisoHttp() {
  const { protocol, hostname } = window.location;
  if (protocol !== 'http:') return;
  const local = LOCAL.test(hostname);
  if (local) return;
  if (!auth.requerida) {
    aviso('avisoHttp',
      'Estás accediendo por HTTP sin token desde fuera de localhost: ' +
      'cualquiera en la red puede ver la cámara. Configura VIGIA_TOKEN y ' +
      'pon HTTPS delante (Caddy) o accede por VPN.', 'bad');
  } else {
    aviso('avisoHttp',
      'Estás accediendo por HTTP: el token de acceso viaja sin cifrar. ' +
      'Pon HTTPS delante (Caddy) o accede por VPN.', 'warn');
  }
}

export function iniciarAuth(alAutenticar) {
  const form = $('loginForm');
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const campo = $('loginToken');
      const boton = $('loginBtn');
      const error = $('loginError');
      const token = (campo && campo.value || '').trim();
      if (!token) {
        if (error) error.textContent = 'Escribe el token.';
        return;
      }
      if (boton) { boton.disabled = true; boton.textContent = 'Entrando…'; }
      try {
        await api('/auth/login', enJson('POST', { token }));
        auth.autenticado = true;
        ocultarLogin();
        pintarSesion();
        toast('Sesión iniciada');
        if (alAutenticar) alAutenticar();
      } catch (err) {
        if (error) error.textContent = err.estado === 401
          ? 'Token incorrecto (o demasiados intentos: espera un minuto).'
          : `No se pudo iniciar sesión: ${err.message}`;
      } finally {
        if (boton) { boton.disabled = false; boton.textContent = 'Entrar'; }
      }
    });
  }

  const salir = $('btnSalir');
  if (salir) {
    salir.addEventListener('click', async () => {
      try { await api('/auth/logout', { method: 'POST', silencioso: true }); } catch (e) { /* da igual */ }
      auth.autenticado = false;
      pintarSesion();
      mostrarLogin('Sesión cerrada.');
    });
  }

  const entrar = $('btnLoginSesion');
  if (entrar) entrar.addEventListener('click', () => mostrarLogin());

  window.addEventListener('vigia:401', () => {
    if (!auth.autenticado) return;
    auth.autenticado = false;
    pintarSesion();
    mostrarLogin('La sesión ha caducado o falta el token.');
  });
}
