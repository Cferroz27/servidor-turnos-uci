"""Crea las tablas del proceso quirúrgico y carga los datos ficticios (relativos a la fecha de carga)."""
import json
from pathlib import Path

from psycopg.rows import dict_row

from . import datos as D

AQUI = Path(__file__).resolve().parent
TABLAS = ["parametros", "salas", "equipos_biomedicos", "cirujanos", "aseguradoras", "procedimientos", "novedades_recursos",
          "casos", "autorizaciones", "programaciones", "programacion_casos", "preparacion", "insumos_caso", "confirmaciones",
          "ingreso", "lista_chequeo", "tiempos", "cancelaciones", "notas", "facturas", "glosas", "auditoria", "alertas_seguridad", "salidas"]
SERIALES = {"novedades_recursos": "id", "autorizaciones": "id", "programaciones": "id", "insumos_caso": "id",
            "cancelaciones": "id", "notas": "id", "glosas": "id", "auditoria": "id", "alertas_seguridad": "id", "salidas": "id"}


def crear_tablas(conn):
    with conn.cursor() as cur:
        cur.execute((AQUI / "esquema.sql").read_text(encoding="utf-8"))


def casos_cargados(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('casos') IS NOT NULL")
        if not cur.fetchone()[0]:
            return 0
        cur.execute("SELECT count(*) FROM casos")
        return cur.fetchone()[0]


def _insertar(cur, tabla, filas):
    if not filas:
        return
    cols = list(filas[0].keys())
    sql = f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    cur.executemany(sql, [tuple(f[c] for c in cols) for f in filas])


def cargar(conn, hoy, reiniciar=False, informar=print, vacia=False):
    """vacia=True: solo tablas y catálogos (salas, equipos, cirujanos, aseguradoras, procedimientos, parámetros), sin casos."""
    existentes = casos_cargados(conn)
    if existentes and not reiniciar:
        raise RuntimeError(f"La base quirúrgica ya tiene {existentes} casos. Para borrar todo y volver a cargar use reiniciar=true.")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('salas') IS NOT NULL")
        if cur.fetchone()[0] and not reiniciar:
            raise RuntimeError("La base del equipo ya existe (sin casos). Para crearla de nuevo use reiniciar=true.")
    with conn.cursor() as cur:
        if reiniciar:
            cur.execute("DROP VIEW IF EXISTS v_autorizacion_vigente")
            for t in reversed(TABLAS):
                cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
            informar("Tablas anteriores eliminadas.")
    crear_tablas(conn)
    T, semana = D.generar(hoy)
    with conn.cursor() as cur:
        _insertar(cur, "parametros", [dict(clave=a, valor=b, descripcion=c) for a, b, c in D.PARAMETROS])
        _insertar(cur, "salas", [dict(id_sala=a, descripcion=b, solo_p1=c, hora_inicio=d, hora_fin=e) for a, b, c, d, e in D.SALAS])
        _insertar(cur, "equipos_biomedicos", [dict(id_equipo=a, tipo=b, descripcion=c, id_sala=d,
                                                   mantenimiento_vence=(semana + __import__("datetime").timedelta(days=e - 7))) for a, b, c, d, e in D.EQUIPOS])
        _insertar(cur, "cirujanos", [dict(codigo=a, especialidad=b, dias=c, hora_fin=d) for a, b, c, d in D.CIRUJANOS])
        _insertar(cur, "aseguradoras", [dict(id_aseguradora=a, nombre=b, tipo=c) for a, b, c in D.ASEGURADORAS])
        _insertar(cur, "procedimientos", [dict(codigo=p[0], nombre=p[1], especialidad=p[2], tipo_cirugia=p[3], duracion_min=p[4],
                                               equipo_requerido=p[5], set_instrumental=p[6], requiere_implante=p[7], requiere_sangre=p[8],
                                               costo_estimado=p[9], tarifa=p[10]) for p in D.PROCEDIMIENTOS])
        for t in ([] if vacia else ["novedades_recursos", "casos", "autorizaciones", "programaciones", "programacion_casos", "preparacion", "insumos_caso",
                  "confirmaciones", "ingreso", "lista_chequeo", "tiempos", "cancelaciones", "facturas", "glosas"]):
            filas = T[t]
            if t == "novedades_recursos":
                filas = [dict(f) for f in filas]
            _insertar(cur, t, filas)
        if not vacia:
            _insertar(cur, "notas", T["notas"])
        for t, col in SERIALES.items():
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}','{col}'), COALESCE(MAX({col}),0)+1, false) FROM {t}")
        # El caso con instrucciones incrustadas queda marcado desde la carga
        cur.execute("SELECT sq, observaciones FROM casos WHERE observaciones IS NOT NULL")
        from .texto import revisar_instrucciones
        for sq, obs in cur.fetchall():
            motivo = revisar_instrucciones(obs)
            if motivo:
                cur.execute("INSERT INTO alertas_seguridad (rol, campo, sq, motivo, extracto) VALUES ('CARGA', 'casos.observaciones', %s, %s, %s)",
                            (sq, motivo, obs[:160]))
        resumen = {}
        for t in TABLAS:
            cur.execute(f"SELECT count(*) FROM {t}")
            resumen[t] = cur.fetchone()[0]
        cur.execute("INSERT INTO auditoria (rol, quien, accion, detalle) VALUES ('TI', 'Carga inicial', 'carga_inicial', %s::jsonb)",
                    (json.dumps({"fecha_carga": hoy.isoformat(), "semana_por_programar": semana.isoformat(), "vacia": vacia, "filas": resumen}),))
    conn.commit()
    informar(f"Datos cargados. Semana por programar: {semana.isoformat()}")
    return {"fecha_carga": hoy.isoformat(), "semana_por_programar": semana.isoformat(), "base": "vacía (solo catálogos)" if vacia else "con datos de ejemplo", "filas": resumen}
