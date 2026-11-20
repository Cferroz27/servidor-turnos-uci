"""Reglas de negocio del sistema de turnos: lo que pueden hacer las herramientas de cada rol."""
import json, datetime as dt
from collections import defaultdict
from psycopg.rows import dict_row
from . import motor, nomina

SERVICIO = "UCI"
LETRAS = {"M", "T", "N"}


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def auditar(conn, rol, accion, detalle):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO auditoria (rol, accion, detalle) VALUES (%s, %s, %s::jsonb)",
                    (rol, accion, json.dumps(detalle, ensure_ascii=False, default=str)))


def _q(conn, sql, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def validar_mes(mes):
    try:
        a, m = map(int, mes.split("-"))
        dt.date(a, m, 1)
    except Exception:
        raise ValueError("El mes debe tener el formato AAAA-MM, por ejemplo 2026-12.")


# ---------- parámetros ----------
def parametros(conn, mes):
    P = {}
    for r in _q(conn, "SELECT clave, valor FROM parametros_programacion"):
        v = r["valor"]
        P[r["clave"]] = int(v) if v.isdigit() else v.split(",")
    P["turnos"] = {r["codigo"]: {"nombre": r["nombre"], "inicio": f"{r['hora_inicio']:02d}:00", "hora_inicio": r["hora_inicio"], "horas": r["horas"]}
                   for r in _q(conn, "SELECT * FROM tipos_turno")}
    P["orden_asignacion"] = ["N", "M", "T"]
    P["cobertura"] = defaultdict(dict)
    for r in _q(conn, "SELECT * FROM cobertura"):
        P["cobertura"][r["turno"]][r["rol"]] = r["minimo"]
    P["cobertura"] = dict(P["cobertura"])
    dias = motor.dias_del_mes(mes)
    P["festivos"] = [r["fecha"].isoformat() for r in _q(conn, "SELECT fecha FROM festivos WHERE fecha BETWEEN %s AND %s ORDER BY fecha",
                                                        (dias[0] - dt.timedelta(days=7), dias[-1] + dt.timedelta(days=7)))]
    return P


def parametros_nomina(conn, fecha):
    r = _q(conn, "SELECT * FROM parametros_nomina WHERE vigente_desde <= %s ORDER BY vigente_desde DESC LIMIT 1", (fecha,))
    if not r:
        raise ValueError(f"No hay parámetros de nómina vigentes para {fecha}.")
    return r[0]


# ---------- personal ----------
def colaboradores(conn, servicio=SERVICIO, incluir_retirados=False):
    sql = """SELECT c.codigo, c.id_cargo, g.nombre AS cargo, g.rol, c.servicio, c.tipo_contrato, c.fecha_ingreso, c.fecha_retiro,
                    c.horas_contrato_semana, c.acepta_noche, c.activo, g.hace_turnos,
                    (i.codigo IS NOT NULL) AS identidad_registrada
             FROM colaboradores c JOIN cargos g USING (id_cargo) LEFT JOIN identidad i USING (codigo)
             WHERE (%s = '' OR c.servicio = %s) AND (%s OR c.activo) ORDER BY c.codigo"""
    return _q(conn, sql, (servicio or "", servicio or "", incluir_retirados))


def personal_del_mes(conn, mes):
    dias = motor.dias_del_mes(mes)
    filas = _q(conn, """SELECT c.codigo, g.rol, c.horas_contrato_semana, c.acepta_noche, c.fecha_ingreso, c.fecha_retiro
                        FROM colaboradores c JOIN cargos g USING (id_cargo)
                        WHERE c.servicio = %s AND g.hace_turnos AND c.fecha_ingreso <= %s
                          AND (c.fecha_retiro IS NULL OR c.fecha_retiro >= %s) ORDER BY c.codigo""",
               (SERVICIO, dias[-1], dias[0]))
    personas, ausencias = [], defaultdict(dict)
    for f in filas:
        personas.append({"codigo": f["codigo"], "rol": f["rol"], "horas_contrato": float(f["horas_contrato_semana"]), "acepta_noche": f["acepta_noche"]})
        for d in dias:
            if d < f["fecha_ingreso"]:
                ausencias[f["codigo"]][d] = "ING"
            elif f["fecha_retiro"] and d > f["fecha_retiro"]:
                ausencias[f["codigo"]][d] = "RET"
    for n in _q(conn, "SELECT * FROM novedades WHERE NOT anulada AND desde <= %s AND hasta >= %s", (dias[-1], dias[0])):
        d = max(n["desde"], dias[0])
        while d <= min(n["hasta"], dias[-1]):
            ausencias[n["codigo"]][d] = n["tipo"]
            d += dt.timedelta(days=1)
    return personas, dict(ausencias)


def estado_anterior(conn, mes):
    a, m = map(int, mes.split("-"))
    ant = f"{a - 1}-12" if m == 1 else f"{a}-{m - 1:02d}"
    r = _q(conn, "SELECT estado_cierre FROM programaciones WHERE mes = %s AND estado = 'APROBADA'", (ant,))
    return r[0]["estado_cierre"] if r else None


def datos_programacion(conn, mes):
    validar_mes(mes)
    personas, aus = personal_del_mes(conn, mes)
    P = parametros(conn, mes)
    prev = estado_anterior(conn, mes)
    novs = []
    for cod, dias in aus.items():
        for d in sorted(dias):
            novs.append({"codigo": cod, "fecha": d.isoformat(), "tipo": dias[d]})
    return {
        "mes": mes,
        "dias_del_mes": len(motor.dias_del_mes(mes)),
        "parametros": P,
        "plantilla": [{"codigo": p["codigo"], "rol": p["rol"], "horas_contrato_semana": p["horas_contrato"],
                       "acepta_noche": "si" if p["acepta_noche"] else "no", "activo": "si"} for p in personas],
        "novedades_por_dia": novs,
        "estado_cierre_mes_anterior": prev,
        "formato_para_guardar": "cuadro = {codigo: cadena de un carácter por día: M, T, N o - (libre)}",
    }


# ---------- programación ----------
def _parsear_cuadro(conn, mes, cuadro):
    dias = motor.dias_del_mes(mes)
    personas, aus = personal_del_mes(conn, mes)
    validos = {p["codigo"] for p in personas}
    errores, out = [], {}
    if isinstance(cuadro, str):
        cuadro = json.loads(cuadro)
    for cod, cadena in cuadro.items():
        cod = cod.strip().upper()
        if cod not in validos:
            errores.append(f"{cod} no es personal de la UCI con turnos en {mes}.")
            continue
        cadena = cadena.replace(" ", "")
        if len(cadena) != len(dias):
            errores.append(f"{cod}: la cadena tiene {len(cadena)} caracteres y el mes tiene {len(dias)} días.")
            continue
        out[cod] = {d: ch.upper() for d, ch in zip(dias, cadena) if ch.upper() in LETRAS}
    faltan = validos - set(out)
    for cod in faltan:
        out[cod] = {}
    return dias, personas, aus, out, errores, sorted(faltan)


def guardar_borrador(conn, mes, cuadro, creada_por, semilla=None, observaciones=""):
    validar_mes(mes)
    dias, personas, aus, cdr, errores, faltan = _parsear_cuadro(conn, mes, cuadro)
    if errores:
        return {"guardado": False, "errores": errores}
    P = parametros(conn, mes)
    prev = estado_anterior(conn, mes)
    ver = motor.verificar(personas, dias, cdr, aus, P, prev)
    with conn.cursor() as cur:
        cur.execute("UPDATE programaciones SET estado='DESCARTADA' WHERE mes=%s AND estado='BORRADOR'", (mes,))
        cur.execute("SELECT COALESCE(MAX(version),0)+1 FROM programaciones WHERE mes=%s", (mes,))
        version = cur.fetchone()[0]
        cur.execute("""INSERT INTO programaciones (mes, version, estado, semilla, observaciones, verificacion, creada_por)
                       VALUES (%s,%s,'BORRADOR',%s,%s,%s::jsonb,%s) RETURNING id""",
                    (mes, version, semilla, observaciones, json.dumps(ver, ensure_ascii=False), creada_por))
        pid = cur.fetchone()[0]
        cur.executemany("INSERT INTO asignaciones VALUES (%s,%s,%s,%s)",
                        [(pid, c, d, t) for c in cdr for d, t in cdr[c].items()])
    auditar(conn, "JEFE", "guardar_borrador", {"mes": mes, "version": version, "por": creada_por,
                                               "reglas_incumplidas": [r["regla"] for r in ver if not r["ok"]]})
    conn.commit()
    return {"guardado": True, "mes": mes, "version": version, "estado": "BORRADOR",
            "codigos_sin_turnos": faltan, "verificacion_del_servidor": ver,
            "siguiente_paso": "Si todas las reglas CUMPLEN, la jefe de la UCI puede aprobar con aprobar_programacion."}


def ver_programacion(conn, mes, version=None):
    validar_mes(mes)
    if version:
        r = _q(conn, "SELECT * FROM programaciones WHERE mes=%s AND version=%s", (mes, version))
    else:
        r = _q(conn, """SELECT * FROM programaciones WHERE mes=%s AND estado IN ('APROBADA','BORRADOR')
                        ORDER BY (estado='BORRADOR') DESC, version DESC LIMIT 1""", (mes,))
    if not r:
        return {"mes": mes, "existe": False, "mensaje": "No hay programación para ese mes."}
    p = r[0]
    dias = motor.dias_del_mes(mes)
    cuadro = defaultdict(lambda: ["-"] * len(dias))
    for a in _q(conn, "SELECT codigo, fecha, turno FROM asignaciones WHERE programacion_id=%s", (p["id"],)):
        cuadro[a["codigo"]][a["fecha"].day - 1] = a["turno"]
    _, aus = personal_del_mes(conn, mes)
    for cod, ds in aus.items():
        for d, tipo in ds.items():
            if cuadro[cod][d.day - 1] == "-":
                cuadro[cod][d.day - 1] = {"VAC": "v", "LIC": "l", "INC": "i", "AUS": "a", "ING": "n", "RET": "r"}.get(tipo, "a")
    conflictos = []
    for a in _q(conn, "SELECT codigo, fecha, turno FROM asignaciones WHERE programacion_id=%s ORDER BY fecha", (p["id"],)):
        tipo = aus.get(a["codigo"], {}).get(a["fecha"])
        if tipo:
            conflictos.append({"codigo": a["codigo"], "fecha": a["fecha"], "turno": a["turno"], "novedad": tipo})
    versiones = _q(conn, "SELECT version, estado, creada_por, creada_en, aprobada_por FROM programaciones WHERE mes=%s ORDER BY version", (mes,))
    return {"mes": mes, "version": p["version"], "estado": p["estado"], "creada_por": p["creada_por"], "aprobada_por": p["aprobada_por"],
            "cuadro": {c: "".join(v) for c, v in sorted(cuadro.items())},
            "leyenda": "M mañana, T tarde, N noche, - libre; en minúscula: v vacaciones, l licencia, i incapacidad, a ausencia, n aún no ingresa, r retirado",
            "verificacion": p["verificacion"], "versiones": versiones,
            "conflictos_con_novedades_posteriores": conflictos,
            "accion_sugerida": "Hay turnos asignados en días con novedad: reprograme y apruebe una nueva versión." if conflictos else ""}


def aprobar(conn, mes, aprobada_por, justificacion_excepcion=""):
    validar_mes(mes)
    r = _q(conn, "SELECT * FROM programaciones WHERE mes=%s AND estado='BORRADOR' ORDER BY version DESC LIMIT 1", (mes,))
    if not r:
        return {"aprobada": False, "mensaje": "No hay un borrador para ese mes. Primero guarde uno con guardar_borrador."}
    p = r[0]
    incumple = [x["regla"] for x in p["verificacion"] if not x["ok"]]
    if incumple and not justificacion_excepcion.strip():
        return {"aprobada": False, "reglas_incumplidas": incumple,
                "mensaje": "El borrador incumple reglas. Corríjalo, o apruebe con una justificación de excepción firmada por la coordinación."}
    dias, personas, aus, cdr, _, _ = _parsear_cuadro(conn, mes, ver_cadena(conn, p["id"], mes))
    P = parametros(conn, mes)
    prev = estado_anterior(conn, mes)
    cierre = motor.estado_cierre(personas, dias, cdr, P, aus, prev)
    fest = {dt.date.fromisoformat(f) for f in P["festivos"]}
    filas = [(c, d, t) for c in cdr for d, t in cdr[c].items()]
    par = parametros_nomina(conn, dias[0])
    liq = nomina.liquidar_horas(filas, P["turnos"], fest, par["hora_inicio_nocturna"], par["hora_fin_nocturna"])
    with conn.cursor() as cur:
        cur.execute("UPDATE programaciones SET estado='REEMPLAZADA' WHERE mes=%s AND estado='APROBADA'", (mes,))
        cur.execute("""UPDATE programaciones SET estado='APROBADA', aprobada_por=%s, aprobada_en=now(),
                       justificacion_excepcion=%s, estado_cierre=%s::jsonb WHERE id=%s""",
                    (aprobada_por, justificacion_excepcion or None, json.dumps(cierre, ensure_ascii=False), p["id"]))
        cur.executemany("INSERT INTO horas_liquidadas VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(p["id"], c, d) + tuple(tp.get(x, 0) for x in nomina.TIPOS) for (c, d), tp in liq.items()])
    auditar(conn, "JEFE", "aprobar_programacion", {"mes": mes, "version": p["version"], "por": aprobada_por,
                                                   "excepcion": justificacion_excepcion or None})
    conn.commit()
    return {"aprobada": True, "mes": mes, "version": p["version"],
            "mensaje": "Programación aprobada. Las horas ya están clasificadas y disponibles para nómina."}


def ver_cadena(conn, pid, mes):
    dias = motor.dias_del_mes(mes)
    c = defaultdict(lambda: ["-"] * len(dias))
    for a in _q(conn, "SELECT codigo, fecha, turno FROM asignaciones WHERE programacion_id=%s", (pid,)):
        c[a["codigo"]][a["fecha"].day - 1] = a["turno"]
    return {k: "".join(v) for k, v in c.items()}


# ---------- novedades y personal (talento humano) ----------
def registrar_novedad(conn, codigo, tipo, desde, hasta, observacion, registrada_por):
    tipo = tipo.upper()[:3]
    if tipo not in ("VAC", "LIC", "INC", "AUS"):
        raise ValueError("Tipo debe ser VAC (vacaciones), LIC (licencia), INC (incapacidad) o AUS (otra ausencia).")
    d1, d2 = _d(desde), _d(hasta)
    if d2 < d1:
        raise ValueError("La fecha 'hasta' es anterior a 'desde'.")
    if not _q(conn, "SELECT 1 FROM colaboradores WHERE codigo=%s", (codigo.upper(),)):
        raise ValueError(f"No existe el código {codigo}.")
    cruces = _q(conn, "SELECT id, tipo, desde, hasta FROM novedades WHERE codigo=%s AND NOT anulada AND desde<=%s AND hasta>=%s",
                (codigo.upper(), d2, d1))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO novedades (codigo, tipo, desde, hasta, observacion, registrada_por)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""", (codigo.upper(), tipo, d1, d2, observacion, registrada_por))
        nid = cur.fetchone()[0]
    auditar(conn, "TALENTO_HUMANO", "registrar_novedad", {"id": nid, "codigo": codigo.upper(), "tipo": tipo, "desde": d1, "hasta": d2})
    afectadas = _q(conn, """SELECT DISTINCT p.mes, p.version, p.estado FROM programaciones p JOIN asignaciones a ON a.programacion_id=p.id
                            WHERE p.estado IN ('APROBADA','BORRADOR') AND a.codigo=%s AND a.fecha BETWEEN %s AND %s""",
                   (codigo.upper(), d1, d2))
    conn.commit()
    return {"registrada": True, "id": nid, "cruza_con_novedades_existentes": cruces,
            "programaciones_afectadas": afectadas,
            "aviso": "Hay programaciones con turnos en esas fechas: la jefe de la UCI debe reprogramar." if afectadas else ""}


def listar_novedades(conn, mes=None, incluir_anuladas=False):
    if mes:
        validar_mes(mes)
        dias = motor.dias_del_mes(mes)
        return _q(conn, """SELECT id, codigo, tipo, desde, hasta, observacion, registrada_por, registrada_en, anulada FROM novedades
                           WHERE desde <= %s AND hasta >= %s AND (%s OR NOT anulada) ORDER BY desde""", (dias[-1], dias[0], incluir_anuladas))
    return _q(conn, "SELECT * FROM novedades WHERE (%s OR NOT anulada) ORDER BY desde DESC LIMIT 100", (incluir_anuladas,))


def anular_novedad(conn, id_novedad, motivo):
    with conn.cursor() as cur:
        cur.execute("UPDATE novedades SET anulada=TRUE, observacion=COALESCE(observacion,'')||' | ANULADA: '||%s WHERE id=%s RETURNING codigo",
                    (motivo, id_novedad))
        r = cur.fetchone()
    if not r:
        raise ValueError(f"No existe la novedad {id_novedad}.")
    auditar(conn, "TALENTO_HUMANO", "anular_novedad", {"id": id_novedad, "motivo": motivo})
    conn.commit()
    return {"anulada": True, "id": id_novedad, "codigo": r[0]}


def registrar_ingreso(conn, id_cargo, fecha_ingreso, tipo_contrato, horas_contrato_semana, acepta_noche, registrada_por, servicio=SERVICIO):
    if not _q(conn, "SELECT 1 FROM cargos WHERE id_cargo=%s", (id_cargo,)):
        raise ValueError("Cargo no válido. Use ENF_JEFE, AUX_ENF o COORD_UCI.")
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(CAST(SUBSTRING(codigo FROM 5) AS INT)),0)+1 FROM colaboradores")
        n = cur.fetchone()[0]
        cod = f"EMP-{n:02d}"
        cur.execute("""INSERT INTO colaboradores VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,TRUE)""",
                    (cod, id_cargo, servicio, tipo_contrato.upper(), _d(fecha_ingreso), horas_contrato_semana, bool(acepta_noche)))
    auditar(conn, "TALENTO_HUMANO", "registrar_ingreso", {"codigo": cod, "cargo": id_cargo, "fecha_ingreso": fecha_ingreso, "por": registrada_por})
    conn.commit()
    return {"registrado": True, "codigo": cod,
            "pendiente": "Los datos personales de esta persona NO se registran por Claude. TI los carga dentro de la institución "
                         "(script agregar_identidad.py). Hasta entonces nómina la verá como 'identidad pendiente'."}


def registrar_retiro(conn, codigo, fecha_retiro, registrada_por):
    with conn.cursor() as cur:
        cur.execute("UPDATE colaboradores SET fecha_retiro=%s, activo=(%s >= CURRENT_DATE) WHERE codigo=%s RETURNING codigo",
                    (_d(fecha_retiro), _d(fecha_retiro), codigo.upper()))
        if not cur.fetchone():
            raise ValueError(f"No existe el código {codigo}.")
    auditar(conn, "TALENTO_HUMANO", "registrar_retiro", {"codigo": codigo.upper(), "fecha_retiro": fecha_retiro, "por": registrada_por})
    conn.commit()
    return {"registrado": True, "codigo": codigo.upper(), "fecha_retiro": fecha_retiro,
            "aviso": "Si ya hay programación aprobada después de esa fecha, la jefe de la UCI debe reprogramar."}


# ---------- nómina ----------
def horas_mes(conn, mes):
    validar_mes(mes)
    filas = _q(conn, "SELECT * FROM v_horas_mes WHERE mes=%s ORDER BY codigo", (mes,))
    for f in filas:
        for k in nomina.TIPOS:
            f[k] = float(f[k])
        f["total_horas"] = sum(f[k] for k in nomina.TIPOS)
    aprobada = _q(conn, "SELECT version, aprobada_por, aprobada_en FROM programaciones WHERE mes=%s AND estado='APROBADA'", (mes,))
    return {"mes": mes, "programacion_aprobada": aprobada[0] if aprobada else None,
            "nota": "Horas por mes calendario: la noche del último día del mes anterior aporta sus horas de madrugada a este mes.",
            "horas": filas}


def prenomina(conn, mes):
    validar_mes(mes)
    dias = motor.dias_del_mes(mes)
    par = parametros_nomina(conn, dias[0])
    horas = {h["codigo"]: h for h in horas_mes(conn, mes)["horas"]}
    gente = _q(conn, """SELECT c.codigo, g.nombre AS cargo, g.salario_base, c.fecha_ingreso, c.fecha_retiro,
                               (i.codigo IS NOT NULL) AS identidad_registrada
                        FROM colaboradores c JOIN cargos g USING (id_cargo) LEFT JOIN identidad i USING (codigo)
                        WHERE c.servicio=%s AND c.fecha_ingreso <= %s AND (c.fecha_retiro IS NULL OR c.fecha_retiro >= %s)
                        ORDER BY c.codigo""", (SERVICIO, dias[-1], dias[0]))
    nov = defaultdict(list)
    for n in listar_novedades(conn, mes):
        nov[n["codigo"]].append(f"{n['tipo']} {n['desde']:%d/%m}–{n['hasta']:%d/%m}")
    filas, tot = [], defaultdict(int)
    for g in gente:
        ini = max(g["fecha_ingreso"], dias[0])
        fin = min(g["fecha_retiro"] or dias[-1], dias[-1])
        dl = 30 if (ini == dias[0] and fin == dias[-1]) else min(30, (fin - ini).days + 1)
        h = horas.get(g["codigo"], {})
        liq = nomina.prenomina_persona(g["salario_base"], h, par, dl)
        fila = {"codigo": g["codigo"], "cargo": g["cargo"], **{k: h.get(k, 0) for k in nomina.TIPOS}, **liq,
                "novedades_para_nomina": "; ".join(nov.get(g["codigo"], [])),
                "identidad": "registrada" if g["identidad_registrada"] else "PENDIENTE"}
        filas.append(fila)
        for k in ("salario_basico_mes", "total_recargos", "total_devengado", "deduccion_salud", "deduccion_pension", "neto_a_pagar"):
            tot[k] += liq[k]
    return {"mes": mes, "parametros_aplicados": {k: (float(v) if not isinstance(v, (str, dt.date)) else str(v)) for k, v in par.items()},
            "advertencias": ["Valores de prenómina: no incluyen horas extra, incapacidades, licencias, auxilio de transporte ni aportes del empleador.",
                             "Los salarios son ficticios. Solo códigos: la identificación se hace en el sistema de nómina de la institución."],
            "filas": filas, "totales": dict(tot)}


def resumen_costos(conn, meses):
    out = []
    for mes in meses:
        p = prenomina(conn, mes)
        if not p["filas"]:
            continue
        h = defaultdict(float)
        for f in p["filas"]:
            for k in nomina.TIPOS:
                h[k] += f[k]
        out.append({"mes": mes, **{k: round(v, 1) for k, v in h.items()}, **p["totales"]})
    return out


# ---------- TI ----------
def estado_sistema(conn):
    if not _q(conn, "SELECT to_regclass('colaboradores') AS t")[0]["t"]:
        return {"conexion": "ok", "estado": "VACÍA", "mensaje": "La base del equipo está vacía. Cargue los datos con cargar_datos_iniciales (opción A) o con cargar_base.py (opción B)."}
    t = {}
    for tabla in ("colaboradores", "identidad", "novedades", "programaciones", "asignaciones", "horas_liquidadas", "auditoria"):
        t[tabla] = _q(conn, f"SELECT count(*) AS n FROM {tabla}")[0]["n"]
    progs = _q(conn, "SELECT mes, version, estado, creada_por, aprobada_por FROM programaciones ORDER BY mes, version")
    sin_ident = _q(conn, "SELECT codigo FROM colaboradores c WHERE NOT EXISTS (SELECT 1 FROM identidad i WHERE i.codigo=c.codigo)")
    return {"conexion": "ok", "filas_por_tabla": t, "programaciones": progs, "colaboradores_sin_identidad": [r["codigo"] for r in sin_ident]}


def ver_auditoria(conn, limite=30):
    return _q(conn, "SELECT id, momento, rol, accion, detalle FROM auditoria ORDER BY id DESC LIMIT %s", (min(int(limite), 200),))
