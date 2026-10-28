/* Selector y rejilla de cámaras. Hoy son webcams locales; mañana, cámaras IP
 * con id propio (la GUI ya pinta `id`/`nombre` si llegan). */
import { $, esc, toast } from './nucleo.js';
import { api, enJson } from './api.js';

export class Camaras {
  constructor(fuenteDatos) {
    this.datos = fuenteDatos;
    this.actual = null;
    this.lista = [];
    this._enlazado = false;
  }

  async cargar(refrescar = false) {
    try {
      if (!this.lista.length) this._cargando();
      const d = await this.datos.camaras(refrescar);
      this.actual = d ? d.actual : null;
      this.lista = (d && d.camaras) || [];
      this._pintar();
      return d;
    } catch (e) {
      if (e.estado !== 401) {
        const grid = $('camGrid');
        if (grid) grid.innerHTML = '<div class="empty">no se pudieron listar las cámaras</div>';
      }
      return null;
    }
  }

  _cargando() {
    const texto = '<option value="">Cargando cámaras…</option>';
    const sel = $('selCam');
    if (sel) sel.innerHTML = texto;
    const ajustes = $('a_camara');
    if (ajustes) ajustes.innerHTML = texto;
    const grid = $('camGrid');
    if (grid) grid.innerHTML = '<div class="empty">buscando cámaras…</div>';
  }

  _etiqueta(c) {
    const nombre = c.nombre || (c.id != null ? c.nombre || `Cámara ${c.id}` : `Cámara ${c.indice}`);
    return `${esc(nombre)}${c.disponible === false ? ' (no responde)' : ''}`;
  }

  _opciones(el, previo, emoji) {
    if (!el) return;
    const valores = this.lista.map((c) => String(c.indice != null ? c.indice : c.id));
    const elegido = this.actual != null
      ? String(this.actual)
      : (valores.includes(previo) ? previo : (valores[0] || ''));
    el.innerHTML = this.lista.map((c) => {
      const valor = String(c.indice != null ? c.indice : c.id);
      return `<option value="${esc(valor)}"${valor === elegido ? ' selected' : ''}>` +
        `${emoji ? '📷 ' : ''}${this._etiqueta(c)}</option>`;
    }).join('');
  }

  _pintar() {
    const sel = $('selCam');
    const ajustes = $('a_camara');
    const previoAjustes = ajustes ? String(ajustes.value || '') : '';
    const previoSel = sel ? String(sel.value || '') : '';
    this._opciones(sel, previoSel, true);
    this._opciones(ajustes, previoAjustes, false);
    const grid = $('camGrid');
    if (grid) {
      grid.innerHTML = this.lista.length ? this.lista.map((c) => {
        const valor = c.indice != null ? c.indice : c.id;
        const activa = this.actual != null && String(valor) === String(this.actual);
        return `<button type="button" class="cam-btn${activa ? ' activa' : ''}" data-cam="${esc(valor)}">
          <b>${this._etiqueta(c)}</b>
          <small>${activa ? 'principal ahora' : c.disponible === false ? 'sin señal' : 'disponible'}</small>
        </button>`;
      }).join('') : '<div class="empty">ninguna cámara detectada</div>';
    }
    const nombre = $('vivoCamNombre');
    if (nombre) {
      const c = this.lista.find((x) => String(x.indice ?? x.id) === String(this.actual));
      nombre.textContent = c ? (c.nombre || `cámara ${this.actual}`) : 'cámara principal';
    }
    this._enlazar();
    window.dispatchEvent(new CustomEvent('vigia:camaras'));
  }

  _enlazar() {
    if (this._enlazado) return;
    this._enlazado = true;
    const sel = $('selCam');
    if (sel) {
      sel.addEventListener('change', () => this.cambiar(sel.value));
      sel.addEventListener('focus', () => this.cargar(true));
    }
    const grid = $('camGrid');
    if (grid) {
      grid.addEventListener('click', (e) => {
        const boton = e.target.closest('[data-cam]');
        if (boton) this.cambiar(boton.dataset.cam);
      });
    }
    const refrescar = $('camRefrescar');
    if (refrescar) refrescar.addEventListener('click', () => this.cargar(true));
  }

  async cambiar(valor) {
    const indice = Number(valor);
    if (!Number.isFinite(indice)) return;
    try {
      await api('/config', enJson('POST', { camara: indice }));
      toast('Cambiando de cámara…');
      setTimeout(() => this.cargar(true), 4000);   // confirma cuál quedó (revierte si falla)
    } catch (e) {
      toast('Cámara: ' + e.message, false);
      this.cargar();
    }
  }
}
