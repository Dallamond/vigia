# GPUs: inventario y benchmarks medidos

> **Todo lo que hay en esta página se midió en el PC de desarrollo el
> 2026-10-28.** No hay cifras estimadas ni copiadas de terceros. Los JSON
> completos están en `bench/salida/`. El VLM no se pudo medir: no había
> servidor local levantado (ver el final).

## 1. Inventario del equipo

| Dato | Valor |
|---|---|
| GPU | NVIDIA GeForce RTX 3060 |
| VRAM | 12 288 MB (10 442 MB libres al medir) |
| Driver | 580.97 |
| Compute capability | 8.6 (28 SMs) |
| PyTorch | 2.6.0+cu124 |
| CUDA compilada / disponible | 12.4 / sí |
| cuDNN | 90100 |
| Ultralytics | 8.4.191 |
| OpenCV | 5.0.0 |
| NumPy | 2.5.2 |
| ONNX Runtime | no instalado |
| Python | 3.12 (Windows) |

Generado con:

```bash
python bench/inventario.py --json bench/salida/inventario.json
```

## 2. Método

- **Fuente principal:** un clip real del proyecto
  (`clips/20261027_212633_permanencia.webm`, 640×480, con una persona).
  Los frames se recorren en bucle.
- **Fuente sintética:** ocho frames generados (degradados y formas), como
  techo de inferencia sin detecciones reales. Se usó solo para comparar.
- **Medición:** `model.predict(...)` de Ultralytics (preproceso + inferencia +
  NMS), con `torch.cuda.synchronize()` antes y después de cada iteración.
  Calentamiento aparte; el tiempo incluye el lote completo.
- **FPS** = frames procesados / tiempo total (throughput).
- **p50/p95** = percentiles de la latencia **por frame** (lote dividido entre
  sus frames).
- **Cámaras** = `floor(FPS × 0,8 / objetivo)`: se reserva un 20 % para lectura
  de cámara, dibujo, tracking y la segunda pasada de pose/caras. Es una cota
  optimista, no una promesa.
- Comandos: `python bench/benchmark.py ...` (ver «Cómo repetirlo»).

## 3. Resultados con clip real

### 3.1 640×480, lote 1, `predict` (sin tracking)

| Modelo | FPS | p50 (ms) | p95 (ms) | Cámaras a 10 FPS | Cámaras a 15 FPS |
|---|---:|---:|---:|---:|---:|
| yolo11n | 46,4 | 18,20 | 39,29 | 3 | 2 |
| yolo11s | 46,4 | 18,96 | 38,40 | 3 | 2 |
| yolo11m | 35,5 | 27,26 | 36,54 | 2 | 1 |
| yolo11n-pose | 46,4 | 20,43 | 28,80 | 3 | 2 |

`bench/salida/bench_clip_640_b1_rep.json` (100 iteraciones, 15 de calentamiento).

### 3.2 640×480, lote 1, `track` (ByteTrack, como el servicio)

| Modelo | FPS | p50 (ms) | p95 (ms) | Cámaras a 10 FPS | Cámaras a 15 FPS |
|---|---:|---:|---:|---:|---:|
| yolo11n | 30,7 | 30,84 | 44,98 | 2 | 1 |
| yolo11n-pose | 31,4 | 28,98 | 48,24 | 2 | 1 |

`bench/salida/bench_clip_640_b1_track.json`. El tracking cuesta ~35 % de FPS
frente a `predict` y sube el p95; es el modo que usa Vigia de verdad.

### 3.3 640×480, lote 4, `predict`

| Modelo | FPS | p50 por frame (ms) | p95 (ms) | Cámaras a 10 FPS | Cámaras a 15 FPS |
|---|---:|---:|---:|---:|---:|
| yolo11n | 137,8 | 7,05 | 8,96 | 11 | 7 |
| yolo11s | 95,0 | 10,48 | 12,90 | 7 | 5 |
| yolo11m | 48,2 | 20,58 | 24,35 | 3 | 2 |
| yolo11n-pose | 130,6 | 7,71 | 8,76 | 10 | 6 |

`bench/salida/bench_clip_640_b4.json`. El batching amortiza mucho en n/s/pose;
en m apenas cambia. Es útil si un solo proceso atiende varias cámaras, pero
añade latencia de cola (no uses lotes grandes si necesitas reacción inmediata).

### 3.4 `imgsz` 1280, lote 1, `predict`

| Modelo | FPS | p50 (ms) | p95 (ms) | Cámaras a 10 FPS | Cámaras a 15 FPS |
|---|---:|---:|---:|---:|---:|
| yolo11n | 36,0 | 26,38 | 38,38 | 2 | 1 |
| yolo11s | 24,5 | 41,33 | 48,69 | 1 | 1 |
| yolo11m | 12,0 | 84,17 | 93,31 | 0 | 0 |
| yolo11n-pose | 32,9 | 29,20 | 41,79 | 2 | 1 |

`bench/salida/bench_clip_1280_b1.json`. Subir `imgsz` a 1280 reduce los FPS a
la mitad o menos: solo tiene sentido con una cámara y si hace falta detalle.

### 3.5 Fuente sintética (referencia, 640, lote 1)

| Modelo | FPS | p50 (ms) | p95 (ms) |
|---|---:|---:|---:|
| yolo11n | 55,2 | 16,60 | 30,53 |
| yolo11s | 53,3 | 15,98 | 36,42 |
| yolo11m | 38,7 | 25,96 | 31,10 |
| yolo11n-pose | 53,3 | 18,15 | 23,68 |

`bench/salida/bench_sintetico_640_b1.json`. Los frames no tienen personas: el
NMS es más barato. Sirve como techo, no como escenario de producción.

## 4. Lectura y recomendación

1. **n y s rinden casi igual a 640 con una imagen;** s no compensa en FPS, pero
   sí puede compensar en precisión (no medida aquí). m baja a ~35 FPS y a
   1280 no llega a 10 FPS: no es la elección para varias cámaras en esta GPU.
2. **El tracking es el coste real.** Con ByteTrack, n/pose quedan en ~31 FPS
   y 29-31 ms p50: **2 cámaras a 10 FPS o 1 a 15 FPS**, dejando margen para la
   segunda pasada de pose/caras.
3. **Presupuesto conservador para producción:** RTX 3060 + `yolo11n` +
   `yolo11n-pose` a 640, 2 cámaras a 10-15 FPS. Si se añaden caras y pose en
   el mismo proceso, empezar por 1-2 y medir con las cámaras reales. No pasar
   a 4-6 cámaras solo porque el lote 4 dé 137 FPS: ese número no incluye
   captura, cola, dibujo ni el resto del pipeline.
4. **640 es el punto dulce** para esta GPU. 1280 solo para una cámara o para
   análisis puntual.
5. **Batch 4** tiene sentido si se procesan cámaras en un único proceso y la
   latencia de 7-8 ms/frame es aceptable; no reduce el coste total por cámara
   de forma mágica, solo mejora la utilización de la GPU.

## 5. Viabilidad de VLM local (Ollama / LM Studio)

Medido el 2026-10-28 a las 09:42 con `python bench/vlm.py`:

| Servidor | Puerto | Estado | Modelos | Latencia | Respuesta |
|---|---|---:|---|---:|---|
| Ollama | 11434 | **no responde** (conexión rechazada) | — | — | — |
| LM Studio | 1234 | **no responde** (conexión rechazada) | — | — | — |

No hay ningún VLM instalado/levantado en este PC, así que **no hay latencia ni
calidad que reportar**. El script ya está listo para medirlo cuando lo haya:

```bash
# Opción Ollama (ejemplo de modelo de visión; descarga aparte)
ollama pull qwen2.5vl:7b
ollama serve
python bench/vlm.py --modelo qwen2.5vl:7b --json bench/salida/vlm.json

# Opción LM Studio: carga un modelo de visión en el servidor local (puerto 1234)
python bench/vlm.py --json bench/salida/vlm.json
```

Criterio para decidir: un VLM solo entra en Vigia si responde en segundos (no
minutos) a una imagen de 640×480 con el prompt real y sin bloquear la GPU de
detección. Eso **no se ha medido**; no se debe asumir que quepa junto a YOLO en
12 GB. Cuando haya servidor, `bench/vlm.py` guarda latencia y tokens/s.

## 6. Lo que no se ha medido

- TensorRT, ONNX Runtime ni cuantización (ONNX Runtime no está instalado).
- CPU (solo hay la variante documentada en `docs/07`; sin cifras).
- Varias cámaras simultáneas reales: los FPS son de un único flujo en bucle.
- Consumo eléctrico, temperatura estable en 24/7 y ruido.
- Precisión (mAP) de n/s/m con las cámaras reales: aquí solo se mide velocidad.

## 7. Cómo repetirlo

```bash
# Inventario del equipo
python bench/inventario.py --json bench/salida/inventario.json

# Tabla principal (clip real, 640, lote 1, tracking como el servicio)
python bench/benchmark.py \
  --modelos yolo11n.pt,yolo11s.pt,yolo11m.pt,yolo11n-pose.pt \
  --fuente clips/20261027_212633_permanencia.webm \
  --resolucion 640 --lote 1 --iteraciones 80 --track \
  --json bench/salida/mi_medicion.json

# Barrido de resolución y lote
python bench/benchmark.py --resolucion 1280 --lote 1 --iteraciones 40
python bench/benchmark.py --lote 4 --iteraciones 60
```

Los pesos `yolo11s.pt`/`yolo11m.pt` se descargan la primera vez (Ultralytics).
No se versionan en git.
