/* Editor visual de zonas: cajas y polígonos, zonas de exclusión y reglas.
 * Soporta el formato nuevo de /zonas (objetos) y el antiguo (array de 4). */
import { $, esc, toast } from './nucleo.js';
import { api, enJson } from './api.js';

const COLORES = ['#38bdf8', '#4ade80', '#fbbf24', '#a78bfa', '#f87171', '#2dd4bf', '#f472b6'];

const vacia = () => ({
  nombre: '',
  caja: null,
  poligono: null,
  permanencia_seg: null,
  permanencia_conocidos_seg: null,
  franjas: [],
  restringida: false,
  excluir: false,
  max_personas: 0,
  vigilar: [],
  conocidos: true,
});

function desdeBackend(nombre, valor) {
  const z = vacia();
  z.nombre = nombre;
  if (Array.isArray(valor)) {
    z.caja = valor.slice(0, 4);
    return z;
  }
  if (!valor || typeof valor !== 'object') return z;
  if (Array.isArray(valor.poligono)) z.poligono = valor.poligono.map((p) => [Number(p[0]), Number(p[1])]);
  else if (Array.isArray(valor.caja)) z.caja = valor.caja.slice(0, 4);
  z.permanencia_seg = valor.permanencia_seg ?? null;
  z.permanencia_conocidos_seg = valor.permanencia_conocidos_seg ?? null;
  z.franjas = Array.isArray(valor.franjas) ? valor.franjas : [];
  z.restringida = !!valor.restringida;
  z.excluir = !!valor.excluir;
  z.max_personas = Number(valor.max_personas) || 0;
  z.vigilar = Array.isArray(valor.vigilar) ? valor.vigilar.slice() : [];
  z.conocidos = valor.conocidos !== false;
  return z;
}

export class EditorZonas {
  constructor(fuenteDatos) {
    this.datos = fuenteDatos;
    this.zonas = [];
    this.dims = { ancho: 640, alto: 480 };
    this.soporteNuevo = false;
    this.lineas = [];           // [{nombre,p1,p2,sentido,margen_px,cooldown_seg}]
    this.soporteLineas = false;
    this.modo = 'caja';
    this.arrastre = null;
    this.poligono = null;      // puntos en construcción
    this.raton = null;
    this.activo = false;
    this._enlazado = false;
  }

  async activar() {
    this.activo = true;
    if (!this._enlazado) { this._enlazar(); this._enlazado = true; }
    await this.cargar();
  }

  desactivar() {
    this.activo = false;
    this.poligono = null;
    this.arrastre = null;
  }

  async cargar() {
    try {
      const datos = await api('/zonas');
      const bruto = (datos && datos.zonas) || {};
      this.dims = { ancho: (datos && datos.ancho) || 640, alto: (datos && datos.alto) || 480 };
      const valores = Object.values(bruto);
      // Sin zonas no hay forma de distinguir formatos: el backend actual
      // devuelve siempre el objeto nuevo, así que se asume nuevo.
      this.soporteNuevo = !valores.length ||
        valores.some((v) => v && !Array.isArray(v) && typeof v === 'object');
      this.zonas = Object.entries(bruto).map(([n, v]) => desdeBackend(n, v));
      const dims = $('zDims');
      if (dims) dims.textContent = `${this.dims.ancho}×${this.dims.alto}`;
      this._avisoFormato();
    } catch (e) {
      if (e.estado !== 401) toast('Zonas: ' + e.message, false);
      this.zonas = [];
    }
    await this._cargarLineas();
    this._pintarLista();
    await this.captura();
  }

  async _cargarLineas() {
    try {
      const d = await api('/lineas', { tolerante: true });
      this.soporteLineas = d != null;
      this.lineas = d ? Object.entries(d.lineas || {}).map(([nombre, l]) => ({
        nombre,
        p1: (l.p1 || [0, 0]).map(Number),
        p2: (l.p2 || [0, 0]).map(Number),
        sentido: l.sentido || 'ambos',
        margen_px: l.margen_px ?? 6,
        cooldown_seg: l.cooldown_seg ?? 2,
      })) : [];
    } catch (e) {
      this.soporteLineas = false;
      this.lineas = [];
    }
    const boton = document.querySelector('#zModo [data-modo="linea"]');
    if (boton) boton.hidden = !this.soporteLineas;
    const tarjeta = $('zLineasCard');
    if (tarjeta) tarjeta.hidden = !this.soporteLineas;
    this._pintarListaLineas();
  }

  _avisoFormato() {
    const caja = $('zAviso');
    const txt = $('zAvisoTxt');
    if (!caja || !txt) return;
    caja.hidden = this.soporteNuevo;
    if (!this.soporteNuevo) {
      txt.textContent = 'El backend actual usa el formato antiguo de zonas (solo cajas). ' +
        'Los polígonos y las reglas por zona se activarán cuando la terminal A entregue el ' +
        'nuevo /zonas; el editor ya está preparado.';
    }
  }

  async captura() {
    const cv = $('zCanvas');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    this._fondo(ctx, cv);
    if (this.datos && this.datos.estado && this.datos.estado.camara_ok === false) {
      this._img = null;
      this._sinCamara(ctx, cv);
      return;
    }
    const img = new Image();
    img.onload = () => {
      cv.width = img.naturalWidth || this.dims.ancho;
      cv.height = img.naturalHeight || this.dims.alto;
      this.dims = { ancho: cv.width, alto: cv.height };
      const dims = $('zDims');
      if (dims) dims.textContent = `${cv.width}×${cv.height}`;
      this._img = img;
      this.pintar();
    };
    img.onerror = () => {
      this._img = null;
      this._sinCamara(ctx, cv);
    };
    img.src = '/snapshot.jpg?limpio=1&t=' + Date.now();
  }

  _fondo(ctx, cv) {
    ctx.fillStyle = '#05080b';
    ctx.fillRect(0, 0, cv.width, cv.height);
  }

  _sinCamara(ctx, cv) {
    this._fondo(ctx, cv);
    this.pintar();
    ctx.save();
    ctx.fillStyle = 'rgba(230, 237, 245, .55)';
    ctx.font = '600 18px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('cámara no disponible: dibuja igualmente sobre el fondo', cv.width / 2, cv.height / 2);
    ctx.restore();
  }

  pintar() {
    const cv = $('zCanvas');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    // el fondo (captura) se repinta aquí: un clearRect a secas lo borraba
    this._fondo(ctx, cv);
    if (this._img) ctx.drawImage(this._img, 0, 0, cv.width, cv.height);

    this.zonas.forEach((z, i) => {
      const color = z.excluir ? '#f87171' : z.restringida ? '#fbbf24' : COLORES[i % COLORES.length];
      ctx.save();
      ctx.strokeStyle = color;
      ctx.fillStyle = color + '22';
      ctx.lineWidth = 3;
      ctx.setLineDash(z.excluir ? [8, 5] : []);
      if (z.poligono && z.poligono.length >= 3) {
        ctx.beginPath();
        z.poligono.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      } else if (z.caja) {
        const [x1, y1, x2, y2] = z.caja;
        ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      }
      if (z.caja || (z.poligono && z.poligono.length)) {
        const [lx, ly] = z.caja
          ? [z.caja[0], z.caja[1]]
          : z.poligono[0];
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(0,0,0,.62)';
        const anchoEtiqueta = Math.max(70, z.nombre.length * 9 + 16);
        const etiqueta = z.excluir ? `⛔ ${z.nombre}` : z.restringida ? `⚠ ${z.nombre}` : z.nombre;
        ctx.fillRect(lx, Math.max(0, ly - 24), anchoEtiqueta, 22);
        ctx.fillStyle = color;
        ctx.font = '600 14px system-ui';
        ctx.fillText(etiqueta, lx + 6, Math.max(15, ly - 8));
      }
      ctx.restore();
    });

    this.lineas.forEach((l) => this._pintarLinea(ctx, l));
    if (this.arrastre && this.arrastre.linea) {
      this._pintarLinea(ctx, { nombre: '', p1: [this.arrastre.x1, this.arrastre.y1],
        p2: [this.arrastre.x2, this.arrastre.y2], sentido: 'ambos' }, true);
    } else if (this.arrastre) {
      ctx.save();
      ctx.strokeStyle = this.modo === 'excluir' ? '#f87171' : '#e6edf5';
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 2;
      const a = this.arrastre;
      ctx.strokeRect(a.x1, a.y1, a.x2 - a.x1, a.y2 - a.y1);
      ctx.restore();
    }
    if (this.poligono && this.poligono.length) {
      ctx.save();
      ctx.strokeStyle = this.modo === 'excluir' ? '#f87171' : '#e6edf5';
      ctx.lineWidth = 2;
      ctx.beginPath();
      this.poligono.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      if (this.raton) ctx.lineTo(this.raton[0], this.raton[1]);
      ctx.stroke();
      ctx.fillStyle = '#e6edf5';
      for (const [x, y] of this.poligono) ctx.fillRect(x - 3, y - 3, 6, 6);
      ctx.restore();
    }
  }

  _pintarLinea(ctx, l, borrador = false) {
    const [x1, y1] = l.p1;
    const [x2, y2] = l.p2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const largo = Math.hypot(dx, dy) || 1;
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    // lado A = izquierda del vector p1->p2 (producto vectorial > 0); B = derecha
    const nbx = dy / largo;
    const nby = -dx / largo;
    const color = '#22d3ee';
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 3;
    ctx.setLineDash(borrador ? [6, 5] : []);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.setLineDash([]);
    for (const [x, y] of [[x1, y1], [x2, y2]]) {
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
    }
    const flecha = (sx, sy, ex, ey) => {
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      const ang = Math.atan2(ey - sy, ex - sx);
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex - 10 * Math.cos(ang - 0.45), ey - 10 * Math.sin(ang - 0.45));
      ctx.lineTo(ex - 10 * Math.cos(ang + 0.45), ey - 10 * Math.sin(ang + 0.45));
      ctx.closePath();
      ctx.fill();
    };
    ctx.lineWidth = 2;
    const L = 34;
    // la flecha A→B apunta hacia el lado B (derecha del vector)
    if (l.sentido !== 'b_a') flecha(mx - nbx * 4, my - nby * 4, mx + nbx * L, my + nby * L);
    if (l.sentido !== 'a_b') flecha(mx + nbx * 4, my + nby * 4, mx - nbx * L, my - nby * L);
    ctx.font = '700 13px system-ui';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const marca = (txt, px, py) => {
      ctx.fillStyle = 'rgba(0,0,0,.62)';
      ctx.fillRect(px - 11, py - 10, 22, 20);
      ctx.fillStyle = color;
      ctx.fillText(txt, px, py);
    };
    marca('A', mx - nbx * (L + 18), my - nby * (L + 18));
    marca('B', mx + nbx * (L + 18), my + nby * (L + 18));
    if (l.nombre) {
      ctx.textAlign = 'left';
      ctx.fillStyle = 'rgba(0,0,0,.62)';
      const ancho = Math.max(60, l.nombre.length * 8 + 14);
      ctx.fillRect(x1, Math.max(0, y1 - 24), ancho, 22);
      ctx.fillStyle = color;
      ctx.fillText(l.nombre, x1 + 6, Math.max(11, y1 - 13));
    }
    ctx.restore();
  }

  _pintarListaLineas() {
    const lista = $('lList');
    if (!lista) return;
    if (!this.lineas.length) {
      lista.innerHTML = '<div class="empty">sin líneas: elige «Línea» y arrastra sobre la imagen (p. ej. en la puerta)</div>';
      return;
    }
    lista.innerHTML = this.lineas.map((l, i) => `<div class="zitem" data-linea="${i}">
      <div class="cabecera">
        <input type="text" data-lnombre="${i}" value="${esc(l.nombre)}" maxlength="60" aria-label="Nombre de la línea">
        <button type="button" class="btn sm ghost" data-invertir="${i}" title="Intercambia los lados A y B">Invertir</button>
        <button type="button" class="btn sm danger" data-lborrar="${i}" aria-label="Borrar línea ${esc(l.nombre)}">✕</button>
      </div>
      <div class="reglas">
        <label>Sentido que avisa
          <select data-lcampo="sentido">
            <option value="ambos"${l.sentido === 'ambos' ? ' selected' : ''}>los dos (A↔B)</option>
            <option value="a_b"${l.sentido === 'a_b' ? ' selected' : ''}>solo A→B</option>
            <option value="b_a"${l.sentido === 'b_a' ? ' selected' : ''}>solo B→A</option>
          </select>
        </label>
        <label>Espera entre avisos (seg)
          <input type="number" min="0" step="1" data-lcampo="cooldown_seg" value="${l.cooldown_seg}">
        </label>
      </div>
    </div>`).join('');
  }

  _nuevoNombreLinea() {
    let n = 1;
    while (this.lineas.some((l) => l.nombre === 'linea' + n)) n++;
    return 'linea' + n;
  }

  _pintarLista() {
    const lista = $('zList');
    if (!lista) return;
    if (!this.zonas.length) {
      lista.innerHTML = '<div class="empty">sin zonas: arrastra sobre la imagen</div>';
      return;
    }
    lista.innerHTML = this.zonas.map((z, i) => {
      const tipo = z.poligono ? 'polígono' : 'caja';
      const puntos = z.poligono ? `${z.poligono.length} vértices` : (z.caja || []).join(', ');
      const reglas = this.soporteNuevo ? `
        <div class="reglas">
          <label>Permanencia (seg)
            <input type="number" min="0" step="1" data-regla="permanencia_seg" value="${z.permanencia_seg ?? ''}" placeholder="global">
          </label>
          <label>Máx. personas
            <input type="number" min="0" step="1" data-regla="max_personas" value="${z.max_personas || 0}">
          </label>
          <label>Permanencia conocidos (seg)
            <input type="number" min="0" step="1" data-regla="permanencia_conocidos_seg" value="${z.permanencia_conocidos_seg ?? ''}" placeholder="global">
          </label>
          <label>Objetos a vigilar
            <input type="text" data-regla="vigilar" value="${esc((z.vigilar || []).join(', '))}" placeholder="laptop, mochila">
          </label>
        </div>
        <div class="checks">
          <label class="check"><input type="checkbox" data-regla="restringida" ${z.restringida ? 'checked' : ''}> restringida fuera de franja</label>
          <label class="check"><input type="checkbox" data-regla="excluir" ${z.excluir ? 'checked' : ''}> exclusión</label>
          <label class="check"><input type="checkbox" data-regla="conocidos" ${z.conocidos ? 'checked' : ''}> conocidos cuentan</label>
        </div>` : '<div class="hint" style="margin-top:6px">reglas por zona: pendientes del backend</div>';
      return `<div class="zitem${z.excluir ? ' excluir' : ''}" data-zona="${i}">
        <div class="cabecera">
          <input type="text" data-nombre="${i}" value="${esc(z.nombre)}" maxlength="60" aria-label="Nombre de la zona">
          <small>${tipo}</small>
          <button type="button" class="btn sm danger" data-borrar="${i}" aria-label="Borrar zona ${esc(z.nombre)}">✕</button>
        </div>
        <small>${esc(puntos)}</small>
        ${reglas}
      </div>`;
    }).join('');
  }

  _coords(ev) {
    const cv = $('zCanvas');
    const r = cv.getBoundingClientRect();
    const x = Math.round((ev.clientX - r.left) * (cv.width / r.width));
    const y = Math.round((ev.clientY - r.top) * (cv.height / r.height));
    return [Math.max(0, Math.min(x, cv.width)), Math.max(0, Math.min(y, cv.height))];
  }

  _nuevoNombre() {
    let n = 1;
    while (this.zonas.some((z) => z.nombre === 'zona' + n)) n++;
    return 'zona' + n;
  }

  _cerrarPoligono() {
    if (this.poligono && this.poligono.length >= 3) {
      const z = vacia();
      z.nombre = this._nuevoNombre();
      z.poligono = this.poligono.slice();
      z.excluir = this.modo === 'excluir';
      this.zonas.push(z);
      this._pintarLista();
    }
    this.poligono = null;
    this.pintar();
  }

  _enlazar() {
    const cv = $('zCanvas');
    if (!cv) return;
    document.querySelectorAll('#zModo button').forEach((b) => {
      b.addEventListener('click', () => {
        this.modo = b.dataset.modo;
        this.poligono = null;
        document.querySelectorAll('#zModo button').forEach((x) => {
          const on = x === b;
          x.classList.toggle('on', on);
          x.setAttribute('aria-pressed', String(on));
        });
        const ayuda = $('zAyuda');
        if (ayuda) {
          ayuda.textContent = this.modo === 'poligono'
            ? 'Haz clic en cada vértice; cierra con doble clic, Intro o el botón «Cerrar polígono».'
            : this.modo === 'linea'
              ? 'Arrastra para trazar una línea de cruce (p. ej. la puerta). Avisa cuando alguien la atraviesa; A y B marcan los lados.'
            : this.modo === 'excluir'
              ? 'Arrastra para marcar una zona de exclusión (póster, espejo): lo que caiga dentro se ignora.'
              : 'Arrastra sobre la imagen para dibujar una zona rectangular.';
        }
        this.pintar();
      });
    });

    cv.addEventListener('pointerdown', (ev) => {
      if (this.modo === 'poligono' || this.modo === 'excluir') return;
      const [x, y] = this._coords(ev);
      this.arrastre = { x1: x, y1: y, x2: x, y2: y, linea: this.modo === 'linea' };
      cv.setPointerCapture(ev.pointerId);
    });
    cv.addEventListener('pointermove', (ev) => {
      if (this.arrastre) {
        const [x, y] = this._coords(ev);
        this.arrastre.x2 = x;
        this.arrastre.y2 = y;
        this.pintar();
      } else if (this.poligono) {
        this.raton = this._coords(ev);
        this.pintar();
      }
    });
    cv.addEventListener('pointerup', () => {
      if (!this.arrastre) return;
      const a = this.arrastre;
      this.arrastre = null;
      if (a.linea) {
        if (Math.hypot(a.x2 - a.x1, a.y2 - a.y1) < 24) { this.pintar(); return; }
        this.lineas.push({ nombre: this._nuevoNombreLinea(), p1: [a.x1, a.y1], p2: [a.x2, a.y2],
          sentido: 'ambos', margen_px: 6, cooldown_seg: 2 });
        this._pintarListaLineas();
        this.pintar();
        return;
      }
      const x1 = Math.min(a.x1, a.x2);
      const x2 = Math.max(a.x1, a.x2);
      const y1 = Math.min(a.y1, a.y2);
      const y2 = Math.max(a.y1, a.y2);
      if (x2 - x1 < 12 || y2 - y1 < 12) { this.pintar(); return; }
      const z = vacia();
      z.nombre = this._nuevoNombre();
      z.caja = [x1, y1, x2, y2];
      z.excluir = this.modo === 'excluir';
      this.zonas.push(z);
      this._pintarLista();
      this.pintar();
    });
    cv.addEventListener('click', (ev) => {
      if (this.modo !== 'poligono' && this.modo !== 'excluir') return;
      const [x, y] = this._coords(ev);
      if (ev.detail >= 2) { this._cerrarPoligono(); return; }
      if (!this.poligono) this.poligono = [];
      this.poligono.push([x, y]);
      this.pintar();
    });
    cv.addEventListener('mouseleave', () => {
      if (this.poligono) { this.raton = null; this.pintar(); }
    });
    document.addEventListener('keydown', (ev) => {
      if (!this.activo) return;
      if (ev.key === 'Enter' && this.poligono) { ev.preventDefault(); this._cerrarPoligono(); }
      if (ev.key === 'Escape' && this.poligono) { this.poligono = null; this.pintar(); }
    });

    const deshacer = $('zDeshacer');
    if (deshacer) deshacer.addEventListener('click', () => {
      if (this.poligono && this.poligono.length) {
        this.poligono.pop();
        this.pintar();
      }
    });
    const captura = $('zCaptura');
    if (captura) captura.addEventListener('click', () => this.captura());
    const guardar = $('zGuardar');
    if (guardar) guardar.addEventListener('click', () => this.guardar());

    const ll = $('lList');
    if (ll) {
      ll.addEventListener('click', (ev) => {
        const borrar = ev.target.closest('[data-lborrar]');
        const invertir = ev.target.closest('[data-invertir]');
        if (borrar) this.lineas.splice(Number(borrar.dataset.lborrar), 1);
        else if (invertir) {
          const l = this.lineas[Number(invertir.dataset.invertir)];
          if (l) [l.p1, l.p2] = [l.p2, l.p1];
        } else return;
        this._pintarListaLineas();
        this.pintar();
      });
      ll.addEventListener('change', (ev) => {
        const item = ev.target.closest('[data-linea]');
        const l = item && this.lineas[Number(item.dataset.linea)];
        if (!l) return;
        if (ev.target.matches('[data-lnombre]')) {
          const nuevo = ev.target.value.trim().slice(0, 60);
          if (!nuevo || this.lineas.some((o) => o !== l && o.nombre === nuevo)) {
            ev.target.value = l.nombre;
            if (nuevo) toast('Ya existe una línea con ese nombre', false);
            return;
          }
          l.nombre = nuevo;
        } else if (ev.target.matches('[data-lcampo]')) {
          const c = ev.target.dataset.lcampo;
          l[c] = c === 'sentido' ? ev.target.value : Math.max(0, Number(ev.target.value) || 0);
        }
        this.pintar();
      });
    }

    const lista = $('zList');
    if (lista) {
      lista.addEventListener('click', (ev) => {
        const borrar = ev.target.closest('[data-borrar]');
        if (!borrar) return;
        this.zonas.splice(Number(borrar.dataset.borrar), 1);
        this._pintarLista();
        this.pintar();
      });
      const cambiar = (ev) => {
        const item = ev.target.closest('[data-zona]');
        if (!item) return;
        const i = Number(item.dataset.zona);
        const z = this.zonas[i];
        if (!z) return;
        const campo = ev.target.closest('[data-regla]');
        if (campo) {
          const regla = campo.dataset.regla;
          if (campo.type === 'checkbox') z[regla] = campo.checked;
          else if (regla === 'vigilar') z[regla] = campo.value.split(',').map((s) => s.trim()).filter(Boolean);
          else if (regla === 'permanencia_seg' || regla === 'permanencia_conocidos_seg') {
            z[regla] = campo.value === '' ? null : Number(campo.value);
          } else z[regla] = Number(campo.value) || 0;
          this.pintar();
        }
        const nombre = ev.target.closest('[data-nombre]');
        if (nombre) {
          const nuevo = nombre.value.trim().slice(0, 60);
          if (!nuevo) { nombre.value = z.nombre; return; }
          if (nuevo !== z.nombre && this.zonas.some((o) => o !== z && o.nombre === nuevo)) {
            toast('Ya existe una zona con ese nombre', false);
            nombre.value = z.nombre;
            return;
          }
          z.nombre = nuevo;
          this.pintar();
        }
      };
      lista.addEventListener('change', (ev) => {
        cambiar(ev);
        // los checkboxes y números actualizan también el estilo de la caja
        const item = ev.target.closest('[data-zona]');
        if (item) {
          const z = this.zonas[Number(item.dataset.zona)];
          if (z) item.classList.toggle('excluir', !!z.excluir);
        }
      });
    }
  }

  serializar() {
    const salida = {};
    for (const z of this.zonas) {
      const nombre = (z.nombre || '').trim();
      if (!nombre) continue;
      if (!this.soporteNuevo) {
        if (z.caja) salida[nombre] = z.caja;
        continue;
      }
      const objeto = {
        permanencia_seg: z.permanencia_seg,
        permanencia_conocidos_seg: z.permanencia_conocidos_seg,
        franjas: z.franjas || [],
        restringida: !!z.restringida,
        excluir: !!z.excluir,
        max_personas: Number(z.max_personas) || 0,
        vigilar: z.vigilar || [],
        conocidos: z.conocidos !== false,
      };
      if (z.poligono && z.poligono.length >= 3) objeto.poligono = z.poligono;
      else if (z.caja) objeto.caja = z.caja;
      else continue;  // zona a medio dibujar: no se envía
      salida[nombre] = objeto;
    }
    return salida;
  }

  async guardar() {
    const zonas = this.serializar();
    if (!this.soporteNuevo && this.zonas.some((z) => z.poligono)) {
      toast('Los polígonos necesitan el formato nuevo de zonas (pendiente de A)', false);
      return;
    }
    try {
      await api('/zonas', enJson('PUT', { zonas }));
      if (this.soporteLineas) {
        const lineas = {};
        for (const l of this.lineas) {
          lineas[l.nombre] = { p1: l.p1, p2: l.p2, sentido: l.sentido,
            margen_px: l.margen_px, cooldown_seg: l.cooldown_seg };
        }
        await api('/lineas', enJson('PUT', { lineas }));
      }
      toast('Zonas y líneas guardadas y aplicadas');
      await this.cargar();
    } catch (e) {
      toast('Zonas: ' + e.message, false);
    }
  }
}
