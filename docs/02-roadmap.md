# Vigia — Roadmap

> fecha_creacion: 2026-10-27
> estado: activo
> area: proyecto
> agente: Alfred
> tags:
>   - tipo/roadmap
>   - area/proyecto-tec
>   - tema/vision-por-computador
> relacionado:
>   - "[[01-diseno]]"
>   - "[[04-estado]]"
>   - "[[03-plan-fase-1]]"

## Estados de fase

`: no_empezada · en_proceso · hecha · pausada`

Fuentes de verdad para "cómo va": este archivo + `04-estado.md`.

## Fases

### Fase 1 — Base: captura + detección en vivo · `no_empezada`

- Entorno Python + GPU (CUDA/torch) funcionando.
- OpenCV abre la webcam y muestra frames.
- YOLO11n detecta personas y objetos en tiempo real con cajas dibujadas.
- Criterio de cierre: ventana en vivo con FPS estables, distinción de
  persona/objeto en etiquetas, cierre limpio con `q`.

### Fase 2 — Tracking de personas · `no_empezada`

- ByteTrack: IDs persistentes entre frames.
- Eventos básicos: `persona_ entra`, `persona_salio`, `presencia` (cuántos ahora).
- Prueba manual: entrar y salir de cámara y ver IDs/estados correctos.
- Criterio de cierre: consola imprime eventos correctos 4/5 intentos.

### Fase 3 — Motor de eventos · `no_empezada`

- Reglas configurables en `config.yaml`:
  - `objeto_desaparece` (objetivo: laptop, mochila, teléfono...)
  - `objeto_aparece`
  - `permanencia` (persona X segundos en zonas)
  - `zona` (reglas solo si ocurren dentro de un rectángulo definido)
  - `franja_horaria` (solo activo entre hh:mm y hh:mm, ej. de madrugada)
- Todo evento → SQLite.
- Tests unitarios del motor con detecciones simuladas.
- Criterio de cierre: suite de tests del motor en verde + eventos en BD.

### Fase 4 — Dashboard web · `no_empezada`

- FastAPI + WebSocket: video en vivo en el navegador + feed de eventos
  en tiempo real.
- Historial consultable (fecha, tipo, zona).
- Estado del sistema: cámara OK/caída, FPS, modelo cargado.
- Criterio de cierre: abrir `localhost:8000`, ver en vivo y eventos con
  latencia < 1s.

### Fase 5 — Extras y robustez · `no_empezada`

- Clip guardado ±5s al disparar evento.
- Reconexión total con la cámara y memoria aislada.
- Panel de zonas configurable (dibujar rectángulo en el navegador).
- De aquí sale la base del módulo futura: **escalada / estimación de pose**
  (MediaPipe Pose sobre los mismos frames).

### Fuera de alcance (por ahora)

- Reconocimiento facial de identidades (privacidad + complejidad).
- Cloud, multi-cámara, app móvil.
- Entrenamiento de modelo propio.

## Aprendizaje acumulado

Registrado en `04-estado.md` a medida que avancemos — materia prima
para el futuro módulo de escalada.
