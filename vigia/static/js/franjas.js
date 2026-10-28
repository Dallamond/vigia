/* Editor visual de franjas horarias: filas desde/hasta + barra de 24 h. */
import { esc } from './nucleo.js';

const minutos = (hhmm) => {
  const m = /^(\d{2}):(\d{2})$/.exec(hhmm || '');
  return m ? (+m[1]) * 60 + (+m[2]) : null;
};

export class EditorFranjas {
  constructor(contenedor, barra) {
    this.contenedor = contenedor;
    this.barra = barra;
    this.franjas = [];
    this._enlazar();
  }

  cargar(franjas) {
    this.franjas = Array.isArray(franjas)
      ? franjas.map((f) => ({ desde: f.desde || '22:00', hasta: f.hasta || '08:00' }))
      : [];
    this.pintar();
  }

  valor() {
    return this.franjas
      .filter((f) => f.desde && f.hasta)
      .map((f) => ({ desde: f.desde, hasta: f.hasta }));
  }

  anadir() {
    this.franjas.push({ desde: '22:00', hasta: '08:00' });
    this.pintar();
  }

  /** Devuelve una lista de errores legibles (vacía si todo está bien). */
  validar() {
    const errores = [];
    for (const [i, f] of this.franjas.entries()) {
      if (minutos(f.desde) == null || minutos(f.hasta) == null) {
        errores.push(`franja ${i + 1}: hora inválida`);
        continue;
      }
      if (f.desde === f.hasta) errores.push(`franja ${i + 1}: desde y hasta son iguales`);
    }
    return errores;
  }

  pintar() {
    if (!this.contenedor) return;
    if (!this.franjas.length) {
      this.contenedor.innerHTML = '<div class="empty">sin franjas: activo todo el día</div>';
    } else {
      this.contenedor.innerHTML = this.franjas.map((f, i) => `
        <div class="fila-franja" data-franja="${i}">
          <span class="hint">de</span>
          <input type="time" value="${esc(f.desde)}" data-campo="desde" aria-label="Franja ${i + 1}: desde">
          <span class="hint">a</span>
          <input type="time" value="${esc(f.hasta)}" data-campo="hasta" aria-label="Franja ${i + 1}: hasta">
          <button type="button" class="btn sm danger" data-quitar="${i}" aria-label="Quitar franja ${i + 1}">✕</button>
        </div>`).join('');
    }
    this._pintarBarra();
  }

  _pintarBarra() {
    if (!this.barra) return;
    let html = '';
    const etiquetas = [0, 6, 12, 18, 24];
    for (const h of etiquetas) {
      const estilo = h === 24
        ? 'right:3px'
        : `left:calc(${(h / 24) * 100}% + 3px)`;
      html += `<span class="h" style="${estilo}">${String(h).padStart(2, '0')}:00</span>`;
    }
    for (const f of this.franjas) {
      const d = minutos(f.desde);
      const h = minutos(f.hasta);
      if (d == null || h == null) continue;
      const tramos = d <= h ? [[d, h]] : [[d, 1440], [0, h]];
      for (const [a, b] of tramos) {
        html += `<span style="left:${(a / 1440) * 100}%;width:${((b - a) / 1440) * 100}%"></span>`;
      }
    }
    this.barra.innerHTML = html;
  }

  _enlazar() {
    if (!this.contenedor) return;
    this.contenedor.addEventListener('input', (e) => {
      const campo = e.target.closest('[data-campo]');
      if (!campo) return;
      const fila = campo.closest('[data-franja]');
      const i = Number(fila.dataset.franja);
      if (this.franjas[i]) {
        this.franjas[i][campo.dataset.campo] = campo.value;
        this._pintarBarra();
      }
    });
    this.contenedor.addEventListener('click', (e) => {
      const boton = e.target.closest('[data-quitar]');
      if (!boton) return;
      this.franjas.splice(Number(boton.dataset.quitar), 1);
      this.pintar();
    });
  }
}
