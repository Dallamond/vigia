"""Banco de evaluacion offline: pasa un video por el MISMO pipeline que el
bucle real y vuelca una linea de tiempo JSON.

Pipeline (igual que `vigia.web.bucle_procesamiento`):

    detector + ByteTrack -> reasociador de IDs -> rastreador entra/sale
    -> pose -> asociacion pose<->persona -> clasificador -> estabilizador

El reloj es simulado: `t` sale de los timestamps del video (frame / fps), no
del reloj del sistema. Asi dos pasadas del mismo clip dan lo mismo y los
tiempos son comparables entre versiones del codigo.

Uso:

    .venv\\Scripts\\python -m eval.reproducir clips/xxx.webm --salida eval/salida/x.json
    .venv\\Scripts\\python -m eval.reproducir clips/xxx.webm --sin-pose

No abre la webcam ni usa red (los modelos .pt se cargan de la carpeta del
proyecto; si faltan, ultralytics intentaria descargarlos).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from vigia.actividades import EstabilizadorAct, clasificar  # noqa: E402
from vigia.analisis import persona_de_pose  # noqa: E402
from vigia.config import cargar_config  # noqa: E402
from vigia.detector import Detector  # noqa: E402
from vigia.pose import PoseDetector  # noqa: E402
from vigia.tracking import RastreadorPersonas, ReasociadorIds  # noqa: E402


class RelojVideo:
    """Reloj monotono simulado a partir del tiempo del video."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def poner(self, t: float) -> None:
        self.t = max(self.t, float(t))


def _redondear_kpts(pose) -> list[list[float]]:
    if pose is None:
        return []
    puntos = []
    for (x, y), c in zip(pose.kpts, pose.conf):
        puntos.append([round(float(x), 1), round(float(y), 1),
                       round(float(c), 3)])
    return puntos


def _fabricas(cfg: dict, sin_pose: bool):
    modelo_pose = cfg.get("pose_modelo", "n")
    if isinstance(modelo_pose, int) or str(modelo_pose) in {"n", "s", "m"}:
        archivo = f"yolo11{modelo_pose}-pose.pt"
    else:
        archivo = str(modelo_pose)
    ruta = Path(archivo)
    if not ruta.is_absolute() and not ruta.exists():
        candidato = RAIZ / archivo
        if candidato.exists():
            archivo = str(candidato)

    def detector() -> Detector:
        return Detector(conf=float(cfg.get("conf_deteccion", 0.45)))

    def pose() -> PoseDetector | None:
        if sin_pose:
            return None
        try:
            return PoseDetector(modelo=archivo,
                                imgsz=int(cfg.get("pose_imgsz", 640)),
                                conf_kpt=float(cfg.get("conf_kpt", 0.3)))
        except TypeError:  # version antigua de pose.py (linea base)
            return PoseDetector(modelo=archivo)

    return detector, pose


def _actualizar(rastreador, ids, info, ancho, alto):
    """Llama al rastreador con la API nueva o la antigua (linea base)."""
    try:
        return rastreador.actualizar(ids, info, ancho=ancho, alto=alto)
    except TypeError:
        return rastreador.actualizar(ids)


def procesar_video(video: str | Path, config: dict | None = None,
                   detector=None, pose=None, sin_pose: bool = False,
                   max_frames: int | None = None) -> dict:
    """Devuelve la linea de tiempo serializable del video."""
    cfg = config or cargar_config()
    reloj = RelojVideo()
    # las versiones antiguas del rastreador leen time.monotonic directamente;
    # se parchea para que la linea base tambien use el reloj del video
    try:
        import time as _time
        _time.monotonic = reloj
    except Exception:
        pass
    if detector is None or (pose is None and not sin_pose):
        fdet, fpose = _fabricas(cfg, sin_pose)
        detector = detector or fdet()
        pose = pose if pose is not None else fpose()
    elif pose is None:
        pose = None

    rastreador = None
    try:
        rastreador = RastreadorPersonas(
            timeout_seg=float(cfg.get("timeout_persona_seg", 10)),
            confirmar_seg=float(cfg.get("confirmar_entrada_seg", 1.0) or 0),
            reloj=reloj,
            timeout_borde_seg=cfg.get("timeout_borde_seg"),
            timeout_centro_seg=cfg.get("timeout_centro_seg"),
            confirmar_interior_seg=cfg.get("confirmar_interior_seg"),
            conf_minima=float(cfg.get("entrada_conf_min", 0.0) or 0.0),
            alto_min_rel=float(cfg.get("entrada_alto_min", 0.0) or 0.0))
    except TypeError:  # version antigua (linea base): solo reloj + tiempos
        try:
            rastreador = RastreadorPersonas(
                timeout_seg=float(cfg.get("timeout_persona_seg", 10)),
                confirmar_seg=float(cfg.get("confirmar_entrada_seg", 1.0) or 0),
                reloj=reloj)
        except TypeError:
            rastreador = RastreadorPersonas(
                timeout_seg=float(cfg.get("timeout_persona_seg", 10)),
                confirmar_seg=float(cfg.get("confirmar_entrada_seg", 1.0) or 0))
    try:
        reasociador = ReasociadorIds(
            ventana_seg=float(cfg.get("reasociar_seg", 8) or 8), reloj=reloj,
            reid=bool(cfg.get("reid", True)),
            sim_min=float(cfg.get("reid_sim_min", 0.55)))
    except TypeError:
        reasociador = ReasociadorIds(
            ventana_seg=float(cfg.get("reasociar_seg", 8) or 8), reloj=reloj)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"no se puede abrir el video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    if not (1.0 <= fps <= 240.0):
        fps = 10.0
    ancho = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    alto = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    frames: list[dict] = []
    eventos: list[dict] = []
    estabilizadores: dict[int, EstabilizadorAct] = {}
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames is not None and i >= max_frames:
            break
        t = i / fps
        reloj.poner(t)
        dets, ids_idx = detector.detectar_track(frame)
        ids_idx = reasociador.mapear(ids_idx, dets)
        mapa_ids = {ids_idx[k]: d.caja for k, d in enumerate(dets)
                    if d.es_persona and k in ids_idx}
        info = {ids_idx[k]: {"caja": d.caja, "conf": d.conf}
                for k, d in enumerate(dets)
                if d.es_persona and k in ids_idx}
        for tipo, pid in _actualizar(rastreador, set(mapa_ids), info,
                                     ancho, alto):
            eventos.append({"t": round(t, 3), "tipo": tipo, "id": pid})

        if pose is not None and hasattr(pose, "poner_tiempo"):
            pose.poner_tiempo(t)
        if pose is not None:
            try:
                poses = pose.detectar(frame, mapa_ids)
            except TypeError:
                poses = pose.detectar(frame)
        else:
            poses = []
        por_persona = []
        for p in poses:
            pid = getattr(p, "track_id", None)
            if pid is None or pid < 0:
                pid = persona_de_pose(p.caja, list(mapa_ids.items()))
            cruda = "desconocida"
            estable = "desconocida"
            if pid is not None:
                try:
                    cruda = clasificar(p, [d for d in dets
                                           if not d.es_persona],
                                       {"distinguir_tumbado": True})
                except TypeError:
                    cruda = clasificar(p, [d for d in dets
                                           if not d.es_persona])
                est = estabilizadores.setdefault(pid, EstabilizadorAct())
                anuncio = est.tick(cruda, t)
                if anuncio:
                    eventos.append({"t": round(t, 3), "tipo": "actividad",
                                    "id": pid, "detalle": anuncio})
                estable = est.actual
            por_persona.append({
                "id": pid, "caja": list(p.caja),
                "actividad_cruda": cruda, "actividad_estable": estable,
                "kpts": _redondear_kpts(p)})
        frames.append({
            "t": round(t, 3),
            "personas": por_persona,
            "ids": sorted(mapa_ids),
            "objetos": [{"clase": d.clase, "conf": round(d.conf, 3),
                         "caja": list(d.caja)} for d in dets
                        if not d.es_persona],
        })
        i += 1
    cap.release()
    return {
        "video": str(video),
        "fps": fps,
        "ancho": ancho,
        "alto": alto,
        "frames": frames,
        "eventos": eventos,
        "config": {k: cfg.get(k) for k in (
            "pose_modelo", "pose_imgsz", "conf_kpt", "confirmar_entrada_seg",
            "confirmar_interior_seg", "timeout_persona_seg",
            "timeout_borde_seg", "timeout_centro_seg", "reasociar_seg",
            "reid", "reid_sim_min", "entrada_conf_min", "entrada_alto_min")},
        "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", help="clip .webm/.mp4/.avi")
    ap.add_argument("--salida", "-s", help="JSON de salida "
                    "(por defecto eval/salida/<nombre>.json)")
    ap.add_argument("--config", help="config.yaml alternativo")
    ap.add_argument("--sin-pose", action="store_true",
                    help="solo deteccion/tracking (mucho mas rapido)")
    ap.add_argument("--max-frames", type=int, default=None)
    args = ap.parse_args(argv)

    cfg = cargar_config(args.config)
    video = Path(args.video)
    salida = Path(args.salida) if args.salida else (
        RAIZ / "eval" / "salida" / (video.stem + ".json"))
    salida.parent.mkdir(parents=True, exist_ok=True)
    datos = procesar_video(video, cfg, sin_pose=args.sin_pose,
                           max_frames=args.max_frames)
    salida.write_text(json.dumps(datos, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    n_ev = len(datos["eventos"])
    personas = {p["id"] for f in datos["frames"] for p in f["personas"]}
    print(f"{video.name}: {len(datos['frames'])} frames, "
          f"{n_ev} eventos, ids {sorted(x for x in personas if x is not None)}"
          f" -> {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
