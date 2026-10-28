// Verificación de la GUI en Chrome real (headless) por el protocolo DevTools.
// Uso: node scripts/verificar_gui.mjs [url] [carpeta-salida]
// Comprueba escritorio y móvil, navega por todas las vistas y falla si hay
// excepciones, errores de consola o recursos 4xx/5xx.
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL_BASE = process.argv[2] || 'http://127.0.0.1:8020/';
const SALIDA = process.argv[3] || join(tmpdir(), 'vigia-gui');
const CHROME = process.env.CHROME ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PUERTO_CDP = 9333;

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
const fallos = [];
const avisos = [];

function lanzarChrome() {
  const perfil = mkdtempSync(join(tmpdir(), 'vigia-chrome-'));
  const proceso = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${PUERTO_CDP}`,
    `--user-data-dir=${perfil}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--hide-scrollbars',
    '--window-size=1440,900',
    'about:blank',
  ], { stdio: 'ignore' });
  return proceso;
}

async function esperarCdp() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/version`);
      if (r.ok) return r.json();
    } catch (e) { /* aún arrancando */ }
    await dormir(250);
  }
  throw new Error('Chrome no abrió el puerto de depuración');
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pendientes = new Map();
    this.oyentes = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pendientes.has(msg.id)) {
        const { resolver, rechazar } = this.pendientes.get(msg.id);
        this.pendientes.delete(msg.id);
        if (msg.error) rechazar(new Error(msg.error.message));
        else resolver(msg.result);
      } else if (msg.method) {
        const fns = this.oyentes.get(msg.method) || [];
        for (const fn of fns) fn(msg.params);
      }
    });
  }

  enviar(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolver, rechazar) => {
      this.pendientes.set(id, { resolver, rechazar });
      setTimeout(() => {
        if (this.pendientes.has(id)) {
          this.pendientes.delete(id);
          rechazar(new Error(`timeout ${method}`));
        }
      }, 15000);
    });
  }

  al(method, fn) {
    if (!this.oyentes.has(method)) this.oyentes.set(method, []);
    this.oyentes.get(method).push(fn);
  }
}

function vigilar(cdp) {
  const registrar = (tipo, texto) => {
    if (/favicon/i.test(texto)) return;
    fallos.push(`[${tipo}] ${texto}`);
  };
  cdp.al('Runtime.exceptionThrown', (p) => {
    const d = p.exceptionDetails || {};
    registrar('excepción', `${d.text || 'error'} ${d.exception?.description || ''}`.trim());
  });
  cdp.al('Runtime.consoleAPICalled', (p) => {
    if (p.type !== 'error' && p.type !== 'assert') return;
    registrar('console', p.args.map((a) => a.value ?? a.description ?? '').join(' '));
  });
  cdp.al('Log.entryAdded', (p) => {
    // El intento de login con token erróneo devuelve 401 a propósito.
    if (p.entry.level === 'error' && /\/auth\/login$/.test(p.entry.url || '')) return;
    if (p.entry.level === 'error') registrar('log', `${p.entry.text} ${p.entry.url || ''}`);
    else if (p.entry.level === 'warning') avisos.push(`[aviso] ${p.entry.text}`);
  });
  cdp.al('Network.responseReceived', (p) => {
    const r = p.response;
    // 401 esperados: /auth/estado sin sesión y un intento de login fallido.
    if (/\/auth\/(estado|login)$/.test(r.url) && r.status === 401) return;
    if (r.status >= 400 && r.url.startsWith(URL_BASE.replace(/\/$/, ''))) {
      fallos.push(`[http ${r.status}] ${r.url}`);
    }
  });
  cdp.al('Network.loadingFailed', (p) => {
    if (p.type === 'Image' && /caras\/ultima/.test(p.requestId)) return;
    avisos.push(`[red] ${p.errorText} (${p.type})`);
  });
}

async function evaluar(cdp, expresion) {
  const r = await cdp.enviar('Runtime.evaluate', {
    expression: expresion,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result.value;
}

async function navegar(cdp, url) {
  await cdp.enviar('Page.navigate', { url });
  await dormir(2500);
}

/* Con VIGIA_TOKEN: comprueba que aparece el login, que un token erróneo da
 * error y que el correcto entra. Devuelve un resumen de la prueba. */
async function probarLogin(cdp, token) {
  const visible = await evaluar(cdp, '!document.getElementById("login").hidden');
  if (!visible) {
    fallos.push('[login] no se pidió el token al entrar sin sesión');
    return { visible: false };
  }
  const enviar = (valor) => `(() => {
    const campo = document.getElementById('loginToken');
    campo.value = ${JSON.stringify(valor)};
    document.getElementById('loginForm').requestSubmit();
    return true;
  })()`;
  await evaluar(cdp, enviar('token-erroneo-a-proposito'));
  await dormir(900);
  const error = await evaluar(cdp, 'document.getElementById("loginError").textContent');
  if (!/incorrecto|intentos/i.test(error)) {
    fallos.push(`[login] token erróneo sin mensaje claro: "${error}"`);
  }
  await evaluar(cdp, enviar(token));
  await dormir(1500);
  const dentro = await evaluar(cdp,
    'document.getElementById("login").hidden && !document.body.classList.contains("con-login")');
  if (!dentro) fallos.push('[login] el token correcto no abrió la sesión');
  return { visible: true, error, dentro };
}

async function captura(cdp, nombre) {
  const r = await cdp.enviar('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(SALIDA, nombre), Buffer.from(r.data, 'base64'));
}

const VISTAS = ['vivo', 'eventos', 'clips', 'personas', 'zonas', 'sistema', 'ajustes'];

async function recorrer(cdp, etiqueta) {
  const resultado = {};
  resultado.tema = await evaluar(cdp, 'getComputedStyle(document.body).backgroundColor');
  resultado.css = await evaluar(cdp,
    'Array.from(document.styleSheets).some(s => (s.href||"").includes("panel.css"))');
  for (const vista of VISTAS) {
    await cdp.enviar('Runtime.evaluate', { expression: `location.hash = '#/${vista}'` });
    await dormir(vista === 'vivo' ? 2500 : 1200);
    const estado = await evaluar(cdp, `(() => {
      const sec = document.querySelector('[data-vista="${vista}"]');
      const visibles = Array.from(document.querySelectorAll('[data-vista]'))
        .filter(s => !s.hidden).map(s => s.dataset.vista);
      const nav = document.querySelector('.nav a[aria-current="page"]');
      return {
        visible: !!sec && !sec.hidden,
        visibles,
        nav: nav && nav.dataset.nav,
        texto: (sec && sec.innerText || '').slice(0, 140),
        ancho: document.documentElement.scrollWidth,
        ventana: window.innerWidth,
      };
    })()`);
    resultado[vista] = estado;
    if (!estado.visible) fallos.push(`[${etiqueta}] la vista ${vista} no se mostró`);
    if (estado.nav !== vista) fallos.push(`[${etiqueta}] nav no marcan ${vista}`);
    if (estado.visibles.length !== 1) fallos.push(`[${etiqueta}] vistas visibles: ${estado.visibles.join(',')}`);
    if (estado.ancho > estado.ventana + 2) {
      fallos.push(`[${etiqueta}] desbordamiento horizontal en ${vista}: ${estado.ancho} > ${estado.ventana}`);
    }
    await captura(cdp, `${etiqueta}-${vista}.png`);
    if (vista === 'vivo') {
      resultado.timeline = await evaluar(cdp,
        'document.querySelectorAll("#plot .tick").length + document.querySelectorAll("#plot .lane").length');
      if (!resultado.timeline) fallos.push(`[${etiqueta}] la línea del tiempo está vacía`);
      resultado.fps = await evaluar(cdp, 'document.getElementById("pFps").textContent');
    }
    if (vista === 'zonas') {
      resultado.canvas = await evaluar(cdp, 'document.getElementById("zCanvas").width');
      resultado.zonasLista = await evaluar(cdp, 'document.getElementById("zList").innerText.slice(0,80)');
    }
    if (vista === 'ajustes') {
      resultado.franjas = await evaluar(cdp, 'document.querySelectorAll("#franjasEditor .fila-franja").length');
      resultado.perm = await evaluar(cdp, 'document.getElementById("a_permanencia").value');
    }
    if (vista === 'sistema') {
      resultado.salud = await evaluar(cdp, 'document.getElementById("kpisSalud").innerText.slice(0,80)');
    }
  }
  return resultado;
}

async function main() {
  mkdirSync(SALIDA, { recursive: true });
  const chrome = lanzarChrome();
  try {
    await esperarCdp();
    const info = await (await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/new?about:blank`, { method: 'PUT' })).json();
    const ws = new WebSocket(info.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res);
      ws.addEventListener('error', rej);
    });
    const cdp = new Cdp(ws);
    vigilar(cdp);
    await cdp.enviar('Page.enable');
    await cdp.enviar('Runtime.enable');
    await cdp.enviar('Log.enable');
    await cdp.enviar('Network.enable');

    // --- escritorio ---
    await cdp.enviar('Emulation.setDeviceMetricsOverride', {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await navegar(cdp, URL_BASE + '#/vivo');
    const token = process.env.VIGIA_TOKEN || '';
    const login = token ? await probarLogin(cdp, token) : null;
    const escritorio = await recorrer(cdp, 'escritorio');

    // --- móvil ---
    await cdp.enviar('Emulation.setDeviceMetricsOverride', {
      width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
    });
    await cdp.enviar('Emulation.setTouchEmulationEnabled', { enabled: true });
    await navegar(cdp, URL_BASE + '#/vivo');
    const movil = await recorrer(cdp, 'movil');

    const resumen = { url: URL_BASE, login, escritorio, movil, fallos, avisos };
    writeFileSync(join(SALIDA, 'resumen.json'), JSON.stringify(resumen, null, 2));
    console.log(JSON.stringify(resumen, null, 2));
    if (fallos.length) process.exitCode = 1;
  } finally {
    chrome.kill();
  }
}

main().catch((e) => {
  console.error('FALLO:', e);
  process.exit(2);
});
