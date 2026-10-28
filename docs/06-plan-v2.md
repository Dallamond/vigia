# Vigia — Plan v2 (priorizado)

> fecha_creacion: 2026-10-27
> estado: en curso (bloques 1-4, 6 y 10 hechos; ver 04-estado.md)
> area: proyecto
> agente: Alfred
> tags:
>   - tipo/plan
>   - area/proyecto-tec
>   - tema/vision-por-computador
> relacionado:
>   - "[[04-estado]]"
>   - "[[05-mejoras]]"

Objetivo: pasar de "un PC con una webcam" a **un servicio en tu servidor local,
con varias cámaras IP Wi-Fi, que te avisa por Telegram** y cuyas detecciones
son fiables. Este documento recoge lo pedido el 27/10/2026, ordenado por
dependencias: cada bloque desbloquea el siguiente.

## Orden recomendado

| Prio | Bloque | Por qué va en este puesto | Esfuerzo |
|---|---|---|---|
| 1 | Seguridad y base de despliegue | Sin auth no se puede exponer nada en red; es requisito de 5 y 6 | S |
| 2 | Alertas por Telegram | Es lo que convierte el sistema en útil cuando no estás delante | M |
| 3 | Reorganización de la GUI | Hay que hacerla antes de añadir más cámaras y reglas, o crecerá torcida | M-L |
| 4 | Permanencia y reglas por zona | Más valor por línea de código; lo que más falsea hoy | M |
| 5 | Cámaras IP Wi-Fi (RTSP) y multicámara | Cambia la arquitectura del bucle: mejor con 1-4 ya limpios | L |
| 6 | Servidor local (Docker/systemd) | Depende de 1 y 5 | M |
| 7 | Detección de personas mejorada | Se mide con material real de las cámaras IP (5) | M |
| 8 | Detección de acciones mejorada | Depende de buena pose (7) | L |
| 9 | Caras: mejora | Depende de calidad de imagen de las cámaras IP | M |
| 10 | Investigación GPUs | Transversal; los benchmarks deciden 5-8 | M (investigación) |

> El 10 se puede hacer en paralelo desde ya: sus resultados (FPS por modelo y
> por cámara) condicionan cuántas cámaras caben y qué modelos usar.

---

## 1. Seguridad y base de despliegue
- Token de acceso (cabecera o cookie) para toda la API y el vídeo; `VIGIA_TOKEN`
  por variable de entorno. Sin token, solo `127.0.0.1`.
- Mover la config de host/puerto a `config.yaml` (hoy solo variables de entorno).
- HTTPS delante con un proxy inverso (Caddy/nginx); no exponer el puerto directo.
- Limitar `POST /config` y `/caras/*` a sesiones autenticadas.
- Aviso en el panel si se accede por HTTP sin token fuera de localhost.

## 2. Alertas por Telegram
- Bot por *long polling* (no abre puertos). Token del bot y `chat_id` en `.env`,
  nunca en `config.yaml`.
- Mensaje con miniatura y, cuando esté listo, el clip WebM (Telegram acepta
  vídeo; convertir a MP4 H.264 si el cliente móvil no reproduce WebM).
- Reglas de aviso: tipo de evento + franja + máximo por hora + cooldown por
  persona/zona (sin esto se desactiva por spam).
- Modo **armado/desarmado**: botón en el panel, comandos `/armar`, `/desarmar`,
  `/estado`, `/foto` en el bot, y armado automático por franja o por ausencia
  del móvil de casa.
- Avisos del propio sistema: cámara caída > 5 min, disco < 5 GB, servicio reiniciado.
- Prioridad de eventos: crítico (desconocido en franja armada) / aviso (permanencia)
  / informativo (entra/sale, no notificar).

## 3. Reorganización completa de la GUI
Hoy `index.html` son ~1.100 líneas con CSS y JS mezclados y todo en una pantalla.
- **Separar** en ficheros (`static/panel.css`, `panel.js` por módulos) o pasar a
  un framework ligero (Preact/Alpine) sin paso de build obligatorio.
- **Navegación por vistas**: *En vivo* (vídeo + línea del tiempo), *Eventos*
  (historial, filtros, detalle), *Clips*, *Personas* (caras + actividad),
  *Zonas y reglas*, *Sistema* (salud, logs, cámaras), *Ajustes*.
- **Multicámara**: rejilla de cámaras con una principal; la línea del tiempo
  filtra por cámara.
- Ajustes agrupados por tema con descripción y validación en el formulario
  (hoy: un modal largo y un JSON de franjas escrito a mano → editor visual de franjas).
- Estado vivo por SSE/WebSocket en vez de *polling* cada 1,5 s.
- Responsive de verdad (móvil primero: es lo que abrirás desde el sofá o fuera).
- Accesibilidad y tema claro/oscuro; atajos de teclado.
- Lo ya hecho que se conserva: línea del tiempo en directo, detalle con clip,
  editor de zonas, registro de caras.

## 4. Permanencia mejorada y reglas por zona
Problemas actuales: `permanencia_seg` es global; una zona = una caja; el aviso
se dispara una vez y no vuelve a hacerlo; no distingue conocidos de desconocidos.
- Permanencia **por zona** (cada zona con su umbral y su franja).
- Reglas ricas: zona restringida fuera de horario, más de N personas, **merodeo**
  (vuelve a entrar varias veces), inmóvil durante X (posible caída), cruce con
  dirección (entra/sale por la puerta).
- Re-aviso periódico mientras sigue ahí (cada X min), con tiempo acumulado.
- **Conocidos no disparan** permanencia (o con otro umbral); desconocidos sí.
- Histéresis: tolerar pérdidas cortas de ID de ByteTrack sin reiniciar el contador
  (hoy hay una gracia fija de `timeout_persona_seg`).
- Objetos vigilados **por zona** y no solo por clase global.
- Polígonos en vez de rectángulos y zonas de *exclusión* (un póster, un espejo).

## 5. Cámaras IP Wi-Fi (RTSP) y multicámara
- `Camara` ya acepta una fuente `str`: añadir RTSP/ONVIF con reconexión robusta,
  *timeouts* y búfer mínimo (si no, el vídeo se queda varios segundos atrasado).
- Config como **lista de cámaras** (`id`, `nombre`, `url`, `zonas`, `franjas`,
  `activa`); un hilo de captura + un motor de eventos por cámara; la BD añade
  `camara_id` a eventos y clips.
- Usar el **substream** de baja resolución para detectar y el *mainstream* solo
  para clips/miniaturas.
- Descubrimiento ONVIF y prueba de conexión desde el panel.
- Wi-Fi: planificar ancho de banda (varias cámaras 1080p saturan un AP) y
  preferir cableado/PoE cuando se pueda.
- Reparto de GPU entre cámaras (ver bloque 10) y *frame skipping* adaptativo.

## 6. Servidor local
- Empaquetar: `Dockerfile` (CUDA runtime si hay GPU) + `docker-compose.yml`
  con volúmenes para `vigia.db`, `clips/`, `modelos/`, `config.yaml`.
- Alternativa sin Docker: servicio `systemd` (Linux) o NSSM (Windows) con reinicio
  automático.
- Dónde: decidir entre una VM/LXC en Proxmox (el homelab) con GPU en *passthrough*
  o el PC con la RTX 3060. Las mini-PC y la Raspberry no sirven para inferencia pesada.
- Proxy inverso + HTTPS + acceso remoto por VPN (WireGuard/Tailscale) en lugar de
  abrir puertos al exterior.
- Copias de seguridad de la BD y de `rostros` (los embeddings no se regeneran
  solos); rotación de logs ya hecha.
- Monitorización: `/salud` ya existe; añadir endpoint Prometheus o simple *healthcheck*.

## 7. Detección de personas mejorada
- Medir antes de cambiar: crear un **conjunto de prueba** con clips reales de
  cada cámara (con y sin gente, noche, contraluz) y puntuar falsos positivos/negativos.
- Probar `yolo11s/m` frente a `yolo11n` (más precisión, más GPU), umbral de
  confianza por cámara y por clase.
- Tracking: ByteTrack → BoT-SORT con *re-identificación* para no perder el ID
  tras oclusiones (hoy cada pérdida genera un `persona_entra` falso).
- Máscaras de ignorar (televisor, ventana con tráfico, póster) y tamaño mínimo
  de caja.
- Visión nocturna/IR: validar el modelo con imagen en gris.
- Exportar a ONNX/TensorRT para ganar FPS (ver bloque 10).

## 8. Detección de acciones mejorada
Hoy: heurística sobre keypoints de un solo frame + estabilizador de 3 s.
- **Ventana temporal** de keypoints (1-2 s) en lugar de un frame: distingue
  "sentándose" de "sentado", "cayendo" de "tumbado".
- **Caídas** (`persona_caida`): velocidad/ángulo del torso + inmovilidad posterior;
  alerta prioritaria.
- Mejorar *usando móvil/PC* con detección de objeto cerca de manos y orientación
  de la cabeza; hoy depende de que YOLO vea el objeto.
- Más acciones: comer/beber, hacer ejercicio, entrar/salir por puerta.
- Plan a más largo plazo: clasificador entrenado (p. ej. un modelo de acción sobre
  esqueletos) con datos propios etiquetados desde los clips guardados.
- Medir con el mismo conjunto de prueba del bloque 7.

## 9. Caras: mejora de la función
- Registro con **varias muestras** guiado (frente, perfil, gafas): ya se admiten
  varias por nombre; falta el asistente y mostrar cuántas tiene cada persona.
- Umbral por persona y comprobación de calidad (tamaño, desenfoque, ángulo) antes
  de aceptar una muestra.
- Registrar desde un **evento del historial** ("esta cara desconocida es Ana").
- Cara pequeña/lejana: recorte de la persona con *upscale* antes de detectar.
- Complementar con **re-ID de cuerpo** para seguir identificando cuando no se ve la cara.
- Privacidad: los embeddings son datos biométricos; documentar su borrado y mantenerlos
  cifrados en disco/copias.

## 10. Investigación: usar tus GPUs para procesar y dar información
Preguntas a responder (con medidas, no a ojo):
1. **Inventario**: qué GPUs hay y dónde (la RTX 3060 de este PC y cuáles más; VRAM,
   driver, en qué máquina está cada una).
2. **Rendimiento**: FPS de `yolo11n/s/m` y pose en PyTorch vs ONNX Runtime vs
   TensorRT; cuántas cámaras a 10-15 FPS caben por GPU.
3. **Arquitectura multi-GPU**: una GPU por tarea (detección / pose+caras) o
   reparto por cámaras; cola de frames con proceso de inferencia separado.
4. **Servidor de inferencia** (NVIDIA Triton u ONNX Runtime server) para que
   las cámaras se procesen en un nodo y la web viva en otro.
5. **Información extra con modelos locales**: ya tienes Ollama y LM Studio
   instalados; probar un modelo de visión (VLM) para **describir cada evento en
   una frase** ("una persona deja una mochila junto a la puerta") y para
   búsqueda en lenguaje natural del historial. Medir latencia y VRAM; ejecutarlo
   solo en eventos, nunca en cada frame.
6. **Resumen diario**: un LLM local que resuma el día a partir de los eventos
   (cuántas personas, a qué horas, qué es inusual) y lo mande por Telegram.
7. Consumo eléctrico y ruido: ¿compensa tener la GPU encendida 24/7?

Entregable: una tabla de benchmarks en `docs/` y una recomendación de hardware
por número de cámaras.

---

## Decisiones abiertas (a responder antes de empezar)
- ¿Cuántas cámaras IP y de qué marca/modelo (RTSP/ONVIF)? ¿Wi-Fi o PoE?
- ¿Dónde va el servidor: Proxmox del homelab o el PC con la GPU?
- ¿Qué GPUs tienes disponibles exactamente (para el bloque 10)?
- ¿Acceso desde fuera de casa? (VPN recomendada frente a abrir puertos.)
- ¿Se puede guardar cara/biometría de otras personas (visitas, familia)? Conviene
  decidir la política antes de ampliar el registro.
