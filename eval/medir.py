"""Mide la linea de tiempo de `eval/reproducir.py` contra etiquetas manuales.

Metricas por clip:

- actividad: exactitud de la actividad estable y precision/recall por clase
  (se muestrea cada 0,25 s dentro de los tramos etiquetados)
- `persona_entra` / `persona_salio`: aciertos, falsos y perdidos con una
  tolerancia temporal (`--tol`, por defecto 2,5 s)
- cambios de ID: transiciones del ID dominante dentro de los tramos

Uso:

    .venv\\Scripts\\python -m eval.medir
    .venv\\Scripts\\python -m eval.medir --dir eval/salida --tol 3

Imprime una tabla y guarda `eval/salida/resumen.json` (y un JSON por clip).
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EVAL = RAIZ / "eval"


def _cargar_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _frame_cerca(frames: list[dict], t: float) -> dict | None:
    if not frames:
        return None
    # los frames estan ordenados por t: busqueda lineal suficiente (7 s)
    return min(frames, key=lambda f: abs(f["t"] - t))


def _persona_principal(frame: dict) -> dict | None:
    personas = frame.get("personas") or []
    if not personas:
        return None
    # la persona mas grande (la etiqueta describe a la del primer plano)
    return max(personas, key=lambda p: max(
        (p["caja"][2] - p["caja"][0]) * (p["caja"][3] - p["caja"][1]), 0))


def _muestrear(timeline: dict, desde: float, hasta: float,
               paso: float = 0.25):
    t = desde
    while t <= hasta + 1e-9:
        frame = _frame_cerca(timeline["frames"], t)
        if frame is not None:
            yield t, frame
        t += paso


def medir_clip(timeline: dict, etiquetas: dict, tol: float = 2.5) -> dict:
    frames = timeline.get("frames") or []
    tramos = [x for x in etiquetas.get("tramos", [])
              if x.get("actividad")]
    correctos = 0
    total = 0
    confusion: dict[str, Counter] = defaultdict(Counter)
    ids_por_tramo: list[list[int]] = []
    for tramo in tramos:
        esperado = tramo["actividad"]
        ids_tramo: list[int] = []
        for _t, frame in _muestrear(timeline, float(tramo["desde"]),
                                   float(tramo["hasta"])):
            persona = _persona_principal(frame)
            if persona is None:
                total += 1
                confusion[esperado]["(sin persona)"] += 1
                continue
            obtenido = persona.get("actividad_estable") or "desconocida"
            total += 1
            confusion[esperado][obtenido] += 1
            if obtenido == esperado:
                correctos += 1
            pid = persona.get("id")
            if pid is not None:
                ids_tramo.append(pid)
        ids_por_tramo.append(ids_tramo)

    # eventos: emparejamiento uno-a-uno por tipo y cercania temporal
    etiquetados = etiquetas.get("eventos", [])
    emitidos = [e for e in timeline.get("eventos", [])
                if e.get("tipo") in ("persona_entra", "persona_salio")]
    usados: set[int] = set()
    eventos = {}
    for tipo in ("persona_entra", "persona_salio"):
        esperados = [e for e in etiquetados if e.get("evento") == tipo]
        candidatos = [i for i, e in enumerate(emitidos)
                      if e.get("tipo") == tipo]
        aciertos = 0
        for ev in esperados:
            mejor, mejor_d = None, tol
            for i in candidatos:
                if i in usados:
                    continue
                d = abs(float(emitidos[i]["t"]) - float(ev["t"]))
                if d <= mejor_d:
                    mejor, mejor_d = i, d
            if mejor is not None:
                usados.add(mejor)
                aciertos += 1
        detectados = len(candidatos)
        eventos[tipo] = {
            "etiquetados": len(esperados),
            "detectados": detectados,
            "aciertos": aciertos,
            "falsos": detectados - aciertos,
            "perdidos": len(esperados) - aciertos,
        }
    # eventos emitidos sin etiqueta (aunque no hubiera ninguna etiqueta del
    # mismo tipo, cuentan como falsos)
    for tipo in ("persona_entra", "persona_salio"):
        if not any(e.get("evento") == tipo for e in etiquetados):
            eventos.setdefault(tipo, {"etiquetados": 0, "detectados": 0,
                                      "aciertos": 0, "falsos": 0,
                                      "perdidos": 0})
            eventos[tipo]["detectados"] = sum(
                1 for e in emitidos if e.get("tipo") == tipo)
            eventos[tipo]["falsos"] = eventos[tipo]["detectados"]

    # cambios de ID: solo si hay una persona por frame en el tramo
    cambios = 0
    multi = False
    for tramo in tramos:
        secuencia = []
        for _t, frame in _muestrear(timeline, float(tramo["desde"]),
                                   float(tramo["hasta"])):
            personas = frame.get("personas") or []
            if len(personas) > 1:
                multi = True
            p = _persona_principal(frame)
            if p and p.get("id") is not None:
                secuencia.append(p["id"])
        cambios += sum(1 for a, b in zip(secuencia, secuencia[1:]) if a != b)

    return {
        "video": timeline.get("video") or etiquetas.get("video"),
        "actividad_total": total,
        "actividad_aciertos": correctos,
        "actividad_exactitud": round(correctos / total, 3) if total else None,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "eventos": eventos,
        "cambios_id": cambios,
        "multi_persona": multi,
    }


def _tabla(resultados: list[dict]) -> str:
    lineas = []
    cab = (f"{'clip':38} {'act.':>7} {'entra F/P':>10} "
           f"{'salio F/P':>10} {'IDs':>4}")
    lineas.append(cab)
    lineas.append("-" * len(cab))
    for r in resultados:
        nombre = Path(str(r["video"])).stem[-38:]
        acc = (f"{r['actividad_exactitud']:.0%}"
               if r["actividad_exactitud"] is not None else "-")
        ent = r["eventos"].get("persona_entra", {})
        sal = r["eventos"].get("persona_salio", {})
        lineas.append(
            f"{nombre:38} {acc:>7} "
            f"{ent.get('falsos', 0)}/{ent.get('perdidos', 0):<8} "
            f"{sal.get('falsos', 0)}/{sal.get('perdidos', 0):<8} "
            f"{r['cambios_id']:>4}")
    # agregado
    total = sum(r["actividad_total"] for r in resultados)
    aciertos = sum(r["actividad_aciertos"] for r in resultados)
    falsos_e = sum(r["eventos"].get("persona_entra", {}).get("falsos", 0)
                   for r in resultados)
    perdidos_e = sum(r["eventos"].get("persona_entra", {}).get("perdidos", 0)
                     for r in resultados)
    falsos_s = sum(r["eventos"].get("persona_salio", {}).get("falsos", 0)
                   for r in resultados)
    perdidos_s = sum(r["eventos"].get("persona_salio", {}).get("perdidos", 0)
                     for r in resultados)
    ids = sum(r["cambios_id"] for r in resultados)
    lineas.append("-" * len(cab))
    lineas.append(f"{'TOTAL':38} "
                  f"{(aciertos / total if total else 0):>7.0%} "
                  f"{falsos_e}/{perdidos_e:<8} {falsos_s}/{perdidos_s:<8} "
                  f"{ids:>4}")
    # confusion agregada
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in resultados:
        for esperado, cuentas in r["confusion"].items():
            confusion[esperado].update(cuentas)
    if confusion:
        lineas.append("")
        lineas.append("Confusion (esperado -> obtenido):")
        for esperado in sorted(confusion):
            cuentas = ", ".join(f"{k}={v}" for k, v in
                                confusion[esperado].most_common())
            lineas.append(f"  {esperado:14} {cuentas}")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lineas", nargs="*", help="JSON de eval/reproducir")
    ap.add_argument("--etiquetas", default=str(EVAL / "etiquetas"))
    ap.add_argument("--dir", default=str(EVAL / "salida"))
    ap.add_argument("--tol", type=float, default=2.5)
    args = ap.parse_args(argv)

    carpeta_et = Path(args.etiquetas)
    carpeta_sal = Path(args.dir)
    if args.lineas:
        rutas = [Path(p) for p in args.lineas]
    else:
        rutas = sorted(carpeta_sal.glob("*.json"))
        rutas = [p for p in rutas if p.name != "resumen.json"]
    resultados = []
    for ruta in rutas:
        etiqueta = carpeta_et / ruta.name
        if not etiqueta.exists():
            continue
        timeline = _cargar_json(ruta)
        labels = _cargar_json(etiqueta)
        r = medir_clip(timeline, labels, tol=args.tol)
        r["linea_tiempo"] = str(ruta)
        resultados.append(r)
        (carpeta_sal / f"medido_{ruta.stem}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    if not resultados:
        print("No hay lineas de tiempo con etiqueta. Ejecuta antes "
              "eval.reproducir sobre los clips etiquetados.")
        return 1
    print(_tabla(resultados))
    resumen = {
        "clips": resultados,
        "total": {
            "actividad_exactitud": (
                round(sum(r["actividad_aciertos"] for r in resultados)
                      / max(1, sum(r["actividad_total"] for r in resultados)),
                      3)),
            "persona_entra_falsos": sum(
                r["eventos"].get("persona_entra", {}).get("falsos", 0)
                for r in resultados),
            "persona_entra_perdidos": sum(
                r["eventos"].get("persona_entra", {}).get("perdidos", 0)
                for r in resultados),
            "persona_salio_falsos": sum(
                r["eventos"].get("persona_salio", {}).get("falsos", 0)
                for r in resultados),
            "persona_salio_perdidos": sum(
                r["eventos"].get("persona_salio", {}).get("perdidos", 0)
                for r in resultados),
            "cambios_id": sum(r["cambios_id"] for r in resultados),
        },
    }
    (carpeta_sal / "resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
