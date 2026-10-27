# Vigia — Estado del Proyecto

> fecha_actualizacion: 2026-10-27
> estado_general: fases_1-8_hechas · plan_v2_apuntado
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

## Estado actual (27/10/2026)

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

## Datos técnicos acumulados

- GPU: NVIDIA RTX 3060. torch 2.6.0+cu124, `cuda.is_available() == True`.
- Webcam: solo funciona con backend **CAP_DSHOW + FOURCC MJPG** (MSMF abre pero no entrega frames).
- Python 3.12 (.venv en la carpeta del proyecto).
- FPS medidos al primer arranque del dashboard: ~7 (calentando), pendiente valor estable.

(vacío — a medida que ejecutamos fases aquí se registran: FPS reales,
versión de CUDA que funcionó, modelos, resolución usada...)

## Aprendizajes

(vacío — cada fase deja aquí qué aprendimos, en especial lo que
aporte al futuro módulo de escalada)

## Próxima acción concreta

Fases 1-8 cerradas (177 tests). Siguiente: **plan v2** en `06-plan-v2.md`.
Orden: 1) seguridad/token, 2) Telegram, 3) reorganización de la GUI,
4) permanencia y reglas por zona, 5) cámaras IP RTSP, 6) servidor local,
7-9) mejora de personas/acciones/caras, 10) investigación de GPUs (en paralelo).
Antes de empezar hay decisiones abiertas al final de ese documento.

## Cómo se actualiza este archivo

Al final de cada sesión de trabajo en Vigia:
1. Actualizar tabla de fases.
2. Añadir fila en el registro de cambios.
3. Si hubo datos técnicos (FPS, versiones), anotarlos.
4. Dejar la "próxima acción concreta" apuntando al paso actual.
