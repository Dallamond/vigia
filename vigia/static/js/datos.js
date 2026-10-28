/* Capa de datos del panel.
 *
 * Hoy: sondeo (polling) con pausa automática cuando la pestaña está oculta.
 * Mañana: si el backend expone SSE, solo hay que cambiar `iniciar()` por un
 * EventSource y seguir emitiendo los mismos eventos ('estado', 'salud').
 */
import { api } from './api.js';

const INTERVALO_ESTADO = 1500;
const INTERVALO_SALUD = 15000;

class FuenteDatos {
  constructor() {
    this.estado = null;
    this.eventos = [];
    this.actividades = {};
    this.salud = null;
    this.ultimoError = null;
    this._oyentes = new Map();
    this._temporizadores = [];
    this._activa = false;
    this._visibilidad = null;
    this._enCurso = false;
  }

  al(evento, fn) {
    if (!this._oyentes.has(evento)) this._oyentes.set(evento, new Set());
    this._oyentes.get(evento).add(fn);
    return () => this._oyentes.get(evento).delete(fn);
  }

  emitir(evento, valor) {
    const oyentes = this._oyentes.get(evento);
    if (!oyentes) return;
    for (const fn of oyentes) {
      try { fn(valor); } catch (e) { console.error('[vigia] oyente', evento, e); }
    }
  }

  get vivo() {
    return this._activa && !document.hidden;
  }

  iniciar() {
    if (this._activa) return;
    this._activa = true;
    this.refrescarEstado();
    this.refrescarSalud();
    this._temporizadores.push(setInterval(() => this.refrescarEstado(), INTERVALO_ESTADO));
    this._temporizadores.push(setInterval(() => this.refrescarSalud(), INTERVALO_SALUD));
    this._visibilidad = () => {
      if (!document.hidden) {
        this.refrescarEstado();
        this.refrescarSalud();
        this.emitir('visibilidad', true);
      }
    };
    document.addEventListener('visibilitychange', this._visibilidad);
  }

  parar() {
    this._activa = false;
    this._temporizadores.forEach(clearInterval);
    this._temporizadores = [];
    if (this._visibilidad) {
      document.removeEventListener('visibilitychange', this._visibilidad);
      this._visibilidad = null;
    }
  }

  async refrescarEstado() {
    if (!this.vivo || this._enCurso) return;
    this._enCurso = true;
    try {
      const [estado, eventos, actividades] = await Promise.all([
        api('/estado'),
        api('/eventos'),
        api('/actividades', { tolerante: true }),
      ]);
      this.estado = estado;
      this.eventos = Array.isArray(eventos) ? eventos : [];
      this.actividades = actividades && typeof actividades === 'object' ? actividades : {};
      this.ultimoError = null;
      this.emitir('estado', {
        estado: this.estado,
        eventos: this.eventos,
        actividades: this.actividades,
      });
    } catch (e) {
      if (e.estado !== 401) this._fallo(e);
    } finally {
      this._enCurso = false;
    }
  }

  async refrescarSalud() {
    if (!this.vivo) return;
    try {
      this.salud = await api('/salud');
      this.emitir('salud', this.salud);
    } catch (e) {
      if (e.estado !== 401) this._fallo(e);
    }
  }

  _fallo(e) {
    const texto = e && e.message ? e.message : String(e);
    if (texto !== this.ultimoError) {
      this.ultimoError = texto;
      this.emitir('error', e);
    }
  }

  /* ---- consultas puntuales ---- */
  historial({ tipo = '', desde = '', hasta = '', limite = 50 } = {}) {
    const q = new URLSearchParams();
    q.set('limite', limite);
    if (tipo) q.set('tipo', tipo);
    if (desde) q.set('desde', desde);
    if (hasta) q.set('hasta', hasta);
    return api('/historial?' + q.toString());
  }

  clips() { return api('/clips'); }

  timeline({ desde, hasta, dia, camara } = {}) {
    const q = new URLSearchParams();
    if (desde && hasta) { q.set('desde', desde); q.set('hasta', hasta); }
    else if (dia) q.set('dia', dia);
    if (camara != null && camara !== '') q.set('camara_id', camara);
    return api('/timeline?' + q.toString());
  }

  caras() { return api('/caras/registro'); }

  config() { return api('/config'); }

  zonas() { return api('/zonas'); }

  camaras(refrescar = false) {
    return api('/camaras' + (refrescar ? '?refrescar=true' : ''));
  }

  salud() { return api('/salud'); }

  /* endpoints opcionales que aún puede no tener el backend */
  armado() { return api('/armado', { tolerante: true }); }
  ponerArmado(valor) { return api('/armado', { ...cuerpoJson({ armado: valor }), tolerante: true }); }
  telegram() { return api('/telegram/estado', { tolerante: true }); }
  probarTelegram() { return api('/telegram/prueba', { ...cuerpoJson({}), tolerante: true }); }
}

function cuerpoJson(cuerpo) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cuerpo),
  };
}

export const datos = new FuenteDatos();
