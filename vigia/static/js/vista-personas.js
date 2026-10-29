/* Vista «Personas»: registro de caras conocidas y actividad (pose). */
import { $, esc, toast } from './nucleo.js';
import { api, enJson } from './api.js';
import { pintarFilas, enlazarFilas } from './detalle.js';

export function iniciarPersonas(fuenteDatos) {
  let activo = false;
  let sondeo = null;
  let refresco = null;

  let firma = '';

  function fotos(tomas, ruta) {
    const hay = tomas.filter((t) => t.foto !== false);
    return hay.length
      ? hay.map((t) => `<img class="toma" loading="lazy" src="/caras/foto/${ruta}/${t.id}" alt="">`).join('')
      : '<span class="toma vacia" title="muestra antigua sin foto">?</span>';
  }

  function tarjetaConocida(p) {
    const n = esc(p.nombre);
    return `<article class="pers" data-persona="${n}">
      <div class="tomas">${fotos(p.tomas, 'rostro')}</div>
      <div class="pers-pie">
        <input type="text" value="${n}" maxlength="60" data-renombrar="${n}" aria-label="Nombre de ${n}">
        <small>${p.tomas.length} toma${p.tomas.length === 1 ? '' : 's'}</small>
        <button type="button" class="btn sm" data-tomar="${n}" title="Añade la cara que se ve ahora como otra muestra de esta persona">+ toma</button>
        <button type="button" class="btn sm danger" data-borrar-persona="${n}" aria-label="Eliminar a ${n}">Eliminar</button>
      </div>
    </article>`;
  }

  function tarjetaDesconocida(g) {
    const visto = (g.ultima || '').slice(5, 16);
    return `<article class="pers sin" data-grupo="${g.id}">
      <div class="tomas">${fotos(g.tomas.map((id) => ({ id })), 'toma')}</div>
      <div class="pers-pie">
        <input type="text" placeholder="Nombre…" maxlength="60" data-nombre-grupo="${g.id}" aria-label="Nombre para la cara ${g.id}">
        <small title="última vez ${esc(g.ultima)}">${g.veces}× · ${esc(visto)}</small>
        <button type="button" class="btn sm primary" data-nombrar="${g.id}">Guardar</button>
        <button type="button" class="btn sm ghost" data-descartar="${g.id}" aria-label="Descartar">✕</button>
      </div>
    </article>`;
  }

  async function listarCaras(forzar = true) {
    const con = $('cConocidas');
    const des = $('cDesconocidas');
    try {
      const d = await api('/caras/personas', { silencioso: !forzar });
      const f = JSON.stringify(d);
      // no repintar si nada cambió o si el usuario está escribiendo un nombre
      if (!forzar && (f === firma || (document.activeElement && document.activeElement.closest('.galeria')))) return;
      firma = f;
      const conocidas = d.conocidas || [];
      const sin = d.desconocidas || [];
      const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
      set('carasCount', `${conocidas.length + sin.length}`);
      set('cConCount', conocidas.length);
      set('cDesCount', sin.length);
      if (con) con.innerHTML = conocidas.length ? conocidas.map(tarjetaConocida).join('')
        : '<div class="empty">todavía no hay nadie registrado</div>';
      if (des) des.innerHTML = sin.length ? sin.map(tarjetaDesconocida).join('')
        : '<div class="empty">ninguna cara pendiente: aparecerán aquí cuando la cámara las vea</div>';
    } catch (e) {
      if (e.estado !== 401 && con) con.innerHTML = '<div class="empty">no se pudo cargar el registro</div>';
    }
  }

  async function accion(fn, ok) {
    try {
      await fn();
      if (ok) toast(ok);
    } catch (err) { toast(err.message, false); }
    listarCaras(true);
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

  const galeria = document.querySelector('[data-vista="personas"]');
  if (galeria && !galeria._enlazado) {
    galeria._enlazado = true;
    galeria.addEventListener('change', (e) => {
      const campo = e.target.closest('[data-renombrar]');
      if (!campo) return;
      const nuevo = campo.value.trim();
      if (!nuevo || nuevo === campo.dataset.renombrar) { campo.value = campo.dataset.renombrar; return; }
      accion(() => api('/caras/persona/renombrar', enJson('POST', { de: campo.dataset.renombrar, a: nuevo })),
        'Nombre actualizado');
    });
    galeria.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const campo = e.target.closest('[data-nombre-grupo]');
      if (!campo) return;
      const b = campo.closest('.pers').querySelector('[data-nombrar]');
      if (b) b.click();
    });
    galeria.addEventListener('click', (e) => {
      const nombrar = e.target.closest('[data-nombrar]');
      const descartar = e.target.closest('[data-descartar]');
      const tomar = e.target.closest('[data-tomar]');
      const borrar = e.target.closest('[data-borrar-persona]');
      if (nombrar) {
        const id = nombrar.dataset.nombrar;
        const campo = galeria.querySelector(`[data-nombre-grupo="${id}"]`);
        const nombre = (campo && campo.value || '').trim();
        if (!nombre) { toast('Escribe un nombre', false); if (campo) campo.focus(); return; }
        accion(() => api(`/caras/desconocidas/${id}/nombrar`, enJson('POST', { nombre })),
          'Cara registrada: ' + nombre);
      } else if (descartar) {
        if (!confirm('¿Descartar esta cara sin identificar?')) return;
        accion(() => api('/caras/desconocidas/' + descartar.dataset.descartar, { method: 'DELETE' }));
      } else if (tomar) {
        accion(() => api('/caras/persona/tomar', enJson('POST', { nombre: tomar.dataset.tomar })),
          'Toma añadida: mira a la cámara');
      } else if (borrar) {
        const n = borrar.dataset.borrarPersona;
        if (!confirm(`¿Eliminar a ${n} y todas sus tomas? Dejará de reconocerse.`)) return;
        accion(() => api('/caras/persona?nombre=' + encodeURIComponent(n), { method: 'DELETE' }),
          n + ' eliminado');
      }
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
        listarCaras(true);
      } catch (err) { toast(err.message, false); }
    });
  }

  const histAct = $('histActividad');
  enlazarFilas(histAct);

  return {
    entrar() {
      activo = true;
      listarCaras(true);
      estadoConfig();
      cargarActividad();
      if (!sondeo) sondeo = sondearCara();
      if (!refresco) refresco = setInterval(() => { if (activo && !document.hidden) listarCaras(false); }, 4000);
    },
    salir() {
      activo = false;
      sondeo = null;
      clearInterval(refresco);
      refresco = null;
    },
    cargar: () => listarCaras(true),
  };
}
