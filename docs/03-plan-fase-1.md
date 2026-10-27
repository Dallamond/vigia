# Vigia — Plan de Implementación Fase 1

> fecha_creacion: 2026-10-27
> estado: listo_para_ejecutar
> area: proyecto
> agente: Alfred
> tags:
>   - tipo/plan
>   - area/proyecto-tec
> relacionado:
>   - "[[02-roadmap]]"
>   - "[[04-estado]]"

Fase 1: **Base — captura + detección en vivo**. Objetivo: webcam +
YOLO corriendo con cajas y FPS en pantalla, GPU activa.

## Paso 0 — Estructura y entorno

- [ ] Crear estructura de carpetas:

```
vigia/
  vigia/
    __init__.py
    captura.py        (Fase 1)
    detector.py       (Fase 1)
    main.py           (Fase 1)
  tests/
    __init__.py
  docs/               (ya creada)
  config.yaml         (Fase 3)
  requirements.txt
  README.md
  .gitignore
```

- [ ] `requirements.txt`:

```
opencv-python
ultralytics
pytest
```

- [ ] Crear entorno virtual: `python -m venv .venv` + activar.
- [ ] Instalar torch con CUDA (verificando la versión de CUDA del PC)
      e instalar el resto: `pip install -r requirements.txt`.

## Paso 1 — Verificar GPU + modelo

- [ ] Script corto que imprima `torch.cuda.is_available()` y el nombre
      de la GPU.
- [ ] Cargar YOLO11n y confirmar que descarga/encuentra el peso
      `yolo11n.pt`.

### Criterio de cierre

`.cuda.is_available() == True` y el modelo se carga sin error.

## Paso 2 — Módulo captura

- [ ] `captura.py`:
      - Clase `Camara(abrir=True)` con ` leer() -> frame | None`.
      - Manejo de cámara no disponible (excepción clara, no crash).
      - `liberar()` limpio.
- [ ] Test: mock de cámara (no requiere webcam real) para la lógica de
      apertura/salida.

### Criterio de cierre

`pytest` verde con la webcam desenchufada (mocks).

## Paso 3 — Módulo detector

- [ ] `detector.py`:
      - Clase `Detector(modelo='yolo11n', conf=0.4)` con GPU `device`.
      - `detectar(frame) -> lista[Deteccion(id_clase_clase_nombre, conf, caja)]`.
      - Filtrado por clases objetivo (person + objetos seleccionables).

### Criterio de cierre

Un frame de prueba pintando (imagen con una persona) devuelve ≥1
detección de persona en test.

## Paso 4 — Punto de Fusión en vivo

- [ ] `main.py`:
      - Bucle: leer frame → detectar → dibujar cajas/etiquetas/FPS.
      - Tecla `q` cierra; Ctrl+C cierra Programando.
      - Mostrar FPS promedio en la esquina.
- [ ] Arranque: `python -m vigia.main`.

### Criterio de cierre (manual real)

Vea a usted mismo entonces: en vivo, dándose FPS ≥ 15 con GPU,
etiquetas correctas, y cierre limpio con `q`.

## Paso 5 — Cierre de fase

- [ ] `README.md` con instrucciones de arranque.
- [ ] Lanzar `pytest` completo y dejar verde.
- [ ] Registrar en `04-estado.md` + `log.md` del vault que la Fase 1
      quedó `hecha`, con FPS medidos como dato.

## Riesgos conocidos

| Riesgo | Mitigación |
|---|---|
| CUDA/torch malicen instalado en Windows | Instalar torch con índice CUDA explícito; fallback a CPU para avanzar mientras |
| Webcam lenta (HD 30fps + detección) | Reducir resolución a 640, o inferir cada 2 frames |
| Primer arranque descarga el peso | Mensaje claro + progreso |

## Nota TDD

Los módulos con tests (captura, detector, motor de eventos) se
desarrollan con el test escrito antes. La verificación de webcam es
siempre manual al final de cada paso.
