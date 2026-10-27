# Vigia — Documento de Diseño

> fecha_creacion: 2026-10-27
> estado: aprobado (visto bueno de Lucas, 27/10/2026)
> area: proyecto
> agente: Alfred + Atlas (estructura), Morgan (si consume dinero)
> tags:
>   - tipo/diseno
>   - area/proyecto-tec
>   - tema/vision-por-computador

## Objetivo

Sistema local de visión por computador que usa la webcam para detectar
personas y objetos, convertir detecciones en **eventos** (entrada, salida,
aparición, desaparición, permanencia, zonas) y exponerlos en un
**dashboard web** con **historial persistente**. 100% local: el video no
sale del PC.

Posibles usos: seguridad/saludos en casa, seguimiento de objetos y
presencia en el escritorio, aprendizaje de visión por computador. En el
futuro: módulo de análisis de movimiento para escalada (estimación de
pose).

## Nombre

Nombre vigía elegido: **Vigia** (antes "Sentinel", descartado por colisión
con otro proyecto de Lucas).

## Arquitectura

```
[Webcam] → [Captura OpenCV]
              ↓
        [Detector YOLO + Tracking (personas y objetos)]
              ↓
        [Motor de Eventos]  ← reglas: entra/sale, aparición/desaparición,
              ↓                 duración, zonas, franja horaria...
        [SQLite] (historial de eventos + configuración)
              ↓
[FastAPI] → Dashboard web local (video en vivo + feed de eventos)
              ↓
        Clip guardado opcional al disparar evento
```

## Stack

| Pieza | Elección | Razón |
|---|---|---|
| Lenguaje | Python 3.11+ | Estándar en visión |
| Captura/frames | OpenCV | Robusto con webcams en Windows |
| Detección | YOLO11n/s (Ultralytics) | Rápido con GPU NVIDIA; personas + 80 objetos COCO |
| Tracking | ByteTrack (integrado en Ultralytics) | IDs persistentes → eventos de entra/sale |
| Motor de eventos | Python puro, reglas configurables | Simple, extensible, testeable |
| BD | SQLite | Sin servidor, historial consultable |
| Web | FastAPI + WebSocket | Video en vivo (JPEG en WebSocket) + feed de eventos |
| UI | HTML/JS simple (vanilla, 1 página) | empezar simple; se puede migrar a React después |

## Al Manejo De Errores

- Cámara no disponible / desenchufada → reintento con backoff + estado
  visible en el dashboard ("cámara caída").
- Modelo no descargado → descarga automática en primer arranque con
  mensaje claro.
- Regla mal configurada → validar al cargar y avisar en log, no crashear.
- Cualquier excepción del loop se registra y el sistema intenta
  recuperarse, no morir silenciosamente.

## Pruebas

- Motor de eventos: tests unitarios con detecciones simuladas (no webcam).
- Tracking: simulacion de trayectorias (una persona entra/sale).
- Web: test de endpoints con FastAPI TestClient.
- Fase de detección: verificación manual con webcam.

## Privacidad Y Seguridad

- Todo local. Sin servicios en la nube.
- La base de datos guarda solo eventos (metadatos), no video continuo.
- Clips (fase 5) guardados en disco local, aún Configurables.
- En el vault: nunca guardar claves en Markdown plano (protocol AGENTS.md).

## Componentes (unidades y límites)

| Unidad | Qué hace | Depende de |
|---|---|---|
| `captura` | Abre webcam, produce frames, gestiona reconexión | OpenCV |
| `detector` | Encola frames → cajas de personas/objetos con IDs | Ultralytics |
| `eventos` | Convierte tracking en eventos según reglas | detector (salidas) |
| `almacen` | Guarda/carga eventos en SQLite, esquema y migraciones | eventos |
| `web` | Sirve dashboard + WS de frames/eventos | almacen, captura |
| `config` | Carga/valida `config.yaml` (zonas, reglas, cámara) | — |

Cada unidad tiene su módulo y su test. El `main.py` los ensambla.

## Roadmap por fases

Ver `02-roadmap.md`.
