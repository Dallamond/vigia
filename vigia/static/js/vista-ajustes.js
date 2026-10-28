/* Vista «Ajustes»: formulario agrupado, validación y editor visual de franjas. */
import { $, toast } from './nucleo.js';
import { api, enJson } from './api.js';
import { EditorFranjas } from './franjas.js';

const NUMEROS = {
  a_ancho: [160, 3840],
  a_alto: [120, 2160],
  a_permanencia: [1, 3600],
  a_timeout_persona: [1, 3600],
  a_timeout_objeto: [1, 3600],
  a_telegram_max: [0, 10000],
  a_telegram_cooldown: [0, 86400],
  a_perm_conocidos: [0, 3600],
  a_reaviso: [0, 1440],
  a_max_personas: [0, 1000],
  a_merodeo_entradas: [1, 100],
  a_merodeo_ventana: [1, 86400],
  a_inmovil: [0, 86400],
  a_ret_eventos: [0, 3650],
  a_ret_clips: [0, 3650],
  a_ret_mb: [0, 1048576],
  a_mantener: [1, 1440],
};

const CLAVES = {
  a_ancho: 'ancho',
  a_alto: 'alto',
  a_permanencia: 'permanencia_seg',
  a_timeout_persona: 'timeout_persona_seg',
  a_timeout_objeto: 'timeout_objeto_seg',
  a_telegram_max: 'telegram_max_hora',
  a_telegram_cooldown: 'telegram_cooldown_seg',
  a_perm_conocidos: 'permanencia_conocidos_seg',
  a_reaviso: 'reaviso_min',
  a_max_personas: 'max_personas',
  a_merodeo_entradas: 'merodeo_entradas',
  a_merodeo_ventana: 'merodeo_ventana_seg',
  a_inmovil: 'inmovil_seg',
  a_ret_eventos: 'retencion_dias',
  a_ret_clips: 'retencion_clips_dias',
  a_ret_mb: 'retencion_clips_mb',
  a_mantener: 'mantener_cada_min',
};

const CHECKBOX = {
  a_clips: 'guardar_clips',
  a_miniaturas: 'miniaturas',
  a_notificar: 'notificar',
  a_actividades: 'actividades',
  a_caras: 'caras',
  a_telegram: 'telegram',
};

export function iniciarAjustes(fuenteDatos) {
  const editor = new EditorFranjas($('franjasEditor'), $('franjasBarra'));
  const anadir = $('btnAnadirFranja');
  if (anadir) anadir.addEventListener('click', () => editor.anadir());
  let ultimaConfig = null;

  window.addEventListener('vigia:camaras', () => {
    if (!ultimaConfig) return;
    const camara = $('a_camara');
    if (camara && ultimaConfig.camara != null) {
      const valor = String(ultimaConfig.camara);
      if (Array.from(camara.options).some((o) => o.value === valor)) camara.value = valor;
    }
  });

  const form = $('formAjustes');
  const recargar = $('ajRecargar');
  if (recargar) recargar.addEventListener('click', () => cargar());

  async function cargar() {
    let config;
    try {
      config = await api('/config');
    } catch (e) {
      if (e.estado !== 401) toast('No se pudo cargar la configuración: ' + e.message, false);
      return;
    }
    ultimaConfig = config;
    for (const [id, clave] of Object.entries(CLAVES)) {
      const campo = $(id);
      if (campo && config[clave] != null) campo.value = config[clave];
      if (campo) campo.classList.remove('campo-error');
    }
    for (const [id, clave] of Object.entries(CHECKBOX)) {
      const campo = $(id);
      if (campo) campo.checked = !!config[clave];
    }
    const armadoAuto = $('a_armado_auto');
    if (armadoAuto) armadoAuto.value = config.armado_auto ? 'true' : 'false';
    const vigiladas = $('a_vigiladas');
    if (vigiladas) vigiladas.value = (config.vigilar_desaparicion || []).join(', ');
    const camara = $('a_camara');
    if (camara && config.camara != null) {
      const valor = String(config.camara);
      if (Array.from(camara.options).some((o) => o.value === valor)) camara.value = valor;
    }
    editor.cargar(config.franjas || []);

    // Las reglas por zona llegan con el bloque 4 de la terminal A. Si aún no
    // están en /config, se avisa y el servidor simplemente las ignorará.
    const aviso = $('ajAvisoZonas');
    if (aviso) aviso.hidden = 'reaviso_min' in config;
  }

  function validar() {
    let bien = true;
    const mensajes = [];
    for (const [id, [min, max]] of Object.entries(NUMEROS)) {
      const campo = $(id);
      if (!campo) continue;
      const valor = Number(campo.value);
      const malo = campo.value === '' || !Number.isFinite(valor) || valor < min || valor > max;
      campo.classList.toggle('campo-error', malo);
      if (malo) {
        bien = false;
        mensajes.push(`${campo.labels?.[0]?.textContent || id}: entre ${min} y ${max}`);
      }
    }
    const erroresFranjas = editor.validar();
    if (erroresFranjas.length) {
      bien = false;
      mensajes.push(...erroresFranjas);
    }
    return { bien, mensajes };
  }

  async function guardar(ev) {
    ev.preventDefault();
    const msg = $('aj_msg');
    const { bien, mensajes } = validar();
    if (!bien) {
      if (msg) { msg.className = 'msg err'; msg.textContent = mensajes[0]; }
      const primero = document.querySelector('.campo-error');
      if (primero) primero.focus();
      return;
    }
    const cuerpo = {};
    for (const [id, clave] of Object.entries(CLAVES)) {
      const campo = $(id);
      if (campo) cuerpo[clave] = Number(campo.value);
    }
    for (const [id, clave] of Object.entries(CHECKBOX)) {
      const campo = $(id);
      if (campo) cuerpo[clave] = campo.checked;
    }
    const camara = $('a_camara');
    if (camara && camara.value !== '') cuerpo.camara = Number(camara.value);
    const armadoAuto = $('a_armado_auto');
    if (armadoAuto) cuerpo.armado_auto = armadoAuto.value === 'true';
    const vigiladas = $('a_vigiladas');
    cuerpo.vigilar_desaparicion = (vigiladas && vigiladas.value || '')
      .split(',').map((s) => s.trim()).filter(Boolean);
    cuerpo.franjas = editor.valor();

    const boton = $('ajGuardar');
    if (boton) boton.disabled = true;
    try {
      const res = await api('/config', enJson('POST', cuerpo));
      if (res && res.ok === false) {
        if (msg) { msg.className = 'msg err'; msg.textContent = 'Aplicado, pero no se pudo guardar en config.yaml'; }
        return;
      }
      if (msg) {
        msg.className = 'msg ok';
        msg.textContent = res && res.reinicio
          ? 'Guardado. Reinicia el servicio para aplicar la nueva resolución.'
          : 'Ajustes guardados.';
      }
      toast(res && res.reinicio
        ? 'Guardado: reinicia para aplicar la resolución'
        : 'Ajustes aplicados');
    } catch (e) {
      if (msg) { msg.className = 'msg err'; msg.textContent = e.message; }
    } finally {
      if (boton) boton.disabled = false;
    }
  }

  if (form) form.addEventListener('submit', guardar);

  return {
    entrar() { cargar(); },
    salir() { /* nada */ },
    cargar,
  };
}
