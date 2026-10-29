/* Overlay del vídeo en vivo: esqueleto, cajas y etiquetas dibujados en un
 * canvas sobre el MJPEG con los datos de `GET /vivo` (coordenadas en píxeles
 * del frame). Si el backend no lo expone (404) se desactiva solo y reintenta
 * de vez en cuando; nunca rompe el resto del panel. */
import { $ } from './nucleo.js';

const INTERVALO = 110;       // ms entre lecturas de /vivo
const REINTENTO_404 = 8000;  // ms antes de volver a probar si no existe
const CONF_KPT = 0.3;

// pares de keypoints COCO-17 agrupados por zona del cuerpo
const HUESOS = {
  tronco: [[5, 6], [5, 11], [6, 12], [11, 12]],
  brazos: [[5, 7], [7, 9], [6, 8], [8, 10]],
  piernas: [[11, 13], [13, 15], [12, 14], [14, 16]],
  cabeza: [[0, 1], [0, 2], [1, 3], [2, 4], [0, 5], [0, 6]],
};

// El canvas no entiende light-dark()/var(): se deja que el navegador resuelva
// el color en un elemento sonda y se lee ya como rgb().
let _sonda = null;
function css(nombre, defecto) {
  try {
    if (!_sonda) {
      _sonda = document.createElement('span');
      _sonda.style.cssText = 'position:absolute;width:0;height:0;visibility:hidden;pointer-events:none';
      document.body.appendChild(_sonda);
    }
    _sonda.style.color = '';
    _sonda.style.color = `var(${nombre}, ${defecto})`;
    return getComputedStyle(_sonda).color || defecto;
  } catch (e) {
    return defecto;
  }
}

export class Overlay {
  constructor() {
    this.canvas = $('overlay');
    this.activo = false;
    this.visible = true;
    this.ultimo = null;
    this._limpio = false;
    this._timer = null;
    this._ctrl = null;
    try { this.visible = localStorage.getItem('vigia.overlay') !== '0'; } catch (e) { /* nada */ }
    const toggle = $('ovToggle');
    if (toggle) {
      toggle.checked = this.visible;
      toggle.addEventListener('change', () => {
        this.visible = toggle.checked;
        this._modo(this.visible && this.ultimo != null);
        try { localStorage.setItem('vigia.overlay', this.visible ? '1' : '0'); } catch (e) { /* nada */ }
        this.pintar();
      });
    }
    if (this.canvas && 'ResizeObserver' in window) {
      new ResizeObserver(() => this.pintar()).observe(this.canvas);
    }
  }

  /** El vídeo va sin anotaciones mientras el overlay dibuja encima. */
  _modo(limpio) {
    if (limpio === this._limpio) return;
    this._limpio = limpio;
    window.dispatchEvent(new CustomEvent('vigia:overlay', { detail: { limpio } }));
  }

  activar() {
    if (this.activo || !this.canvas) return;
    this.activo = true;
    this._ciclo();
  }

  desactivar() {
    this.activo = false;
    clearTimeout(this._timer);
    if (this._ctrl) this._ctrl.abort();
    this.ultimo = null;
    this._limpio = false;
    this.pintar();
  }

  async _ciclo() {
    if (!this.activo) return;
    let espera = INTERVALO;
    if (document.hidden || !this.visible) {
      espera = 600;
    } else {
      try {
        this._ctrl = new AbortController();
        const r = await fetch('/vivo', { credentials: 'same-origin', cache: 'no-store', signal: this._ctrl.signal });
        if (r.status === 404) {
          this.ultimo = null;
          this._modo(false);
          espera = REINTENTO_404;
        } else if (r.ok) {
          this.ultimo = await r.json();
          window.dispatchEvent(new CustomEvent('vigia:vivo', { detail: this.ultimo }));
          this._modo(this.visible);
        } else {
          espera = 2000;
        }
      } catch (e) {
        if (e.name === 'AbortError') return;
        espera = 2000;
      }
      this.pintar();
    }
    this._timer = setTimeout(() => this._ciclo(), espera);
  }

  pintar() {
    const c = this.canvas;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(c.clientWidth * dpr));
    const h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    const d = this.ultimo;
    if (!d || !this.visible || !d.ancho || !d.alto) return;

    // object-fit: contain -> el vídeo ocupa un rectángulo centrado
    const s = Math.min(w / d.ancho, h / d.alto);
    const ox = (w - d.ancho * s) / 2;
    const oy = (h - d.alto * s) / 2;
    const X = (x) => ox + x * s;
    const Y = (y) => oy + y * s;
    const u = dpr;  // unidad en píxeles de pantalla

    const acento = css('--accent', '#6b97ff');
    const ok = css('--ok', '#34d399');
    const warn = css('--warn', '#f5b84a');
    const cian = css('--cyan', '#38bdf8');

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (const o of d.objetos || []) this._objeto(ctx, o, X, Y, u, cian);
    for (const cara of d.caras || []) this._cara(ctx, cara, X, Y, u, ok);
    for (const p of d.personas || []) {
      const color = p.nombre ? ok : acento;
      this._esqueleto(ctx, p, X, Y, u, { color, acento, ok, warn });
      this._caja(ctx, p, X, Y, u, color);
    }
  }

  _esqueleto(ctx, p, X, Y, u, paleta) {
    const k = p.kpts;
    if (!Array.isArray(k) || k.length < 17) return;
    const color = { tronco: '#ffffff', brazos: paleta.acento, piernas: paleta.ok, cabeza: paleta.warn };
    ctx.lineWidth = 3 * u;
    for (const [zona, pares] of Object.entries(HUESOS)) {
      ctx.strokeStyle = color[zona];
      ctx.globalAlpha = 0.9;
      for (const [a, b] of pares) {
        const pa = k[a]; const pb = k[b];
        if (!pa || !pb || pa[2] < CONF_KPT || pb[2] < CONF_KPT) continue;
        ctx.beginPath();
        ctx.moveTo(X(pa[0]), Y(pa[1]));
        ctx.lineTo(X(pb[0]), Y(pb[1]));
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    for (const pt of k) {
      if (!pt || pt[2] < CONF_KPT) continue;
      ctx.beginPath();
      ctx.arc(X(pt[0]), Y(pt[1]), 3.6 * u, 0, Math.PI * 2);
      ctx.fillStyle = '#0b0b0d';
      ctx.fill();
      ctx.lineWidth = 2 * u;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
    }
  }

  _caja(ctx, p, X, Y, u, color) {
    const [x1, y1, x2, y2] = (p.caja || []).map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite)) return;
    const a = X(x1); const b = Y(y1); const w = X(x2) - a; const h = Y(y2) - b;
    const l = Math.min(w, h, 46 * u) * 0.5;   // longitud de las esquinas
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5 * u;
    ctx.beginPath();
    for (const [px, py, dx, dy] of [[a, b, 1, 1], [a + w, b, -1, 1], [a, b + h, 1, -1], [a + w, b + h, -1, -1]]) {
      ctx.moveTo(px + dx * l, py);
      ctx.lineTo(px, py);
      ctx.lineTo(px, py + dy * l);
    }
    ctx.stroke();

    const partes = [`#${p.id}`];
    if (p.nombre) partes.push(p.nombre);
    if (p.actividad && p.actividad !== 'desconocida') partes.push(p.actividad);
    this._etiqueta(ctx, partes.join('  ·  '), a, b, u, color);
  }

  _objeto(ctx, o, X, Y, u, color) {
    const [x1, y1, x2, y2] = (o.caja || []).map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite)) return;
    ctx.save();
    ctx.setLineDash([5 * u, 4 * u]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5 * u;
    ctx.strokeRect(X(x1), Y(y1), X(x2) - X(x1), Y(y2) - Y(y1));
    ctx.restore();
    this._etiqueta(ctx, o.clase, X(x1), Y(y1), u, color, true);
  }

  _cara(ctx, c, X, Y, u, color) {
    const [x1, y1, x2, y2] = (c.caja || []).map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite)) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5 * u;
    const r = 5 * u;
    const a = X(x1); const b = Y(y1); const w = X(x2) - a; const h = Y(y2) - b;
    ctx.beginPath();
    ctx.roundRect(a, b, w, h, r);
    ctx.stroke();
  }

  _etiqueta(ctx, texto, x, y, u, color, pequena = false) {
    ctx.font = `600 ${(pequena ? 11 : 12.5) * u}px ui-sans-serif, system-ui, sans-serif`;
    const padX = 7 * u;
    const alto = (pequena ? 17 : 21) * u;
    const ancho = ctx.measureText(texto).width + padX * 2;
    const ty = Math.max(0, y - alto - 3 * u);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x, ty, ancho, alto, 5 * u);
    ctx.fill();
    ctx.fillStyle = '#0b0b0d';
    ctx.textBaseline = 'middle';
    ctx.fillText(texto, x + padX, ty + alto / 2 + 0.5 * u);
  }
}
