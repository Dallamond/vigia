"""Tests del almacen SQLite (fichero temporal en memoria de pytest tmp_path)."""
from vigia.almacen import Almacen


def test_guardar_y_historial(tmp_path):
    a = Almacen(tmp_path / "test.db")
    a.guardar("persona_entra", id_tracking=1)
    a.guardar("objeto_desaparece", detalle="laptop")
    hist = a.historial()
    assert len(hist) == 2
    assert hist[0]["tipo"] == "objeto_desaparece"  # mas reciente primero
    assert hist[1]["id_tracking"] == 1
    a.cerrar()


def test_filtro_por_tipo(tmp_path):
    a = Almacen(tmp_path / "t.db")
    a.guardar("persona_entra", 1)
    a.guardar("objeto_aparece")
    assert len(a.historial(tipo="persona_entra")) == 1
    assert a.historial(tipo="persona_entra")[0]["id_tracking"] == 1
    a.cerrar()


def test_limpiar(tmp_path):
    a = Almacen(tmp_path / "t.db")
    a.guardar("persona_entra", 1)
    a.limpiar()
    assert a.historial() == []
    a.cerrar()


def test_persiste_entre_conexiones(tmp_path):
    db = tmp_path / "p.db"
    a = Almacen(db)
    a.guardar("persona_entra", 5)
    a.cerrar()
    b = Almacen(db)
    assert len(b.historial()) == 1
    b.cerrar()
