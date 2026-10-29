/* Arranque del panel: tema, sesión, router, datos y atajos. */
import { $, $$, abrirModal, aviso, claseEvento, enlazarModales, esEscribiendo, toast } from './nucleo.js';
import { consultarAuth, iniciarAuth, mostrarLogin } from './auth.js';
import { datos } from './datos.js';
import { alEntrarVista, alSalirVista, iniciarRouter, vistaActual } from './router.js';
import { iniciarTema, alternarTema } from './tema.js';
import { Timeline } from './timeline.js';
import { Camaras } from './camaras.js';
import { EditorZonas } from './zonas.js';
import { iniciarVivo } from './vista-vivo.js';
import { Overlay } from './overlay.js';
import { iniciarEventos } from './vista-eventos.js';
import { iniciarResumen } from './vista-resumen.js';
import { iniciarClips } from './vista-clips.js';
import { iniciarPersonas } from './vista-personas.js';
import { iniciarSistema } from './vista-sistema.js';
import { iniciarAjustes } from './vista-ajustes.js';

const ORDEN_VISTAS = ['resumen', 'vivo', 'eventos', 'clips', 'personas', 'zonas', 'sistema', 'ajustes'];

function cabecera() {
  datos.al('estado', ({ estado }) => {
    if (!estado) return;
    const fps = $('pFps');
    const per = $('pPer');
    const obj = $('pObj');
    const res = $('pRes');
    const dot = $('dotCam');
    if (fps) fps.textContent = (estado.fps || 0).toFixed(1);
    if (per) per.textContent = estado.personas ?? 0;
    if (obj) obj.textContent = estado.objetos ?? 0;
    if (res && estado.ancho) res.textContent = `${estado.ancho}×${estado.alto}`;
    if (dot) dot.className = 'dot ' + (estado.camara_ok ? 'ok' : 'bad');
    const lado = $('sideEstado');
    if (lado) lado.textContent = estado.camara_ok ? 'cámara activa' : 'cámara caída';
  });
  datos.al('salud', (s) => {
    const v = $('sideVersion');
    if (v && s && s.version) v.textContent = 'v' + s.version;
  });
  // avisos flotantes para lo que merece atención (llegan por SSE)
  datos.al('evento', (e) => {
    if (!e || claseEvento(e.tipo) !== 'aviso' || e.tipo === 'permanencia') return;
    toast(`${String(e.tipo).replace(/_/g, ' ')}${e.detalle ? ' · ' + e.detalle : ''}`, false);
  });
  datos.al('error', (e) => {
    aviso('avisoError', `Sin conexión con el servidor: ${e.message}. Se reintenta solo.`, 'bad');
  });
  datos.al('estado', () => {
    const caja = $('avisoError');
    if (caja && !caja.hidden) caja.hidden = true;
  });
}

function atajos(vistas) {
  let esperandoG = false;
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'Escape') {
      if (esperandoG) { esperandoG = false; return; }
      return;                           // los modales los cierra nucleo
    }
    if (esEscribiendo(e)) return;

    if (esperandoG) {
      esperandoG = false;
      const i = Number(e.key);
      if (i >= 1 && i <= ORDEN_VISTAS.length) {
        e.preventDefault();
        location.hash = `#/${ORDEN_VISTAS[i - 1]}`;
      }
      return;
    }
    if (e.key === 'g') { esperandoG = true; return; }
    if (e.key === '?') { e.preventDefault(); $('mAyuda')?.classList.add('open'); return; }
    if (e.key === 't') { e.preventDefault(); alternarTema(); return; }
    if (e.key === 'r') {
      const vista = vistaActual();
      if (vistas[vista] && vistas[vista].cargar) vistas[vista].cargar();
      else if (vista === 'zonas') vistas.zonasEditor.cargar();
      else if (vista === 'vivo') vistas.timeline.cargar();
      return;
    }
    if (e.key === 'f' && vistaActual() === 'vivo') {
      e.preventDefault();
      $('btnFull')?.click();
      return;
    }
    if (e.key === '/') {
      const campo = document.querySelector('[data-vista]:not([hidden]) input, [data-vista]:not([hidden]) select');
      if (campo) { e.preventDefault(); campo.focus(); }
      return;
    }
    const i = Number(e.key);
    if (i >= 1 && i <= ORDEN_VISTAS.length) {
      e.preventDefault();
      location.hash = `#/${ORDEN_VISTAS[i - 1]}`;
    }
  });
}

async function arrancar() {
  iniciarTema();
  enlazarModales();

  $$('[data-cerrar-aviso]').forEach((b) => {
    b.addEventListener('click', () => { $(b.dataset.cerrarAviso).hidden = true; });
  });

  const timeline = new Timeline(datos);
  const camaras = new Camaras(datos);
  const zonasEditor = new EditorZonas(datos);
  const overlay = new Overlay();

  const vs = {
    resumen: iniciarResumen(),
    vivo: iniciarVivo(datos),
    eventos: iniciarEventos(datos),
    clips: iniciarClips(datos),
    personas: iniciarPersonas(datos),
    sistema: iniciarSistema(datos, camaras),
    ajustes: iniciarAjustes(datos),
    timeline,
    zonasEditor,
  };

  alEntrarVista('vivo', () => { timeline.activar(); vs.vivo.entrar(); overlay.activar(); });
  alSalirVista('vivo', () => { timeline.desactivar(); vs.vivo.salir(); overlay.desactivar(); });
  alEntrarVista('resumen', () => vs.resumen.entrar());
  alSalirVista('resumen', () => vs.resumen.salir());
  alEntrarVista('eventos', (params) => vs.eventos.entrar(params));
  alSalirVista('eventos', () => vs.eventos.salir());
  alEntrarVista('clips', () => vs.clips.entrar());
  alSalirVista('clips', () => vs.clips.salir());
  alEntrarVista('personas', () => vs.personas.entrar());
  alSalirVista('personas', () => vs.personas.salir());
  alEntrarVista('zonas', () => zonasEditor.activar());
  alSalirVista('zonas', () => zonasEditor.desactivar());
  alEntrarVista('sistema', () => vs.sistema.entrar());
  alSalirVista('sistema', () => vs.sistema.salir());
  alEntrarVista('ajustes', () => vs.ajustes.entrar());
  alSalirVista('ajustes', () => vs.ajustes.salir());

  cabecera();
  atajos(vs);

  const ayuda = $('btnAyuda');
  if (ayuda) ayuda.addEventListener('click', () => abrirModal('mAyuda'));

  window.addEventListener('vigia:401', () => {
    datos.parar();
    const img = $('video');
    if (img) img.removeAttribute('src');
  });

  let panelIniciado = false;
  function entrarPanel() {
    if (!panelIniciado) {
      panelIniciado = true;
      iniciarRouter();
    }
    datos.iniciar();
    camaras.cargar();
  }

  iniciarAuth(entrarPanel);

  const sesion = await consultarAuth();
  if (sesion.requerida && !sesion.autenticado) {
    mostrarLogin();
  } else {
    entrarPanel();
  }
}

arrancar();
