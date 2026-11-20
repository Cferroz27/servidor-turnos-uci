"""Crea las tablas y carga los datos iniciales (CSV) en una base PostgreSQL.

Lo usan dos caminos:
  - El servidor (herramienta de TI `cargar_datos_iniciales`).
  - El script local `cargar_base.py`, que se ejecuta en el computador de TI.
"""
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ORDEN = ["cargos", "colaboradores", "identidad", "tipos_turno", "cobertura", "festivos", "parametros_programacion",
         "parametros_nomina", "novedades", "programaciones", "asignaciones", "horas_liquidadas"]
SECUENCIAS = {"novedades": "id", "programaciones": "id"}


def _esquema_actual(cur):
    cur.execute("SELECT current_schema()")
    return '"' + cur.fetchone()[0] + '"'


def tablas_con_datos(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('colaboradores') IS NOT NULL")
        if not cur.fetchone()[0]:
            return 0
        cur.execute("SELECT count(*) FROM colaboradores")
        return cur.fetchone()[0]


def cargar(conn, carpeta_datos=None, esquema=None, reiniciar=False, informar=print):
    carpeta = Path(carpeta_datos or AQUI / "datos")
    esquema = Path(esquema or AQUI / "esquema.sql")
    existentes = tablas_con_datos(conn)
    if existentes and not reiniciar:
        raise RuntimeError(f"La base ya tiene {existentes} colaboradores. Para borrar todo y volver a cargar: en Claude, reiniciar=true; en el script, --reiniciar.")
    with conn.cursor() as cur:
        if reiniciar:
            cur.execute("DROP VIEW IF EXISTS v_horas_mes")
            for t in reversed(ORDEN + ["auditoria"]):
                cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
            informar("Tablas anteriores eliminadas.")
        cur.execute(esquema.read_text(encoding="utf-8"))
        informar("Tablas creadas.")
        resumen = {}
        for t in ORDEN:
            archivo = carpeta / f"{t}.csv"
            with open(archivo, encoding="utf-8") as f:
                cabecera = f.readline().strip()
                f.seek(0)
                with cur.copy(f"COPY {t} ({cabecera}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')") as cp:
                    while data := f.read(65536):
                        cp.write(data)
            cur.execute(f"SELECT count(*) FROM {t}")
            resumen[t] = cur.fetchone()[0]
            informar(f"  {t}: {resumen[t]} filas")
        for t, col in SECUENCIAS.items():
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}','{col}'), COALESCE(MAX({col}),0)+1, false) FROM {t}")
        # Permisos para el usuario de TI del equipo (opción B): el acceso real lo controla el USAGE sobre el esquema
        esq = _esquema_actual(cur)
        cur.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA {esq} TO PUBLIC")
        cur.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA {esq} TO PUBLIC")
        cur.execute("INSERT INTO auditoria (rol, accion, detalle) VALUES ('TI', 'carga_inicial', %s::jsonb)",
                    (__import__("json").dumps(resumen),))
    conn.commit()
    return resumen
