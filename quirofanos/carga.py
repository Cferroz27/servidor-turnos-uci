"""Crea las tablas del proceso quirúrgico y carga los datos ficticios (relativos a la fecha de carga)."""
import json
from pathlib import Path

from psycopg.rows import dict_row

from . import datos as D

AQUI = Path(__file__).resolve().parent
TABLAS = ["parametros", "salas", "equipos_biomedicos", "cirujanos", "aseguradoras", "procedimientos", "novedades_recursos",
          "casos", "autorizaciones", "programaciones", "programacion_casos", "preparacion", "insumos_caso", "confirmaciones",
          "ingreso", "lista_chequeo", "tiempos", "cancelaciones", "notas", "facturas", "glosas", "auditoria", "alertas_seguridad", "salidas", "soportes"]
SERIALES = {"novedades_recursos": "id", "autorizaciones": "id", "programaciones": "id", "insumos_caso": "id",
            "cancelaciones": "id", "notas": "id", "glosas": "id", "auditoria": "id", "alertas_seguridad": "id", "salidas": "id", "soportes": "id"}


def pdf_ficticio(titulo, sq):
    """Un PDF mínimo válido (una página de texto) para los soportes de ejemplo."""
    import hashlib  # noqa: F401
    texto = f"DOCUMENTO FICTICIO - TALLER IA CON CLAUDE | {titulo} | Caso {sq} | Sin datos personales"
    texto = texto.encode("latin-1", "replace").decode("latin-1").replace("(", "[").replace(")", "]")
    stream = f"BT /F1 11 Tf 40 780 Td ({texto}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def sembrar_soportes(cur):
    """Soportes de ejemplo: casos realizados (todos los de cobro) y casos activos con la preparación completa
    (consentimiento firmado y autorización), para que haya pacientes habilitados para agendar desde el inicio."""
    import hashlib
    cur.execute("""SELECT c.sq, c.estado, p.requiere_implante,
                          (SELECT estado FROM v_autorizacion_vigente v WHERE v.sq = c.sq) AS aut,
                          NOT EXISTS (SELECT 1 FROM preparacion x WHERE x.sq = c.sq AND x.estado = 'PENDIENTE' AND x.item <> 'CAMA_UCI') AS prep_ok
                   FROM casos c JOIN procedimientos p ON p.codigo = c.procedimiento""")
    filas = []
    for sq, estado, implante, aut, prep_ok in cur.fetchall():
        tipos = []
        if estado == "REALIZADO":
            tipos = ["CONSENTIMIENTO_FIRMADO", "AUTORIZACION", "DESCRIPCION_QUIRURGICA", "REGISTRO_ANESTESIA"] + (["REGISTRO_IMPLANTE"] if implante else [])
        elif estado == "ACTIVO" and prep_ok:
            tipos = ["CONSENTIMIENTO_FIRMADO"] + (["AUTORIZACION"] if aut == "APROBADA" else [])
        for t in tipos:
            b = pdf_ficticio(t.replace("_", " ").title(), sq)
            filas.append((sq, t, f"{t.lower()}_{sq}.pdf", "application/pdf", len(b), hashlib.sha256(b).hexdigest(), b,
                          "CARGA", "Carga inicial"))
    cur.executemany("""INSERT INTO soportes (sq, tipo, nombre_archivo, mime, tamano, sha256, contenido, rol, subido_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", filas)


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
            sembrar_soportes(cur)
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
