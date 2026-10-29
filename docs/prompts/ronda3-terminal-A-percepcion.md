# Ronda 3 · Terminal A — Percepción: cuerpo, extremidades, actividad, entra/sale

Trabajas en **Vigia** (`D:\Lukaton1\03 - PROYECTOS TEC\vigia`): videovigilancia local con YOLO (FastAPI + OpenCV + SQLite, Python 3.12, `.venv` en la carpeta). Lee antes `docs/04-estado.md`, `docs/prompts/NOTAS.md` y el código de `vigia/pose.py`, `actividades.py`, `tracking.py`, `analisis.py`, `caras.py`, `dibujo.py`, `detector.py`.

Hay **otra terminal (B)** trabajando a la vez en el mismo árbol (eventos, zonas, API, cámaras) y **Claude** rehaciendo la GUI (`vigia/static/`). Nadie va a estar disponible ~2 horas: **decide con criterio, anota la decisión y sigue. No te pares a preguntar.**

## Problema real (lo que Lucas ha visto con la cámara)
- `persona_entra` / `persona_salio` fallan: falsos positivos, IDs que cambian, salidas que no llegan.
- "sentado", "usando PC", "de pie", "usando móvil" se confunden. El caso típico es **cámara de escritorio con medio cuerpo visible** (caderas/rodillas fuera de plano) y también cuerpo entero a distancia.
- Se quiere ver el **cuerpo y las extremidades** bien detectados (esqueleto estable, no tembloroso).

## Reparto de ficheros (obligatorio)
- **Tuyos:** `vigia/pose.py`, `vigia/actividades.py`, `vigia/tracking.py`, `vigia/analisis.py`, `vigia/caras.py`, `vigia/detector.py`, `vigia/dibujo.py`, ficheros nuevos de percepción (`vigia/suavizado.py`, `vigia/apariencia.py`, `vigia/vivo.py`...), carpeta nueva `eval/`, y sus tests.
- **De B (no tocar):** `web.py`, `eventos.py`, `zonas.py`, `config.py`, `almacen.py`, `captura.py`, `clips.py`, `telegram.py`, `armado.py`, `mantener.py`.
- **De Claude (no tocar):** `vigia/static/*`, `scripts/`, `docs/04-estado.md`.
- Excepciones permitidas, solo con **edit puntual aditivo** (nunca reescribir el fichero): claves nuevas de config en `vigia/config.py` y `config.example.yaml`, y **una línea** en `web.py` para montar tu router `vigia/vivo.py` (ver P4).
- Git: commits pequeños con `git add <rutas concretas>` (nunca `-A` ni `commit -a`). Mensajes en español como el historial. Sin rebase/reset/force. **No hagas push** (lo hace Claude). Si la suite falla por ficheros de B, espera y reintenta; no los arregles tú.
- Servidor de prueba en el puerto **8010** (`VIGIA_PUERTO=8010`). **No abras la webcam** (la usa Lucas/Claude): trabaja con los clips de `clips/*.webm`, `vigia.db` (solo lectura) y datos sintéticos. Tests sin red y sin cámara.
- Suite completa (`.venv\Scripts\python -m pytest -q`) verde antes de cada commit.

## Tareas, por orden de prioridad

### P1. Banco de evaluación offline (primero; sin esto todo es a ciegas)
- `eval/reproducir.py`: pasa un vídeo (los `clips/*.webm` o cualquier mp4) por el **mismo pipeline** que el bucle real (detector + tracking + pose + clasificador + estabilizador + rastreador entra/sale) con reloj simulado a partir de los timestamps del vídeo, y vuelca una línea de tiempo JSON: por frame y por persona (id, caja, keypoints, actividad cruda, actividad estable) y los eventos emitidos.
- `eval/etiquetas/*.json`: etiquetas manuales mínimas por clip (tramos `{desde,hasta,actividad}` y `{t, evento}`); crea las que puedas mirando los clips (extrae frames y míralos). `eval/medir.py` calcula precisión por actividad, nº de `persona_entra` falsos/perdidos y cambios de ID por persona. Imprime una tabla y guarda `eval/salida/*.json`.
- Anota la **línea base** (números antes de mejorar) en `docs/prompts/NOTAS.md` y repite al final de cada tarea para demostrar mejora real.

### P2. Esqueleto y extremidades
- Modelo de pose configurable (`pose_modelo`: n/s/m; el `.pt` se descarga solo si falta) y `pose_imgsz`. Mide coste con `bench/benchmark.py --help` como ejemplo y elige un valor por defecto que no baje de ~15 fps con GPU RTX 3060.
- **Suavizado temporal** de keypoints por persona (One-Euro o EMA adaptativo) con descarte por confianza (`conf_kpt` configurable) y mantenimiento breve del último valor válido. Tests deterministas.
- Asociación pose↔persona rastreada robusta (IoU + distancia de centros, un-a-uno, húngaro si hace falta) en vez de por orden.
- `dibujo.py`: pintar **esqueleto** (huesos por colores brazos/piernas/tronco, puntos con confianza) detrás de un flag `dibujar_esqueleto` (config), y **polígonos de zona** (hoy solo cajas). El vídeo del panel debe seguir fluido.

### P3. Actividad (sentado / de pie / usando PC / usando móvil / durmiendo / caída…)
- Rehacer `actividades.py` con **features geométricas normalizadas por el tamaño del cuerpo** (ángulo del tronco respecto a la vertical, ángulo cadera-rodilla-tobillo, altura de cadera relativa a la caja, relación de aspecto, posición de muñecas respecto a hombros/cara, inclinación y orientación de cabeza con nariz/ojos/orejas).
- **Modo medio cuerpo** explícito: si no hay caderas/rodillas fiables, decidir con hombros/cabeza/brazos y el contexto (mesa, portátil, teclado, monitor detectado por YOLO) en vez de caer en "de pie" o "sentado" por defecto. Si la evidencia es débil devuelve `desconocida` en vez de inventar.
- "usando PC": sentado/medio cuerpo + manos al frente y a la altura del teclado/portátil, o objeto `laptop|keyboard|mouse|tv` solapado, cabeza orientada hacia delante/abajo. "usando móvil": móvil en mano **o** muñeca cerca de la cara con cabeza inclinada (aunque YOLO no vea el móvil).
- Actividades nuevas: `caminando` (desplazamiento sostenido del centro del track), `agachado`, `tumbado`, y `persona_caida` (transición rápida de de pie → tumbado/suelo + inmovilidad N s; evento emitido a través del mismo camino que usa `actividades` hoy, con config `caida_activa`).
- Estabilizador con **histéresis por actividad** (entrar cuesta más que salir) y ventana de voto; sin parpadeos. Todo con tests deterministas sobre keypoints sintéticos (genera poses de muestra: sentado, de pie, medio cuerpo, tumbado, móvil en mano).
- Mide con `eval/` y reporta la mejora.

### P4. Entra/sale fiable + estado en vivo para la GUI
- **Entrada**: confirmar tras `confirmar_entrada_seg` **y** confianza media mínima **y** tamaño mínimo; ignorar apariciones "en medio de la nada" de un frame (sombras, reflejos). Si la persona es nueva pero aparece lejos del borde, exige más tiempo.
- **Re-identificación ligera** al reasociar IDs: además de IoU/distancia, usa un embedding barato de apariencia (histograma de color HSV del torso, o el feature que ya dé el tracker; BoT-SORT con ReID si ultralytics lo trae y no cuesta fps) para decidir si "el ID nuevo" es la misma persona. Config `reid` on/off.
- **Salida**: timeout adaptativo (más corto si la última posición fue en el borde del encuadre, más largo si fue en el centro: probable oclusión). Nunca emitir `persona_salio` sin `persona_entra` previo y viceversa.
- Medir con `eval/`: falsos `persona_entra`, salidas perdidas y cambios de ID. Objetivo: reducir a menos de la mitad frente a la línea base.
- **Estado en vivo**: módulo `vigia/vivo.py` con un router FastAPI (`APIRouter`) y una función `snapshot_vivo()` que publique, protegido por el middleware actual, `GET /vivo` →
  ```json
  {"t": 1730000000.12, "ancho": 960, "alto": 540, "fps": 21.4,
   "personas": [{"id": 3, "nombre": "Lucas|null", "caja": [x1,y1,x2,y2],
                 "actividad": "sentado", "actividad_cruda": "sentado", "desde_seg": 42.1,
                 "kpts": [[x,y,conf],... 17 puntos], "zona": "puerta|null",
                 "conf": 0.88}],
   "objetos": [{"clase": "laptop", "caja": [..], "conf": 0.7}],
   "caras": [{"caja": [..], "nombre": "Lucas|null"}]}
  ```
  Coordenadas en píxeles del frame que sirve `/video.mjpg`. Cacheado (no recalcular por petición). **Añade una sola línea en `web.py`** para `include_router`, y anota el contrato en `NOTAS.md` (sección "Contrato /vivo"). La GUI dibujará el esqueleto y las cajas en un canvas encima del vídeo con esto.

### P5 (si sobra tiempo)
- Caras: asistente de varias muestras con comprobación de calidad (tamaño, nitidez, ángulo) y umbral de reconocimiento por persona; solo backend y API (anota rutas en NOTAS).
- Probar `yolo11s` frente a `yolo11n` para la detección de persona con `eval/`; dejar la recomendación en NOTAS.

## Al terminar cada tarea
Commit pequeño. En `docs/prompts/NOTAS.md`, bajo un encabezado **"Ronda 3 — A"** (no edites lo de otros): qué hiciste, números antes/después de `eval/`, claves de config nuevas, contratos de API y decisiones raras. Si algo queda a medias o sin probar con cámara real, dilo claro.
