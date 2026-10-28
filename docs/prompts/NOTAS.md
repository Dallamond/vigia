# Notas entre terminales

## Cambios de contrato (A)

### Seguridad / auth (bloque 1)

- `POST /auth/login` body `{"token": "..."}`:
  - `200 {"ok": true, "auth_requerida": true}` y cookie `vigia_token`
    (HttpOnly, SameSite=Lax, 30 días, path `/`).
  - `401 {"detail": ...}` si el token no coincide.
  - `429` si esa IP supera 5 fallos en 60 s.
  - Si no hay `VIGIA_TOKEN`, devuelve `200` sin cookie (`auth_requerida:false`).
- `POST /auth/logout` → `200 {"ok": true}` y borra la cookie.
- `GET /auth/estado` → siempre accesible:
  `{"auth_requerida": bool, "autenticado": bool}`.
- Cualquier otro endpoint (API, vídeo, `/media`, `/clip`, `/static` no) sin
  credencial → `401` JSON **`{"detalle": "no autenticado"}`** (ojo: `detalle`,
  no `detail`).
- Credenciales aceptadas:
  - `Authorization: Bearer <token>`
  - cookie `vigia_token`
  - `?token=<token>` **solo** en `/video.mjpg` y `/snapshot.jpg`.
- Público sin token: `/`, `/index.html`, `/favicon.ico`, todo `/static/*` y
  todo `/auth/*`. El resto queda cerrado.
- Sin `VIGIA_TOKEN` solo se atiende a loopback (`127.0.0.1`, `::1`,
  `localhost`); desde otra IP → `403`. Si `host` no es loopback y no hay
  token, el proceso **no arranca** (mensaje claro en consola/log).
- `host` y `puerto` ya se leen de `config.yaml`; `VIGIA_HOST`/`VIGIA_PUERTO`
  siguen ganando. Por defecto `127.0.0.1:8000`.
- Cabeceras en todas las respuestas: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy` sin cámara/micro/geo.

### Zonas (bloque 4) — **lo consume la GUI**

`GET /zonas` → `{"zonas": {nombre: ZONA}, "ancho": int, "alto": int}`

`ZONA` siempre lleva estas claves:

```json
{
  "caja": [x1, y1, x2, y2],          // o "poligono": [[x,y], ...], nunca ambos
  "permanencia_seg": 20,            // null = umbral global
  "permanencia_conocidos_seg": null,// null = usa el global; 0 = no dispara
  "franjas": [{"desde": "22:00", "hasta": "08:00"}],
  "restringida": false,
  "excluir": false,
  "max_personas": 0,                // 0 = sin límite
  "vigilar": ["laptop"],
  "conocidos": true
}
```

- `PUT /zonas` acepta exactamente ese objeto (sin `nombre` dentro) **y** el
  formato antiguo `{"zonas": {"puerta": [x1,y1,x2,y2]}}`.
- Un `PUT` inválido → `400` con texto en `detail`.
- Se persiste en `config.yaml` también en el formato nuevo; `config.yaml`
  antiguo con listas sigue cargando.
- Reglas: `restringida` avisa cuando hay alguien y **no** está en sus
  `franjas`; si no tiene `franjas`, avisa siempre. `excluir` ignora esa área
  para todo. Los polígonos usan el **centro** de la caja de la persona.
- Eventos nuevos: `zona_restringida`, `demasiadas_personas`, `merodeo`,
  `persona_inmovil`.

### Telegram / armado (bloque 2)

- `GET /telegram/estado` →
  `{"configurado": bool, "activo": bool, "token_configurado": bool,
    "chat_configurado": bool, "ultimo_error": str|null,
    "ultimo_envio": "AAAA-MM-DD HH:MM:SS"|null, "pendientes": int}`.
  Nunca incluye el token ni el chat_id.
- `POST /telegram/prueba` → `{"ok": true}` o `{"ok": false, "error": "..."}`;
  `409` si Telegram no está configurado.
- `GET /armado` → `{"armado": bool, "auto": bool, "desde": str|null,
  "franjas": [{"desde","hasta"}]}`.
- `POST /armado` body `{"armado": bool}` y opcional `{"auto": bool}`.
  Un cambio manual desactiva el automático (salvo que mandes `auto` en el
  mismo POST). Devuelve el mismo objeto que `GET /armado`.
- Prioridades por defecto: `cara_desconocida` → crítico si armado / aviso si
  no; `permanencia` y `objeto_desaparece` → aviso; `persona_entra/salio` →
  info (no se envían). Configurable en `telegram_prioridades`.

## Peticiones de B a A

## Resumen final A

### Qué se hizo

- **A1 (seguridad)**: `vigia/seguridad.py` (`.env` sin dependencias, token,
  intentos por IP, loopback, cabeceras), middleware en `vigia/web.py`,
  endpoints `/auth/*`, `host`/`puerto` en `config.py`, validación estricta de
  `fichero` en `/clip` y `/media` (traversal, ocultos, `~`, separadores),
  `VIGIA_TOKEN` también con `Authorization`/cookie/query. 14 tests.
- **A2 (Telegram)**: `vigia/telegram.py` con cliente `urllib` (long polling,
  multipart para foto/vídeo), cola y dos hilos (envíos y bot), anti-spam con
  cooldown y resumen, prioridades, `vigia/armado.py` persistente en SQLite,
  comandos `/armar /desarmar /estado /foto`, armado automático por franjas,
  avisos de cámara/disco/reinicio y endpoints para la GUI. Tests con cliente
  falso (sin red).
- **A3 (zonas)**: `vigia/zonas.py` (modelo, polígonos, franjas, validación),
  motor de eventos reescrito para umbral por zona, re-aviso, conocidos,
  restringidas, aforo, merodeo, inmovilidad, exclusión y objetos por zona.
  `GET/PUT /zonas` en formato objeto retrocompatible.

### Qué quedó a medias / pendiente

- **Telegram no se ha probado contra la API real** (no hay token en `.env`);
  toda la lógica está testeada con un cliente falso. Falta una prueba manual
  con un bot real.
- **WebM en móviles**: queda el `TODO` documentado en `vigia/telegram.py`.
  No se monta ffmpeg ni conversión a MP4 H.264.
- **Polígonos en el vídeo**: se guardan y se evalúan, pero `dibujo.py` solo
  pinta cajas (no es fichero mío); la GUI puede pintarlos en el editor. Si se
  quieren ver en el stream, pedírselo a A o ampliar `dibujo.py` en otra ronda.
- Las reglas por zona se evalúan solo cuando la **franja global** está activa
  (comportamiento previo del motor). Si en el futuro se quieren zonas activas
  fuera de la franja global, hay que cambiar `procesar` para no salir antes.
- Una persona que cae en dos zonas se asigna a la **primera** del mapa (orden
  de `config.yaml`/PUT); no se generan eventos duplicados por múltiples zonas.
- `/zonas` no valida el rango de coordenadas contra la resolución (solo que
  sean 4 números o un polígono). La GUI ya limita al dibujar.

### Decisiones raras (a propósito)

- `detalle` (español) en el 401, tal y como pidió el contrato de B.
- Sin token, las peticiones remotas reciben **403** (no 401) porque no hay
  credencial que presentar; el 401 es solo cuando hay token configurado.
- `reaviso_min` por defecto **0** (comportamiento antiguo: un solo aviso).
- `inmovil_seg` por defecto **0**: activarlo de serie generaría falsos
  positivos con personas sentadas.
- Los conocidos **no** disparan permanencia por defecto
  (`permanencia_conocidos_seg: 0`); se puede dar un umbral distinto o dejar
  que la zona los ignore con `conocidos: false`.
- Un POST manual a `/armado` desactiva el modo automático para que la
  siguiente franja no revierta la decisión del usuario.
- El estado de armado se guarda en la tabla `estado_sistema` de `vigia.db`
  (no en `config.yaml`, que es de ajustes).
- `?token=` se acepta en `/snapshot.jpg` además de `/video.mjpg` porque el
  editor de zonas carga la imagen con `<img>` y no puede mandar cabeceras.
- La cookie no lleva `Secure` para no romper HTTP local; delante de HTTPS
  (Caddy) conviene añadirlo o dejar que el proxy lo haga.

## Resumen final B
