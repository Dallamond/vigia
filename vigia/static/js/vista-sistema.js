/* Vista «Sistema»: salud, cámaras, armado y Telegram.
 * Los endpoint de armado/Telegram pueden no existir todavía (404): se muestra
 * "pendiente" y no se escribe nada raro en la consola. */
import { $, esc, toast } from './nucleo.js';
import { pintarSalud } from './vista-vivo.js';

export function iniciarSistema(fuenteDatos, camaras) {
  let activo = false;
  let intervalo = null;

  fuenteDatos.al('salud', (salud) => { if (salud) pintarSalud(salud); });

  function pintarArmado(d) {
    const pendiente = $('armadoPendiente');
    const txt = $('armadoTxt');
    const dot = $('dotArmado');
    if (!d) {
      if (pendiente) pendiente.hidden = false;
      if (txt) txt.textContent = 'sin datos';
      if (dot) dot.className = 'dot';
      return;
    }
    if (pendiente) pendiente.hidden = true;
    const armado = d.armado ?? d.activo ?? (d.estado === 'armado');
    if (txt) txt.textContent = armado ? 'Armado: los avisos se envían' : 'Desarmado: solo se registra';
    if (dot) dot.className = 'dot ' + (armado ? 'ok' : '');
  }

  async function cargarArmado() {
    try {
      pintarArmado(await fuenteDatos.armado());
    } catch (e) { pintarArmado(null); }
  }

  function pintarTelegram(d) {
    const pendiente = $('telegramPendiente');
    const txt = $('telegramTxt');
    const dot = $('dotTelegram');
    if (!d) {
      if (pendiente) pendiente.hidden = false;
      if (txt) txt.textContent = 'sin datos';
      if (dot) dot.className = 'dot';
      return;
    }
    if (pendiente) pendiente.hidden = true;
    const configurado = d.configurado ?? d.activo ?? d.ok;
    if (txt) {
      const error = d.ultimo_error || d.error;
      txt.textContent = configurado
        ? (error ? `Configurado · último error: ${error}` : 'Configurado y operativo')
        : 'Sin configurar (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID en .env)';
    }
    if (dot) dot.className = 'dot ' + (configurado ? (d.ultimo_error || d.error ? 'bad' : 'ok') : '');
  }

  async function cargarTelegram() {
    try {
      pintarTelegram(await fuenteDatos.telegram());
    } catch (e) { pintarTelegram(null); }
  }

  async function refrescarTodo() {
    if (!activo) return;
    try {
      const salud = await fuenteDatos.salud();
      fuenteDatos.salud = salud;
      pintarSalud(salud);
    } catch (e) { /* silencioso */ }
    await Promise.all([cargarArmado(), cargarTelegram()]);
  }

  const armar = $('btnArmar');
  if (armar) armar.addEventListener('click', async () => {
    try {
      const r = await fuenteDatos.ponerArmado(true);
      if (!r) { toast('Armado: pendiente del backend', false); return; }
      toast('Sistema armado');
      cargarArmado();
    } catch (e) { toast('Armado: ' + e.message, false); }
  });
  const desarmar = $('btnDesarmar');
  if (desarmar) desarmar.addEventListener('click', async () => {
    try {
      const r = await fuenteDatos.ponerArmado(false);
      if (!r) { toast('Armado: pendiente del backend', false); return; }
      toast('Sistema desarmado');
      cargarArmado();
    } catch (e) { toast('Armado: ' + e.message, false); }
  });

  const prueba = $('btnTelegramPrueba');
  if (prueba) prueba.addEventListener('click', async () => {
    const msg = $('telegramMsg');
    prueba.disabled = true;
    if (msg) msg.textContent = 'enviando…';
    try {
      const r = await fuenteDatos.probarTelegram();
      if (!r) {
        if (msg) msg.textContent = 'pendiente del backend';
      } else {
        if (msg) msg.textContent = r.ok === false ? `error: ${r.error || 'no enviado'}` : 'enviado';
        toast(r.ok === false ? 'Telegram: ' + (r.error || 'no enviado') : 'Mensaje de prueba enviado', r.ok !== false);
        cargarTelegram();
      }
    } catch (e) {
      if (msg) msg.textContent = 'error: ' + e.message;
    } finally {
      prueba.disabled = false;
    }
  });

  return {
    entrar() {
      activo = true;
      refrescarTodo();
      camaras.cargar();
      if (!intervalo) intervalo = setInterval(refrescarTodo, 12000);
    },
    salir() {
      activo = false;
      if (intervalo) { clearInterval(intervalo); intervalo = null; }
    },
    refrescar: refrescarTodo,
  };
}
