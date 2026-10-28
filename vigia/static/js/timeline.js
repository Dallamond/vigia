/* Línea del tiempo en directo. Se repinta cada 2 s y recarga cada 6 s,
 * solo mientras la vista "En vivo" está abierta y la pestaña visible. */
import { $, esc, fmtSQL, horaDe, media, parseFecha, pad2 } from './nucleo.js';
import { verEvento } from './detalle.js';

const CARRILES = ['presencia', 'permanencia', 'actividad', 'objetos', 'caras'];
const ALTO_CARRIL = 30;
const ALTO_EJE = 22;

const COLOR = {
  persona_entra: '#4ade80',
  persona_salio: '#f87171',
  permanencia: '#fbbf24',
  actividad: '#a78bfa',
  persona_reconocida: '#2dd4bf',
  cara_desconocida: '#fb923c',
};

function carrilDe(tipo) {
  if (tipo === 'persona_entra' || tipo === 'persona_salio') return 0;
  if (tipo === 'permanencia') return 1;
  if (tipo === 'actividad') return 2;
  if ((tipo || '').startsWith('objeto_')) return 3;
  if (tipo === 'persona_reconocida' || tipo === 'cara_desconocida') return 4;
  return 3;
}

const colorTipo = (t) => COLOR[t] || '#38bdf8';

export class Timeline {
  constructor(fuenteDatos) {
    this.datos = fuenteDatos;
    this.modo = '6';
    this.dia = null;
    this.finFijo = null;      // ventana desplazada a mano = histórico
    this.camaraId = null;     // futuro multicámara (se oculta si no hay datos)
    this.eventos = [];
    this.barras = [];
    this.visibles = [];
    this.ids = [];
    this.ventana = null;
    this.tarjetas = [];
    this.cargando = false;
    this.activa = false;
    this._temporizadores = [];
    this._huella = null;
    this._enlazar();
    this.datos.al('estado', ({ estado, eventos }) => {
      this.ids = (estado && estado.ids) || [];
      const huella = eventos.length
        ? `${eventos[0].hora}${eventos[0].tipo}${eventos[0].etiqueta}` : '';
      if (huella !== this._huella && this._huella !== null && this.activa
          && this.ventana && this.ventana.vivo) {
        this.cargar();
      }
      this._huella = huella;
    });
  }

  activar() {
    if (this.activa) return;
    this.activa = true;
    this.cargar();
    this._temporizadores.push(setInterval(() => {
      if (this.ventana && this.ventana.vivo && !document.hidden) this.pintar();
    }, 2000));
    this._temporizadores.push(setInterval(() => {
      if (!document.hidden && this.ventana && this.ventana.vivo) this.cargar();
    }, 6000));
  }

  desactivar() {
    this.activa = false;
    this._temporizadores.forEach(clearInterval);
    this._temporizadores = [];
  }

  ponerCamara(id) {
    this.camaraId = id;
    if (this.activa) this.cargar();
  }

  ventanaActual() {
    const ahora = new Date();
    if (this.modo === 'dia' && this.dia) {
      const [y, m, d] = this.dia.split('-').map(Number);
      const ini = new Date(y, m - 1, d);
      const fin = new Date(y, m - 1, d + 1);
      return { ini, fin, rango: fin - ini, vivo: ahora >= ini && ahora < fin, ahora };
    }
    const rango = (+this.modo || 6) * 3600e3;
    const fin = this.finFijo || new Date(ahora.getTime() + rango * 0.03);
    return {
      ini: new Date(fin.getTime() - rango),
      fin,
      rango,
      vivo: !this.finFijo,
      ahora,
    };
  }

  async cargar() {
    if (this.cargando) return;
    this.cargando = true;
    try {
      const v = this.ventanaActual();
      // 3 h de margen por detrás: para emparejar entradas anteriores a la ventana
      const desde = new Date(v.ini.getTime() - 3 * 3600e3);
      const datos = await this.datos.timeline({
        desde: fmtSQL(desde),
        hasta: fmtSQL(v.fin),
        camara: this.camaraId,
      });
      this.eventos = (datos && datos.eventos) || [];
      this._mostrarCamaras();
    } catch (e) {
      if (e.estado !== 401) {
        const plot = $('plot');
        if (plot) plot.innerHTML = '<div class="empty">no se pudo cargar la línea del tiempo</div>';
      }
      this.cargando = false;
      return;
    }
    this.cargando = false;
    this.pintar();
  }

  /** El filtro de cámara solo aparece cuando los eventos traen `camara_id`. */
  _mostrarCamaras() {
    const conCamara = this.eventos.some((e) => e.camara_id != null);
    const selector = $('tlCamara');
    if (!selector) return;
    selector.hidden = !conCamara;
    if (!conCamara) return;
    const ids = [...new Set(this.eventos.map((e) => e.camara_id).filter((x) => x != null))].sort();
    selector.innerHTML = '<option value="">todas las cámaras</option>' +
      ids.map((id) => `<option value="${esc(id)}">Cámara ${esc(id)}</option>`).join('');
    selector.value = this.camaraId == null ? '' : String(this.camaraId);
  }

  _eventosFiltrados() {
    if (this.camaraId == null) return this.eventos;
    return this.eventos.filter((e) => e.camara_id == null || String(e.camara_id) === String(this.camaraId));
  }

  barrasDe(v, eventos) {
    const abiertas = new Map();
    const barras = [];
    const cerrar = (id, fin, evSalio) => {
      const a = abiertas.get(id);
      if (!a) return;
      barras.push({ id, ini: a.t, fin, evEntra: a.ev, evSalio, vivo: false });
      abiertas.delete(id);
    };
    for (const e of eventos) {
      const t = parseFecha(e.marca_sistema);
      if (!t) continue;
      if (e.tipo === 'persona_entra' && e.id_tracking != null) {
        cerrar(e.id_tracking, t, null);      // id reutilizado tras reinicio
        abiertas.set(e.id_tracking, { t, ev: e });
      } else if (e.tipo === 'persona_salio' && e.id_tracking != null) {
        if (abiertas.has(e.id_tracking)) cerrar(e.id_tracking, t, e);
        else barras.push({
          id: e.id_tracking,
          ini: new Date(v.ini.getTime() - 1),
          fin: t,
          evEntra: null,
          evSalio: e,
          vivo: false,
        });
      }
    }
    // sin salida registrada: sigue dentro solo si la cámara lo ve ahora mismo
    for (const [id, a] of abiertas) {
      const dentro = v.vivo && this.ids.includes(id);
      barras.push({
        id,
        ini: a.t,
        fin: dentro ? v.ahora : new Date(a.t.getTime() + 20e3),
        evEntra: a.ev,
        evSalio: null,
        vivo: dentro,
      });
    }
    return barras;
  }

  _ticks(v) {
    const horas = v.rango / 3600e3;
    const paso = horas <= 1.5 ? 10 : horas <= 7 ? 60 : horas <= 13 ? 120 : 180;
    const medianoche = new Date(v.ini);
    medianoche.setHours(0, 0, 0, 0);
    const pasoMs = paso * 60e3;
    const ticks = [];
    let t = medianoche.getTime() + Math.ceil((v.ini - medianoche) / pasoMs) * pasoMs;
    for (; t < v.fin.getTime(); t += pasoMs) {
      const d = new Date(t);
      const etiqueta = (d.getHours() === 0 && d.getMinutes() === 0)
        ? `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}`
        : `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
      ticks.push({ t, etiqueta });
    }
    return ticks;
  }

  pintar() {
    const plot = $('plot');
    if (!plot) return;
    const v = this.ventanaActual();
    this.ventana = v;
    const eventos = this._eventosFiltrados();
    const pct = (d) => ((d.getTime ? d.getTime() : d) - v.ini.getTime()) / v.rango * 100;
    const dentro = (p) => p >= -0.5 && p <= 100.5;

    let html = '';
    CARRILES.forEach((_, i) => {
      html += `<div class="lane${i % 2 ? ' alt' : ''}" style="top:${ALTO_EJE + i * ALTO_CARRIL}px"></div>`;
    });
    for (const k of this._ticks(v)) {
      html += `<div class="tick" style="left:${pct(k.t).toFixed(3)}%"><span>${k.etiqueta}</span></div>`;
    }

    this.barras = this.barrasDe(v, eventos);
    this.barras.forEach((b, i) => {
      const a = Math.max(0, pct(b.ini));
      const z = Math.min(100, pct(b.fin));
      if (z < 0 || a > 100) return;
      html += `<button type="button" class="bar${b.vivo ? ' vivo' : ''}" data-barra="${i}"
        aria-label="Persona ${esc(b.id)}"
        style="left:${a.toFixed(3)}%;width:${Math.max(0.15, z - a).toFixed(3)}%;top:${ALTO_EJE + 8}px"></button>`;
    });

    this.visibles = [];
    for (const e of eventos) {
      if (carrilDe(e.tipo) === 0) continue;        // la presencia va en barras
      const t = parseFecha(e.marca_sistema);
      if (!t) continue;
      const p = pct(t);
      if (!dentro(p)) continue;
      this.visibles.push(e);
      html += `<button type="button" class="m" data-marca="${this.visibles.length - 1}"
        aria-label="${esc(e.tipo)}"
        style="left:${p.toFixed(3)}%;top:${ALTO_EJE + carrilDe(e.tipo) * ALTO_CARRIL + 7}px;background:${colorTipo(e.tipo)}"></button>`;
    }
    if (v.vivo && v.ahora >= v.ini && v.ahora <= v.fin) {
      html += `<div class="nowline" style="left:${pct(v.ahora).toFixed(3)}%"></div>`;
    }
    plot.innerHTML = html;

    this._cabecera(v, eventos);

    // tira de tarjetas: los últimos eventos de la ventana, el más reciente primero
    const enVentana = eventos.filter((e) => {
      const t = parseFecha(e.marca_sistema);
      return t && t >= v.ini && t <= v.fin;
    });
    this.tarjetas = enVentana.slice().reverse().slice(0, 40);
    const strip = $('strip');
    if (strip) {
      strip.innerHTML = this.tarjetas.length ? this.tarjetas.map((e, i) => `
        <button type="button" class="sc" data-tarjeta="${i}">
          ${e.miniatura
            ? `<img class="img" src="${media(e.miniatura)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;noimg&quot;>▣</div>'">`
            : '<div class="noimg" aria-hidden="true">▣</div>'}
          <span class="b"><b><span class="tag ${esc(e.tipo)}">${esc(e.tipo)}</span></b>
            <small>${esc(horaDe(e))} · ${esc(e.detalle || '—')}</small></span>
        </button>`).join('') : '<div class="empty">sin eventos en esta ventana</div>';
    }
  }

  _cabecera(v, eventos) {
    const live = $('tlLive');
    if (live) live.classList.toggle('off', !v.vivo);
    const txt = $('tlLiveTxt');
    if (txt) txt.textContent = v.vivo ? 'EN DIRECTO' : 'HISTÓRICO';
    const total = $('tlTotal');
    if (total) {
      const n = eventos.filter((e) => {
        const t = parseFecha(e.marca_sistema);
        return t && t >= v.ini && t <= v.fin;
      }).length;
      total.textContent = `${n} evento${n === 1 ? '' : 's'}`;
    }
  }

  desplazar(signo) {
    const v = this.ventanaActual();
    if (this.modo === 'dia') {
      const [y, m, d] = this.dia.split('-').map(Number);
      const nuevo = new Date(y, m - 1, d + signo);
      this.dia = `${nuevo.getFullYear()}-${pad2(nuevo.getMonth() + 1)}-${pad2(nuevo.getDate())}`;
      const input = $('tdia');
      if (input) input.value = this.dia;
    } else {
      const fin = new Date(v.fin.getTime() + signo * v.rango / 2);
      this.finFijo = (signo > 0 && fin >= new Date()) ? null : fin;
    }
    this.cargar();
  }

  _marcarRango() {
    document.querySelectorAll('#tlRango button').forEach((b) => {
      const on = this.modo !== 'dia' && b.dataset.h === this.modo;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  _enlazar() {
    const tip = $('tip');
    const plot = $('plot');

    document.querySelectorAll('#tlRango button').forEach((b) => {
      b.addEventListener('click', () => {
        this.modo = b.dataset.h;
        this.finFijo = null;
        const dia = $('tdia');
        if (dia) dia.value = '';
        this._marcarRango();
        this.cargar();
      });
    });
    const atras = $('tlAtras');
    if (atras) atras.addEventListener('click', () => this.desplazar(-1));
    const adelante = $('tlAdelante');
    if (adelante) adelante.addEventListener('click', () => this.desplazar(1));
    const ahoraBtn = $('tlAhoraBtn');
    if (ahoraBtn) ahoraBtn.addEventListener('click', () => {
      if (this.modo === 'dia') this.modo = '6';
      this.finFijo = null;
      const dia = $('tdia');
      if (dia) dia.value = '';
      this._marcarRango();
      this.cargar();
    });
    const dia = $('tdia');
    if (dia) dia.addEventListener('change', () => {
      if (!dia.value) { ahoraBtn.click(); return; }
      this.modo = 'dia';
      this.dia = dia.value;
      this.finFijo = null;
      this._marcarRango();
      this.cargar();
    });
    const camara = $('tlCamara');
    if (camara) camara.addEventListener('change', () => {
      this.ponerCamara(camara.value === '' ? null : camara.value);
    });

    const ponerTip = (ev, html) => {
      if (!tip) return;
      tip.innerHTML = html;
      tip.style.display = 'block';
      const w = tip.offsetWidth;
      const h = tip.offsetHeight;
      tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - w - 8) + 'px';
      tip.style.top = Math.max(8, ev.clientY - h - 12) + 'px';
    };
    const htmlTip = (e, extra) => (
      (e && e.miniatura ? `<img src="${media(e.miniatura)}" alt="">` : '') +
      `<b>${esc(e ? e.tipo : '')}</b> <small>${esc(e ? horaDe(e) : '')}</small><br>` +
      `<small>${esc((e && e.detalle) || '')}${extra || ''}</small>`
    );
    if (plot) {
      plot.addEventListener('mouseover', (ev) => {
        const m = ev.target.closest('[data-marca], [data-barra]');
        if (!m) return;
        if (m.dataset.marca != null) {
          ponerTip(ev, htmlTip(this.visibles[+m.dataset.marca]));
        } else {
          const b = this.barras[+m.dataset.barra];
          if (!b) return;
          const dur = Math.max(1, Math.round((b.fin - b.ini) / 1000));
          ponerTip(ev, htmlTip(b.evEntra || b.evSalio,
            `<br>persona #${esc(b.id)} · ${b.vivo ? 'dentro ahora' : dur + ' s'}`));
        }
      });
      plot.addEventListener('mouseout', () => { if (tip) tip.style.display = 'none'; });
      plot.addEventListener('click', (ev) => {
        const m = ev.target.closest('[data-marca], [data-barra]');
        if (!m) return;
        if (tip) tip.style.display = 'none';
        if (m.dataset.marca != null) {
          verEvento(this.visibles[+m.dataset.marca]);
        } else {
          const b = this.barras[+m.dataset.barra];
          if (b) verEvento(b.evEntra || b.evSalio);
        }
      });
    }
    const strip = $('strip');
    if (strip) {
      strip.addEventListener('click', (ev) => {
        const c = ev.target.closest('[data-tarjeta]');
        if (c && this.tarjetas[+c.dataset.tarjeta]) verEvento(this.tarjetas[+c.dataset.tarjeta]);
      });
    }
    this._marcarRango();
  }
}
