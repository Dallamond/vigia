# Vigia — Mejoras propuestas

> fecha_creacion: 2026-10-27
> estado: aprobado (sprints A y C + GUI)
> area: proyecto
> agente: Alfred
> tags:
>   - tipo/plan
>   - area/proyecto-tec
>   - tema/vision-por-computador
> relacionado:
>   - "[[01-diseno]]"
>   - "[[02-roadmap]]"
>   - "[[04-estado]]"

## Diagnóstico

Vigia ya detecta, rastrea, clasifica actividades y guarda eventos. Lo que le
falta para ser útil de verdad es otra cosa:

1. **Que te enteres cuando no estás delante.**
2. **Que revisar un evento cueste segundos, no minutos.**
3. **Que aguante semanas encendido sin tocarlo.**
4. **Que se pueda configurar sin editar YAML a mano.**

Todo lo de abajo está ordenado por esa lógica, no por lucimiento técnico.

---

## Sprint A — "que sirva de verdad" (en curso)

| # | Mejora | Estado |
|---|---|---|
| A1 | **Miniatura JPEG por evento**: cada evento guarda un `.jpg` del instante; el historial deja de ser solo texto | hecho |
| A2 | **Clip enlazado al evento**: columnas `miniatura` y `clip` en SQLite + `anotar_media()` | hecho |
| A3 | **Migración automática de la BD**: bases antiguas reciben las columnas nuevas al abrirse | hecho |
| A4 | **Retención**: purga de eventos por días y de media por días/tamaño, con CLI `python -m vigia.mantener` | hecho |
| A5 | **Log a fichero rotativo** (`logs/vigia.log`, 5×2 MB) | hecho |
| A6 | **`/salud`**: uptime, versión, tamaño de BD, clips, espacio libre en disco | hecho |
| A7 | **`/timeline`**: eventos de un día en orden, para el carril horario | hecho |

## Sprint C — "que se use a gusto" (en curso)

| # | Mejora | Estado |
|---|---|---|
| C1 | **Panel nuevo** (2 columnas, pestañas, modo compacto/amplio, toasts) | hecho |
| C2 | **Tamaño del video ajustable** S/M/L/XL + pantalla completa, recordado en el navegador | hecho |
| C3 | **Editor visual de zonas** sobre una captura congelada (`/snapshot.jpg` + canvas, `GET/PUT /zonas`) | hecho |
| C4 | **Timeline del día** con marcas por tipo y detalle al hacer clic | hecho |
| C5 | **Detalle de evento**: miniatura grande + reproductor del clip | hecho |

## Sprint B — "que me entere" (pendiente, siguiente)

| # | Mejora | Por qué | Esfuerzo |
|---|---|---|---|
| B1 | **Telegram** (long polling, sin abrir puertos) con clip o frame adjunto | El toast solo vale si estás en el PC | M |
| B2 | **Cooldown y reglas de aviso** (tipo + franja + máximo por hora) | Sin esto, Telegram se vuelve insufrible y lo desactivas | S |
| B3 | **Modo armado / desarmado** (botón + automático por franja) | "Vigila cuando no estoy" | S-M |
| B4 | **Grabación con post-buffer (±10 s)** | Un clip solo con lo anterior explica mal lo que pasó | M |

## Sprint D — "que aguante 24/7" (pendiente)

| # | Mejora | Por qué | Esfuerzo |
|---|---|---|---|
| D1 | **Arranque automático + reinicio** (tarea programada o NSSM) | Hoy si cae, no vuelve | S |
| D2 | **Aviso si la cámara lleva caída > 5 min o el disco < 5 GB** | Los fallos silenciosos son el peor enemigo | S |
| D3 | **SSE en vez de polling** para el feed | Menos carga y latencia real | S |
| D4 | **Auth por token** si se expone fuera de `localhost` | Hoy no hay ninguna | S |

## Sprint E — inteligencia (después)

| # | Mejora | Nota |
|---|---|---|
| E1 | **Caídas con pose** (`persona_caida`) | El clasificador de actividades ya tiene los keypoints |
| E2 | **Re-ID de conocidos sin caras** (embedding de apariencia + botón "esta soy yo") | Evita reconocimiento facial de identidad |
| E3 | **Audio** (YAMNet): ladridos, cristales, gritos | Cubre lo que la cámara no ve |
| E4 | **Reglas de zona ricas**: dirección de cruce, zona restringida fuera de horario, "más de N personas" | Hoy: 1 caja = 1 zona, solo solapamiento |
| E5 | **Objetos vigilados por zona** | Hoy la desaparición es global por clase |

## Fuera de alcance (mantener)

- Reconocimiento facial de identidad en abierto.
- Cloud y app móvil nativa (una vista responsive en la LAN cubre el 90 %).
- Entrenamiento de modelos propios.

## Deuda técnica conocida

- `web.py` concentra demasiado (bucle + rutas + HTML). Candidato a partir en
  `nucleo.py` (bucle), `panel.py` (HTML) y `api.py` (rutas).
- El HTML del panel vive en `vigia/static/index.html`; ya no se edita dentro
  de una cadena de Python.
- `pose.py` + `actividades.py` añaden una segunda pasada de inferencia: medir
  FPS con `actividades: true` antes de dejarlo por defecto.
