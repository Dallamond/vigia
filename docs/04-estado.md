# Vigia — Estado del Proyecto

> fecha_actualizacion: 2026-10-29
> estado_general: fases_1-8_hechas · plan_v2_hecho_salvo_camaras_ip · ronda3_gui_v3_y_percepcion_integradas
> area: proyecto
> agente: Alfred (día a día), Atlas (estructura)
> tags:
>   - tipo/referencia
>   - area/proyecto-tec
>   - tema/vision-por-computador
> relacionado:
>   - "[[01-diseno]]"
>   - "[[02-roadmap]]"
>   - "[[03-plan-fase-1]]"

## Dónde está

- **Carpeta:** `03 - PROYECTOS TEC/vigia/`
- **Repositorio:** dentro del vault (git). Carpeta de código en
  `vigia/` con su propio `.gitignore`.

## Estado actual (28/10/2026)

| Fase | Estado | Notas |
|---|---|---|
| 1 — Base (captura + YOLO en vivo) | hecha | 16 FPS, GPU 60fps, CAP_DSHOW+MJPG |
| 2 — Tracking de personas | hecha | ByteTrack + eventos entra/sale + presencia |
| 3 — Motor de eventos | hecha | permanencia, zonas, franjas, objetos → SQLite |
| 4 — Dashboard web | hecha | layout 2 columnas, historial filtrable, clips listados, ajustes ⚙ |
| 5 — Extras | hecha | clips ±5s MP4 (poda max 60) + toast Windows |
| 6 — Actividades | hecha | pose 17 kpts + clasificador estable (5 actividades) |
| 7 — Caras + registro de conocidos | hecha | YuNet+SFace local, registro desde el panel (☺ Caras), eventos `persona_reconocida` / `cara_desconocida` |
| 8 — Pulido (27/10 noche) | hecha | clips WebM, línea del tiempo inferior en directo, selector de webcam, rutas ancladas al proyecto |
| v2-1 — Seguridad | hecha | token (`VIGIA_TOKEN`), cookie, 401, límite de intentos, cabeceras, anti-CSRF, sin proxy sin token |
| v2-2 — Telegram | hecha, sin probar con bot real | long polling, prioridades, anti-spam, armado, comandos, avisos del sistema |
| v2-3 — GUI | hecha | 7 vistas modulares, login, zonas (caja/polígono/exclusión), franjas, tema, móvil |
| v2-4 — Reglas por zona | hecha, solo con tests | permanencia por zona, conocidos, restringida, aforo, merodeo, inmovilidad |
| v2-6 — Despliegue | escrito, sin probar | Docker CUDA, compose, systemd/NSSM, Caddy, backup (Docker no instalado aquí) |
| v2-10 — GPUs | medido (RTX 3060) | `docs/08-gpus.md`; VLM sin medir (Ollama/LM Studio apagados) |
| Ronda 3 — GUI v3 (29/10) | hecha | barra lateral, tokens light-dark, vista Resumen, overlay de esqueleto sobre el vídeo (/vivo), tarjetas de persona, galería de clips, eventos paginados, logs, SSE, líneas de cruce en el editor |
| Ronda 3 — percepción | integrada, sin validar con cámara | pose suavizada (One-Euro), actividad por geometría normalizada, caídas, reasociación por apariencia, banco `eval/` |
| Ronda 3 — eventos | integrada | /resumen /estadisticas /logs /eventos/stream, líneas de cruce, zona_entra/zona_sale, reglas de zona fuera de franja |
| Pulido 28/10 | hecho | caras (cajas YuNet), IDs estables, actividad con votación, FPS 11→~22 (pose/caras y cámara en hilos), 960x540 |

## Registro de cambios

| Fecha | Cambio |
|---|---|
| 2026-10-27 | Proyecto creado. Diseño aprobado por Lucas. Docs: diseño, roadmap, plan fase 1, estado. |
| 2026-10-27 | Fase 1 en proceso: entorno venv Python 3.12 + torch 2.6.0+cu124 (RTX 3060, cuda OK). Código: captura/detector/main/web (+probar_camara). 9/9 tests pasan. Dashboard mínimo lanzado en :8000. |
| 2026-10-27 | Aprendido: 1) instalar torch cu124 y que pip/ultralytics no lo substituya por la rueda CPU de PyPI; 2) MSMF falla con estas webcam (abre, no lee) → CAP_DSHOW + MJPG resuelto; 3) StreamingResponse para MJPEG, no Response. |
| 2026-10-27 | FPS optimizado de 7.5 → ~16: la webcam limita a ~14-16fps; captura óptima 640x480 (detección GPU: 60 fps). ByteTrack integrado (IDs persona + eventos entra/sale con timeout 10s). Fix: cajas sin ID en frames sueltos. |
| 2026-10-27 | FASES 2-5 IMPLEMENTADAS: tracking (tests ✅), motor de eventos (permanencia/zonas/franjas/objetos + SQLite `almacen.py`, tests ✅), dashboard completo (/historial, /eventos, /estado, /clip/...), clips ±5s MP4 (`clips.py`) + toast Windows (`notificaciones.py`). Integración con test_web.py de la otra terminal adaptada. 42/42 tests verdes. Push por fase: v1, y commit fases 2-5. |
| 2026-10-27 | Auditoria paralela integrada: 64/64. Ronda A push `e960675`: UI 2 columnas (video izq / paneles der) + parte inferior reservada (actividades) + ajustes en caliente via ⚙ menú (/config POST → config.yaml) + /clips con poda (max 60) + franjas coherentes en entra/sale + MJPEG compartido con Condition (sin hilo de sondeo por cliente) + borrado motor.py (duplicado) + arreglo de encoding en pose.py. |
| 2026-10-27 | Ronda B push: `pose.py` (YOLO11n-pose con seguimiento) + `actividades.py` (durmiendo/ sentado/ de pie/ usando movil/ usando PC con Estabilizador de 3 s, 7 tests). Bucle integra pose con carga diferida y desactivación automática si el modelo falla. /actividades JSON + parte inferior muestra "persona #N: actividad". 71/71. |
| 2026-10-27 | Verificación en vivo: servidor arriba en :8000; smoke test de endpoints OK (config con actividades=true; /actividades con persona #1; /clips 6 mp4 de permanencia ya guardados y listados). Suite estable 71/71. Repo sincronizado (main = `ad59047`). Fase 7 (caras + registro de conocidos) aprobada como siguiente. |
| 2026-10-27 | Auditoría paralela integrada (logs rotativos `logs.py`, mantenimiento automático `mantener.py` con retención de BD/clips, UI en `vigia/static/index.html`, endpoints /timeline /salud /zonas /media). Fix en `actividades.py` (atributo `anunciada` explícito). Suite 102/102 en ese punto. |
| 2026-10-27 | FASE 7 CARAS: `caras.py` (YuNet detección + SFace embeddings, 100% local, onnx auto-descargados ~40MB a `modelos/`), tabla `rostros` en SQLite, bucle integra identidad asociando cara↔persona por caja (nombre en pantalla + evento `persona_reconocida`; desconocidos → `cara_desconocida` con cooldown 120 s). API del registro: GET/POST/DELETE `/caras/registro` con recarga en caliente (sin reiniciar). `caras` en ajustes bool + config.yaml. Suite 105/105. Servidor verificado en :8000 (~9 FPS con pose+caras). |
| 2026-10-27 | Auditoría y cierre: los clips pasan de `mp4v` (no reproducible en navegador) a WebM VP8, con buffer comprimido, escritura atómica y `--convertir` para los antiguos. Línea del tiempo horizontal a ancho completo, en directo; `/timeline?desde&hasta`. Caras: modelos en segundo plano, análisis cada 3 frames, `persona_reconocida` (antes solo documentada) y registro desde el panel con validación. Selector de webcam en caliente (`/camaras`). Rutas ancladas a la raíz, `config.yaml` fuera de git, config atómica, bucle partido en `analisis.py`. Suite 177/177. |
| 2026-10-27 | Plan v2 apuntado en `docs/06-plan-v2.md`: reorganización de la GUI, caras, permanencia, priorización, personas y acciones mejoradas, Telegram, cámaras IP Wi-Fi, servidor local e investigación de GPUs. Servidor lanzado en :8000 con la versión nueva. |
| 2026-10-28 | **A1 seguridad**: `host`/`puerto` en `config.yaml` (entorno gana), `VIGIA_TOKEN` + `.env` sin dependencias, middleware que exige token a API/vídeo (Bearer, cookie `vigia_token` HttpOnly SameSite=Lax, `?token=` solo en vídeo/snapshot), `/auth/login|logout|estado`, limitación de intentos por IP, negativa a arrancar en red sin token, cabeceras de seguridad y validación anti-traversal en `/clip` y `/media`. Tests nuevos en `tests/test_seguridad.py`. |
| 2026-10-28 | **A2 Telegram**: cliente Bot API por long polling con stdlib, secretos en `.env`, avisos con miniatura y clip WebM en hilo aparte, prioridades configurables, anti-spam (máximo/hora + cooldown + "+N eventos más"), armado persistente con `/armar /desarmar /estado /foto` y franjas automáticas, avisos de cámara caída/disco/servicio reiniciado y endpoints `/armado`, `/telegram/estado`, `/telegram/prueba`. Tests en `tests/test_telegram.py` y `tests/test_armado.py`. |
| 2026-10-28 | **A3 zonas y reglas**: zonas como objeto `{caja|poligono, permanencia_seg, franjas, restringida, excluir, max_personas, vigilar, conocidos}` manteniendo el formato antiguo; permanencia por zona con histéresis y re-aviso; conocidos con umbral propio o excluidos; eventos `zona_restringida`, `demasiadas_personas`, `merodeo`, `persona_inmovil`; polígonos y exclusión; objetos vigilados por zona. Tests en `tests/test_zonas.py`. |
| 2026-10-28 | **B1 GUI modular**: panel reescrito en 7 vistas con router por hash y módulos ES en `vigia/static/js/`, CSS propio (`panel.css`), tema auto/oscuro/claro, responsive móvil y ARIA/atajos; editor visual de zonas (cajas, polígonos, exclusión y reglas por zona) compatible con el formato antiguo y el nuevo, franjas horarias con barra de 24 h, ajustes agrupados con validación, multicámara preparada, login con token y polling pausable aislado (`datos.js`) para un futuro SSE. Verificado en Chrome real (1440×900 y 390×844) con `scripts/verificar_gui.mjs`; login, guardado de zonas y de ajustes probados con `scripts/servir_gui_dev.py` (8020/8021). 236/236 tests. |
| 2026-10-28 | **B2 despliegue**: `Dockerfile` CUDA con usuario no root y `ARG` para CPU, `docker-compose.yml` con GPU, solo loopback y el proyecto montado, `.dockerignore`; unit `deploy/vigia.service` y `deploy/instalar-servicio-windows.ps1` (NSSM); `deploy/Caddyfile` con TLS interno y nota de VPN; `scripts/backup.py` con la API de backup de SQLite, `quick_check`, copia de `config.yaml` y rotación (la BD incluye `rostros`) + 4 tests; `docs/07-despliegue.md` con PC vs Proxmox/LXC, variante CPU, HTTPS/VPN, restauración y checklist. Docker/systemd/NSSM/Caddy no se han ejecutado aquí (no están instalados en este PC); el backup sí se probó contra la BD real. 260/260 tests. |
| 2026-10-28 | **B3 GPUs medida**: `bench/inventario.py` (torch + `nvidia-smi`), `bench/benchmark.py` (YOLO11 n/s/m y pose con clip real y frames sintéticos, `imgsz`/lote/`--track` configurables, FPS y p50/p95 por frame, estimación de cámaras a 10/15 FPS con 20 % de margen) y `bench/vlm.py` (sonda de Ollama/LM Studio); JSON medidos en `bench/salida/` y conclusiones en `docs/08-gpus.md`. En la RTX 3060: yolo11n 46,4 FPS / p50 18,2 ms a 640 sin tracking; con tracking 30,7 FPS / p50 30,8 ms, es decir 2 cámaras a 10 FPS o 1 a 15. Ni Ollama (11434) ni LM Studio (1234) respondían, así que el VLM queda sin medir. 263/263 tests. |
| 2026-10-28 | **Revision y pulido (Claude)**: caras (cajas YuNet xywh), IDs estables + confirmacion de entrada, clasificador de actividad reescrito con votacion, lector de camara y pose/caras en hilos (11 -> ~18-20 fps; 960x540 por limite del driver a 720p), snapshot limpio para zonas, middleware anti-CSRF y anti-proxy sin token, backend de camara portable (Linux). Suite 267. |

| 2026-10-29 | **Ronda 3**: dos terminales (opencode) hicieron percepción (A) y eventos/plataforma (B) y se pararon a medias; Claude revisó, integró y commiteó su trabajo. Fallos reales corregidos al revisar: histéresis de líneas comparaba un producto vectorial (no píxeles), el cooldown inicial suprimía el primer cruce, y los objetos globales no se reiniciaban fuera de franja. 294 tests (16 nuevos en `tests/test_percepcion_ronda3.py`). GUI v3 completa hecha por Claude; `/video.mjpg?limpio=1` añadido. |

## Datos técnicos acumulados

- GPU: NVIDIA RTX 3060 (driver 580.97). torch 2.6.0+cu124,
  `cuda.is_available() == True`.
- Medido con `bench/benchmark.py` (clip 640×480, lote 1): yolo11n 46,4 FPS /
  p50 18,2 ms / p95 39,3 ms sin tracking; con tracking 30,7 FPS / p50 30,8 ms
  (≈2 cámaras a 10 FPS o 1 a 15). Lote 4: 137,8 FPS. A 1280: yolo11n 36,0 FPS,
  yolo11m 12,0 FPS. Detalle en `docs/08-gpus.md`.
- Webcam: solo funciona con backend **CAP_DSHOW + FOURCC MJPG** (MSMF abre pero no entrega frames).
- Python 3.12 (.venv en la carpeta del proyecto).
- FPS medidos al primer arranque del dashboard: ~7 (calentando), pendiente valor estable.

## Aprendizajes

- **Medio cuerpo (29/10):** con la cámara de escritorio los hombros salen con confianza ~0,9 pero caderas 0,02–0,4 y rodillas ~0,01. Con eso **sentado vs de pie no se puede decidir de forma fiable por pose**. Para mejorar de verdad: colocar la cámara más lejos/alta para ver caderas y rodillas, o usar contexto (mesa, portátil, movimiento). `eval/` mide hoy solo 4 clips con etiquetas rápidas: no es ground truth.

(vacío — cada fase deja aquí qué aprendimos, en especial lo que
aporte al futuro módulo de escalada)

## Próxima acción concreta

Siguiente bloque grande del plan v2: **cámaras IP RTSP y multicámara (bloque 5)**.
Antes, cerrar lo que está escrito pero sin comprobar (lista de abajo).

## Tareas pendientes (28/10/2026)

### A. Probar en real (lo escrito pero no comprobado)
- [ ] **Actividad**: sentarse/levantarse con el móvil y ajustar umbrales de
      `actividades.py` si "sentado"/"de pie" fallan en tu postura real.
- [ ] **Caras**: registrar otra muestra de frente (sin capucha) y comprobar
      `persona_reconocida`; ver que Lucas ya no dispara permanencia.
- [ ] **Reglas por zona** con cámara real (restringida, aforo, merodeo, inmóvil).
- [ ] **Telegram** con un bot real: `.env` con `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID`,
      `telegram: true`, `POST /telegram/prueba`, `/armar /desarmar /estado /foto`.
- [ ] **Token**: crear `.env` con `VIGIA_TOKEN` antes de abrir el panel a la red.
- [ ] **Docker/systemd/NSSM/Caddy**: probar en una máquina donde estén instalados.
- [ ] **Cámara**: `config.yaml` sigue con `camara: 0`; elegir la del móvil.

### B. Código pendiente (por prioridad)
1. **Cámaras IP RTSP/ONVIF y multicámara** (bloque 5): lista de cámaras en config,
   un hilo y un motor por cámara, `camara_id` en eventos/clips, substream para
   detectar y mainstream para clips, reconexión robusta.
2. **Personas** (bloque 7): conjunto de prueba con clips reales, probar `yolo11s/m`,
   BoT-SORT con re-ID, máscaras de ignorar, validar visión nocturna, ONNX/TensorRT.
3. **Acciones** (bloque 8): ventana temporal de keypoints, `persona_caida`, "usando
   móvil/PC" por orientación de cabeza, más actividades.
4. **Caras** (bloque 9): asistente de varias muestras, comprobación de calidad,
   registrar desde un evento del historial, umbral por persona, re-ID de cuerpo,
   política de privacidad de los embeddings.
5. **`/logs`**: endpoint para la vista Sistema (hoy muestra "pendiente").
6. **Polígonos en el vídeo**: `dibujo.py` solo pinta cajas.
7. **Telegram**: clip WebM → MP4 H.264 si el móvil no lo reproduce (TODO en `telegram.py`).
8. **VLM local** (Ollama/LM Studio) para describir eventos y resumen diario: medir
   latencia y VRAM; solo en eventos, nunca por frame.
9. **Pequeños**: unificar `detail`/`detalle` en las respuestas de error; reglas por
   zona fuera de la franja global; persona en dos zonas se asigna solo a la primera;
   validar rango de coordenadas de zonas; `main.py` (ventana de escritorio) no usa
   los hilos nuevos.

### C. Decisiones abiertas (de `06-plan-v2.md`)
- ¿Cuántas cámaras IP, marca/modelo (RTSP/ONVIF), Wi-Fi o PoE?
- ¿Servidor en Proxmox (homelab) o en el PC con la RTX 3060?
- ¿Acceso desde fuera de casa? (VPN recomendada.)
- ¿Política para guardar caras de otras personas (visitas, familia)?

### Datos técnicos útiles
- Cámara del móvil: 1280x720 solo da 10 fps (driver); **960x540 da 30 fps**.
- Pose y caras van en un hilo aparte; con ambas activas el panel marca ~18-22 fps.

## Cómo se actualiza este archivo

Al final de cada sesión de trabajo en Vigia:
1. Actualizar tabla de fases.
2. Añadir fila en el registro de cambios.
3. Si hubo datos técnicos (FPS, versiones), anotarlos.
4. Dejar la "próxima acción concreta" apuntando al paso actual.
