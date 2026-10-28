/* Vista «Personas»: registro de caras conocidas y actividad (pose). */
import { $, esc, toast } from './nucleo.js';
import { api, enJson } from './api.js';
import { pintarFilas, enlazarFilas } from './detalle.js';

export function iniciarPersonas(fuenteDatos) {
  let activo = false;
  let sondeo = null;

  async function listarCaras() {
    const lista = $('cLista');
    try {
      const caras = await api('/caras/registro');
      const total = $('carasCount');
      if (total) total.textContent = `${(caras || []).length} muestra${(caras || []).length === 1 ? '' : 's'}`;
      if (!lista) return;
      lista._caras = Array.isArray(caras) ? caras : [];
      lista.innerHTML = lista._caras.length ? lista._caras.map((c) => `
        <div class="zitem" data-cara="${c.id}">
          <div class="cabecera">
            <input type="text" value="${esc(c.nombre)}" maxlength="60" data-nombre-cara="${c.id}" aria-label="Nombre de la muestra ${c.id}">
            <small>muestra #${esc(c.id)}</small>
            <button type="button" class="btn sm danger" data-borrar-cara="${c.id}" aria-label="Borrar muestra ${esc(c.id)}">✕</button>
          </div>
        </div>`).join('') : '<div class="empty">todavía no hay nadie registrado</div>';
    } catch (e) {
      if (e.estado !== 401 && lista) lista.innerHTML = '<div class="empty">no se pudo cargar el registro</div>';
    }
  }

  async function estadoConfig() {
    try {
      const config = await api('/config');
      const aviso = $('cAviso');
      if (aviso) {
        if (!config.caras) {
          aviso.className = 'msg err';
          aviso.innerHTML = 'El reconocimiento de caras está desactivado: actívalo en ' +
            '<a href="#/ajustes">Ajustes → Detección</a>.';
        } else {
          aviso.className = 'msg';
          aviso.textContent = '';
        }
      }
    } catch (e) { /* opcional */ }
  }

  async function sondearCara() {
    while (activo) {
      try {
        const d = await api('/caras/ultima', { silencioso: true });
        const boton = $('cRegistrar');
        const estado = $('cEstado');
        const img = $('cImg');
        if (boton) boton.disabled = !(d && d.disponible);
        if (estado) {
          estado.textContent = d && d.disponible
            ? 'cara detectada: lista para registrar'
            : 'buscando una cara… mira a la cámara';
        }
        if (img) {
          img.hidden = !(d && d.disponible);
          if (d && d.disponible) img.src = '/caras/ultima.jpg?t=' + Date.now();
        }
      } catch (e) { /* sondeo silencioso */ }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  async function cargarActividad() {
    try {
      const eventos = await fuenteDatos.historial({ tipo: 'actividad', limite: 30 });
      const caja = $('histActividad');
      if (caja) pintarFilas(caja, eventos || [], 'sin actividad registrada');
    } catch (e) { /* opcional */ }
  }

  const lista = $('cLista');
  if (lista && !lista._enlazado) {
    lista._enlazado = true;
    lista.addEventListener('change', async (e) => {
      const campo = e.target.closest('[data-nombre-cara]');
      if (!campo) return;
      try {
        await api('/caras/registro', enJson('POST', { id: Number(campo.dataset.nombreCara), nombre: campo.value }));
        toast('Nombre actualizado');
        listarCaras();
      } catch (err) {
        toast(err.message, false);
        listarCaras();
      }
    });
    lista.addEventListener('click', async (e) => {
      const borrar = e.target.closest('[data-borrar-cara]');
      if (!borrar) return;
      if (!confirm('¿Borrar esta muestra de cara? No se puede deshacer.')) return;
      try {
        await api('/caras/registro/' + borrar.dataset.borrarCara, { method: 'DELETE' });
        listarCaras();
      } catch (err) { toast(err.message, false); }
    });
  }

  const registrar = $('cRegistrar');
  if (registrar) {
    registrar.addEventListener('click', async () => {
      const campo = $('cNombre');
      const nombre = (campo && campo.value || '').trim();
      if (!nombre) {
        toast('Escribe un nombre', false);
        if (campo) campo.focus();
        return;
      }
      try {
        await api('/caras/registro', enJson('POST', { nombre, capturar: true }));
        toast('Cara registrada: ' + nombre);
        if (campo) campo.value = '';
        listarCaras();
      } catch (err) { toast(err.message, false); }
    });
  }

  const histAct = $('histActividad');
  enlazarFilas(histAct);

  return {
    entrar() {
      activo = true;
      listarCaras();
      estadoConfig();
      cargarActividad();
      if (!sondeo) sondeo = sondearCara();
    },
    salir() {
      activo = false;
      sondeo = null;
    },
    cargar: listarCaras,
  };
}
