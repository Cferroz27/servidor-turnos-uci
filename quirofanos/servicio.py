"""Reglas de negocio del proceso quirúrgico: lo que pueden hacer las herramientas de cada rol.
Todas las funciones reciben una conexión abierta (search_path ya apunta al esquema del equipo)."""
import datetime as dt
import json
from collections import defaultdict
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from . import reglas, texto
from .datos import ITEMS_CHEQUEO, SOPORTES

ETAPAS = ["Solicitud", "Autorización", "Programación", "Preparación prequirúrgica", "Verificación de insumos",
          "Confirmación del cirujano", "Ingreso del paciente", "Cirugía", "Recuperación y egreso", "Cierre administrativo y contable"]
RESPONSABLES = ["Administrativo", "Administrativo", "Asistencial (coordinación de cirugía)", "Enfermería y anestesia",
                "Central de esterilización y almacén", "Cirujano", "Admisiones y enfermería", "Equipo de sala", "Enfermería",
                "Contable (facturación)"]
ITEMS_PREP = ["VALORACION_PREANESTESICA", "EXAMENES", "CONSENTIMIENTO_INFORMADO", "RESERVA_SANGRE", "CAMA_UCI"]
ITEMS_INGRESO = ["LLEGADA", "IDENTIFICACION", "AYUNO", "CONSENTIMIENTO_FIRMADO", "RETIRO_PROTESIS_JOYAS", "MARCACION_SITIO"]
DIAS = {"1": "lunes", "2": "martes", "3": "miércoles", "4": "jueves", "5": "viernes"}
CONCEPTOS_GLOSA = {"FA": "Facturación", "TA": "Tarifas", "SO": "Soportes", "AU": "Autorización", "CO": "Cobertura",
                   "CL": "Calidad", "SA": "Seguimiento a los acuerdos de voluntades"}
ROLES_FINANCIEROS = {"CONTABLE", "CONSULTA"}
NOMBRE = {"VALORACION_PREANESTESICA": "valoración preanestésica", "EXAMENES": "exámenes", "CONSENTIMIENTO_INFORMADO": "consentimiento informado",
          "RESERVA_SANGRE": "reserva de sangre", "CAMA_UCI": "cama de UCI", "LLEGADA": "llegada", "IDENTIFICACION": "identificación",
          "AYUNO": "ayuno", "CONSENTIMIENTO_FIRMADO": "consentimiento firmado", "RETIRO_PROTESIS_JOYAS": "retiro de prótesis y joyas",
          "MARCACION_SITIO": "marcación del sitio quirúrgico", "LISTO_PARA_SALA": "listo para sala"}


def nom(k):
    return NOMBRE.get(k, k.replace("_", " ").lower())


# ------------------------------------------------------------------ utilidades
def _q(conn, sql, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def _uno(conn, sql, args=()):
    r = _q(conn, sql, args)
    return r[0] if r else None


def _f(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _txt(x):
    return None if x is None else str(x)


def auditar(conn, rol, accion, quien=None, sq=None, detalle=None):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO auditoria (rol, quien, accion, sq, detalle) VALUES (%s, %s, %s, %s, %s::jsonb)",
                    (rol, quien, accion, sq, json.dumps(detalle or {}, ensure_ascii=False, default=str)))


def revisar_texto(conn, rol, campo, valor, sq=None):
    """Rechaza datos personales y marca instrucciones incrustadas. Devuelve un aviso o None."""
    if not valor:
        return None
    pii = texto.revisar_pii(valor)
    if pii:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO alertas_seguridad (rol, campo, sq, motivo, extracto) VALUES (%s, %s, %s, %s, %s)",
                        (rol, campo, sq, f"Dato personal bloqueado ({pii})", "[texto no guardado]"))
        conn.commit()
        raise ValueError(f"El texto de '{campo}' parece contener un dato personal ({pii}). No se guardó. "
                         "Use solo el código SQ y cargos, nunca nombres, documentos, teléfonos ni correos.")
    instr = texto.revisar_instrucciones(valor)
    if instr:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO alertas_seguridad (rol, campo, sq, motivo, extracto) VALUES (%s, %s, %s, %s, %s)",
                        (rol, campo, sq, instr, valor[:160]))
        return "El texto contiene una instrucción dirigida al sistema. Se guardó como dato, no se ejecutó, y quedó una alerta para TI."
    return None


def parametros(conn):
    return {r["clave"]: r["valor"] for r in _q(conn, "SELECT clave, valor FROM parametros")}


def hoy(conn):
    v = parametros(conn).get("fecha_hoy", "")
    return _f(v) if v else dt.datetime.now(ZoneInfo("America/Bogota")).date()


def festivos(conn):
    return {x for x in parametros(conn).get("festivos", "").split(",") if x}


def lunes(fecha):
    fecha = _f(fecha)
    return fecha - dt.timedelta(days=fecha.weekday())


def dias_habiles(semana, fest):
    return [semana + dt.timedelta(days=i) for i in range(5) if (semana + dt.timedelta(days=i)).isoformat() not in fest]


def sumar_habiles(fecha, n, fest):
    d, k = _f(fecha), 0
    while k < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in fest:
            k += 1
    return d


def dia_habil_anterior(fecha, fest):
    d = _f(fecha) - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in fest:
        d -= dt.timedelta(days=1)
    return d


def habiles_entre(a, b, fest):
    a, b = _f(a), _f(b)
    signo = 1 if b >= a else -1
    if signo < 0:
        a, b = b, a
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in fest:
            n += 1
    return n * signo


def caso_existe(conn, sq):
    c = _uno(conn, "SELECT * FROM casos WHERE sq = %s", (str(sq).strip().upper(),))
    if not c:
        raise ValueError(f"No existe el caso {sq}. Use el código SQ (por ejemplo SQ-0901).")
    return c


# ------------------------------------------------------------------ programaciones
def _prog(conn, semana, estado):
    return _uno(conn, "SELECT * FROM programaciones WHERE semana = %s AND estado = %s ORDER BY version DESC LIMIT 1", (semana, estado))


def _asig(conn, prog_id):
    return [{"sq": r["sq"], "fecha": r["fecha"].isoformat(), "sala": r["id_sala"], "hora_inicio": r["hora_inicio"].strftime("%H:%M"),
             "hora_fin": r["hora_fin"].strftime("%H:%M")}
            for r in _q(conn, "SELECT * FROM programacion_casos WHERE programacion_id = %s ORDER BY fecha, id_sala, hora_inicio", (prog_id,))]


def programacion_vigente(conn, semana):
    """La aprobada vigente y, si existe, un borrador más reciente."""
    aprob = _prog(conn, semana, "APROBADA")
    borr = _prog(conn, semana, "BORRADOR")
    if borr and aprob and borr["version"] < aprob["version"]:
        borr = None
    return aprob, borr


# ------------------------------------------------------------------ contexto (todo lo que el motor necesita)
def contexto(conn):
    P = parametros(conn)
    H = hoy(conn)
    salas = {r["id_sala"]: {**r, "hora_inicio": r["hora_inicio"].strftime("%H:%M"), "hora_fin": r["hora_fin"].strftime("%H:%M")}
             for r in _q(conn, "SELECT * FROM salas ORDER BY id_sala")}
    equipos = [{**r, "mantenimiento_vence": r["mantenimiento_vence"].isoformat()} for r in _q(conn, "SELECT * FROM equipos_biomedicos ORDER BY id_equipo")]
    cirujanos = {}
    for r in _q(conn, "SELECT * FROM cirujanos WHERE activo ORDER BY codigo"):
        cirujanos[r["codigo"]] = {**r, "hora_fin": r["hora_fin"].strftime("%H:%M"),
                                  "dias_texto": ", ".join(DIAS[d] for d in r["dias"].split(","))}
    novedades = [{**r, "fecha": r["fecha"].isoformat()} for r in _q(conn, "SELECT * FROM novedades_recursos WHERE fecha >= %s ORDER BY fecha", (H - dt.timedelta(days=7),))]
    casos = {}
    filas = _q(conn, """SELECT c.*, p.nombre AS procedimiento_nombre, p.especialidad, p.tipo_cirugia, p.duracion_min, p.equipo_requerido,
                               p.set_instrumental, p.requiere_implante, p.requiere_sangre, p.costo_estimado, p.tarifa,
                               a.nombre AS aseguradora, v.estado AS aut_estado, v.numero AS aut_numero, v.vigente_hasta AS aut_vigente
                        FROM casos c JOIN procedimientos p ON p.codigo = c.procedimiento
                        JOIN aseguradoras a ON a.id_aseguradora = c.id_aseguradora
                        LEFT JOIN v_autorizacion_vigente v ON v.sq = c.sq
                        WHERE c.estado IN ('ACTIVO') OR c.sq IN (SELECT sq FROM programacion_casos WHERE fecha >= %s)
                           OR c.sq IN (SELECT sq FROM tiempos WHERE fecha >= %s)
                           OR (c.estado = 'REALIZADO' AND c.sq NOT IN (SELECT sq FROM facturas))""", (H - dt.timedelta(days=7), H - dt.timedelta(days=7)))
    sqs = [f["sq"] for f in filas]
    prep, ins, conf, ingr, chk, tiem, notas, canc = (defaultdict(dict), defaultdict(list), {}, defaultdict(dict), {}, {}, defaultdict(list), defaultdict(list))
    if sqs:
        for r in _q(conn, "SELECT * FROM preparacion WHERE sq = ANY(%s)", (sqs,)):
            prep[r["sq"]][r["item"]] = {"estado": r["estado"], "vence": _txt(r["vence"]), "responsable": r["responsable"]}
        for r in _q(conn, "SELECT * FROM insumos_caso WHERE sq = ANY(%s) ORDER BY id", (sqs,)):
            ins[r["sq"]].append({"id": r["id"], "tipo": r["tipo"], "descripcion": r["descripcion"], "estado": r["estado"],
                                 "vence": _txt(r["vence"]), "registro": r["registro"], "responsable": r["responsable"],
                                 "momento": _txt(r["momento"]), "costo": float(r["costo"])})
        for r in _q(conn, "SELECT * FROM confirmaciones WHERE sq = ANY(%s)", (sqs,)):
            conf[r["sq"]] = {"confirmado": r["confirmado"], "ayudante": r["ayudante"], "insumos_especiales": r["insumos_especiales"], "momento": _txt(r["momento"])}
        for r in _q(conn, "SELECT * FROM ingreso WHERE sq = ANY(%s)", (sqs,)):
            ingr[r["sq"]][r["item"]] = {"responsable": r["responsable"], "momento": _txt(r["momento"])}
        for r in _q(conn, "SELECT * FROM lista_chequeo WHERE sq = ANY(%s)", (sqs,)):
            chk.setdefault(r["sq"], {})[r["momento"]] = {"completo": r["completo"], "items": r["items"], "responsable": r["responsable"]}
        for r in _q(conn, "SELECT * FROM tiempos WHERE sq = ANY(%s)", (sqs,)):
            tiem[r["sq"]] = {k: (v.strftime("%H:%M") if isinstance(v, dt.time) else _txt(v)) for k, v in r.items() if k != "sq"}
        for r in _q(conn, "SELECT * FROM notas WHERE sq = ANY(%s) ORDER BY momento", (sqs,)):
            notas[r["sq"]].append({"autor": r["autor"], "texto": r["texto"], "momento": _txt(r["momento"])})
        for r in _q(conn, "SELECT * FROM cancelaciones WHERE sq = ANY(%s) ORDER BY momento", (sqs,)):
            canc[r["sq"]].append({"fecha": r["fecha"].isoformat(), "motivo": r["motivo"], "mismo_dia": r["mismo_dia"], "responsable": r["responsable"]})
    for f in filas:
        sq = f["sq"]
        maximo = int(P[f"max_espera_{f['prioridad']}"])
        espera = (H - f["fecha_solicitud"]).days
        casos[sq] = {
            "sq": sq, "procedimiento": f["procedimiento"], "procedimiento_nombre": f["procedimiento_nombre"], "especialidad": f["especialidad"],
            "tipo_cirugia": f["tipo_cirugia"], "duracion_min": f["duracion_min"], "equipo_requerido": f["equipo_requerido"],
            "requiere_implante": f["requiere_implante"], "requiere_sangre": f["requiere_sangre"],
            "cirujano": f["cirujano"], "prioridad": f["prioridad"], "requiere_uci": f["requiere_uci"],
            "id_aseguradora": f["id_aseguradora"], "aseguradora": f["aseguradora"], "fecha_solicitud": f["fecha_solicitud"].isoformat(),
            "solicitud_validada": f["solicitud_validada"], "estado": f["estado"], "observaciones": f["observaciones"],
            "hora_citacion": f["hora_citacion"].strftime("%H:%M") if f["hora_citacion"] else None,
            "dias_espera": espera, "max_espera": maximo, "atrasado": f["estado"] == "ACTIVO" and espera > maximo,
            "autorizacion": {"estado": f["aut_estado"], "numero": f["aut_numero"], "vigente_hasta": _txt(f["aut_vigente"])} if f["aut_estado"] else None,
            "preparacion": {k: prep[sq].get(k, {}).get("estado", "PENDIENTE") for k in ITEMS_PREP},
            "preparacion_detalle": prep[sq], "insumos": ins[sq], "confirmacion": conf.get(sq), "ingreso": ingr[sq],
            "chequeo": chk.get(sq, {}), "tiempos": tiem.get(sq), "notas": notas[sq], "cancelaciones": canc[sq],
            "realizado": f["estado"] == "REALIZADO", "costo_estimado": float(f["costo_estimado"]), "tarifa": float(f["tarifa"]),
        }
    return {"parametros": P, "hoy": H, "salas": salas, "equipos": equipos, "cirujanos": cirujanos, "novedades": novedades, "casos": casos}


def _slot_de(conn, ctx):
    """Asignación de cada caso: la aprobada manda; si no, la del borrador (propuesta)."""
    slots = {}
    for r in _q(conn, """SELECT pc.*, p.estado AS prog_estado, p.version, p.semana FROM programacion_casos pc
                         JOIN programaciones p ON p.id = pc.programacion_id
                         WHERE p.estado IN ('APROBADA','BORRADOR') ORDER BY p.version"""):
        sq = r["sq"]
        if sq not in ctx["casos"]:
            continue
        info = {"fecha": r["fecha"].isoformat(), "sala": r["id_sala"], "hora_inicio": r["hora_inicio"].strftime("%H:%M"),
                "hora_fin": r["hora_fin"].strftime("%H:%M"), "estado_programacion": r["prog_estado"], "version": r["version"],
                "semana": r["semana"].isoformat()}
        actual = slots.get(sq)
        if actual is None or r["prog_estado"] == "APROBADA" or actual["estado_programacion"] != "APROBADA":
            if not (actual and actual["estado_programacion"] == "APROBADA" and r["prog_estado"] == "BORRADOR"):
                slots[sq] = info
            else:
                actual["propuesta_nueva"] = info
    return slots


# ------------------------------------------------------------------ estado por etapas
def etapas(c, slot, factura, H):
    """Estado de cada una de las 10 etapas. estado: ok | pendiente | bloqueado | no_aplica | en_curso."""
    e = []
    validada = c["solicitud_validada"]
    e.append(("ok" if validada else "bloqueado" if validada is False else "pendiente",
              "Validada" if validada else "Rechazada" if validada is False else "Sin validar"))
    aut = c["autorizacion"]
    if aut and aut["estado"] == "APROBADA":
        e.append(("ok", f"Aprobada {aut['numero'] or ''}".strip()))
    elif aut and aut["estado"] == "NEGADA":
        e.append(("bloqueado", "Negada"))
    else:
        e.append(("en_curso" if c["prioridad"] == "P1" else "pendiente",
                  "En trámite (P1 se programa en paralelo)" if c["prioridad"] == "P1" else ("Pendiente" if aut else "Sin solicitar")))
    if slot and slot["estado_programacion"] == "APROBADA":
        e.append(("ok", f"{slot['fecha']} {slot['sala']} {slot['hora_inicio']}"))
    elif slot:
        e.append(("pendiente", f"Propuesta {slot['fecha']} {slot['sala']} {slot['hora_inicio']} · falta aprobar"))
    else:
        e.append(("pendiente", "Sin programar"))
    pend = [k for k, v in c["preparacion"].items() if v == "PENDIENTE"]
    cons = c["preparacion_detalle"].get("CONSENTIMIENTO_INFORMADO", {})
    fecha_cx = _f(slot["fecha"]) if slot else lunes(H) + dt.timedelta(days=7)
    cons_vencido = cons.get("vence") and fecha_cx and _f(cons["vence"]) < fecha_cx
    e.append(("bloqueado" if cons_vencido else "ok" if not pend else "pendiente",
              "Consentimiento vence antes de la cirugía" if cons_vencido else "Completa" if not pend else
              "Falta: " + ", ".join(nom(p) for p in pend)))
    falt = [i for i in c["insumos"] if i["estado"] == "FALTANTE" or (i["vence"] and fecha_cx and _f(i["vence"]) < fecha_cx)]
    pend_i = [i for i in c["insumos"] if i["estado"] == "PENDIENTE"]
    e.append(("bloqueado" if falt else "pendiente" if pend_i else "ok",
              ("Bloqueada: " + "; ".join(i["descripcion"] + (" (faltante)" if i["estado"] == "FALTANTE" else f" (vence {i['vence']})") for i in falt)) if falt
              else f"{len(pend_i)} por verificar" if pend_i else "Todo verificado"))
    cf = c["confirmacion"]
    e.append(("ok" if cf and cf["confirmado"] else "bloqueado" if cf and not cf["confirmado"] else "pendiente",
              "Confirmada" if cf and cf["confirmado"] else "El cirujano no confirmó" if cf else "Sin confirmar"))
    ing = c["ingreso"]
    falta_ing = [i for i in ITEMS_INGRESO if i not in ing]
    if "LISTO_PARA_SALA" in ing:
        e.append(("ok", "Listo para sala"))
    elif slot and fecha_cx == H:
        e.append(("pendiente", "Falta: " + ", ".join(nom(i) for i in falta_ing) if falta_ing else "Falta confirmar «Listo para sala»"))
    else:
        e.append(("pendiente", "El día de la cirugía"))
    t = c["tiempos"]
    ult = c["cancelaciones"][-1] if c["cancelaciones"] else None
    if ult and (not t or not t.get("salida")) and (not slot or ult["fecha"] == slot["fecha"]):
        e.append(("bloqueado", f"Cancelada el {ult['fecha']}: {ult['motivo'].replace('_', ' ').lower()}"))
    elif t and t.get("salida"):
        chk_ok = all(c["chequeo"].get(m, {}).get("completo") for m in ITEMS_CHEQUEO)
        e.append(("ok" if chk_ok else "pendiente", "Realizada" + ("" if chk_ok else " · lista de chequeo incompleta")))
    elif t and t.get("entrada"):
        e.append(("en_curso", "En sala"))
    else:
        e.append(("pendiente", "—"))
    e.append(("ok" if t and t.get("destino") else "pendiente", f"Destino: {t['destino'].lower()}" if t and t.get("destino") else "—"))
    if factura:
        e.append(("ok" if factura["estado"] in ("RADICADA", "PAGADA") else "pendiente", f"{factura['numero']} · {factura['estado'].lower()}"))
    else:
        e.append(("pendiente", "Sin facturar" if c["realizado"] else "—"))
    out = [{"n": i + 1, "etapa": ETAPAS[i], "responsable": RESPONSABLES[i], "estado": s, "detalle": d} for i, (s, d) in enumerate(e)]
    # etapa actual: la primera que no está ok (la autorización en trámite de un P1 no detiene el flujo)
    actual = next((x for x in out if x["estado"] not in ("ok", "no_aplica") and not (x["n"] == 2 and x["estado"] == "en_curso")), out[-1])
    return out, actual


def listo_para_sala(c, slot, H):
    """Etapas 2 a 7 completas, salvo P1 con autorización en trámite. Devuelve (bool, faltantes, nota)."""
    falt = []
    aut = c["autorizacion"]
    nota = None
    if not (aut and aut["estado"] == "APROBADA"):
        if c["prioridad"] == "P1" and not (aut and aut["estado"] == "NEGADA"):
            nota = "Lista para sala con autorización en trámite (P1)"
        else:
            falt.append("autorización aprobada")
    if not (slot and slot["estado_programacion"] == "APROBADA"):
        falt.append("programación aprobada")
    elif _f(slot["fecha"]) != H:
        falt.append(f"la cirugía es el {slot['fecha']}, no hoy")
    for k, v in c["preparacion"].items():
        if v == "PENDIENTE":
            falt.append("preparación: " + nom(k))
    cons = c["preparacion_detalle"].get("CONSENTIMIENTO_INFORMADO", {})
    if cons.get("vence") and _f(cons["vence"]) < H:
        falt.append("consentimiento informado vigente")
    for i in c["insumos"]:
        if i["estado"] != "OK":
            falt.append(f"insumo {i['estado'].lower()}: {i['descripcion']}")
        elif i["vence"] and _f(i["vence"]) < H:
            falt.append(f"insumo vencido: {i['descripcion']}")
    if not (c["confirmacion"] and c["confirmacion"]["confirmado"]):
        falt.append("confirmación del cirujano")
    for it in ITEMS_INGRESO:
        if it not in c["ingreso"]:
            falt.append("ingreso: " + nom(it))
    return (not falt), falt, nota


def soportes_de(c):
    """Soportes de cobro disponibles a partir de lo registrado (simplificación del Anexo 1 de la Res. 2284 de 2023)."""
    aut = c["autorizacion"]
    t = c["tiempos"] or {}
    chk = c["chequeo"]
    fecha_cx = _f(t["fecha"]) if t.get("fecha") else None
    cons = c["preparacion_detalle"].get("CONSENTIMIENTO_INFORMADO", {})
    implantes = [i for i in c["insumos"] if i["tipo"] == "IMPLANTE"]
    return {
        "autorizacion": bool(aut and aut["estado"] == "APROBADA" and aut["numero"]),
        "descripcion_quirurgica": bool(t.get("incision") and t.get("cierre") and chk.get("ANTES_SALIR", {}).get("completo")),
        "registro_anestesia": bool(c["preparacion"].get("VALORACION_PREANESTESICA") == "OK" and chk.get("ANTES_ANESTESIA", {}).get("completo")),
        "registro_insumos_e_implantes": all(i["estado"] == "OK" for i in c["insumos"]) and all(i["registro"] for i in implantes),
        "consentimiento_informado": c["preparacion"].get("CONSENTIMIENTO_INFORMADO") == "OK" and not (cons.get("vence") and fecha_cx and _f(cons["vence"]) < fecha_cx),
        "resumen_de_atencion": bool(t.get("destino")),
        "rips": True,  # supuesto: lo genera el sistema de facturación institucional
    }


CONCEPTO_POR_SOPORTE = {"autorizacion": "AU", "descripcion_quirurgica": "SO", "registro_anestesia": "SO",
                        "registro_insumos_e_implantes": "SO", "consentimiento_informado": "SO", "resumen_de_atencion": "SO", "rips": "SO"}


# ------------------------------------------------------------------ TABLERO (la lectura que usa el front)
def tablero(conn, rol, semana=None, cirujano=None):
    ctx = contexto(conn)
    H = ctx["hoy"]
    P = ctx["parametros"]
    fest = festivos(conn)
    sem = lunes(semana) if semana else lunes(H) + dt.timedelta(days=7)   # por defecto: la semana que se está programando
    fin = rol in ROLES_FINANCIEROS
    slots = _slot_de(conn, ctx)
    facturas = {r["sq"]: r for r in _q(conn, "SELECT * FROM facturas")}
    aprob, borr = programacion_vigente(conn, sem)
    casos = []
    revision = []
    for sq, c in ctx["casos"].items():
        if cirujano and c["cirujano"] != cirujano:
            continue
        slot = slots.get(sq)
        fac = facturas.get(sq)
        et, actual = etapas(c, slot, {"numero": fac["numero"], "estado": fac["estado"]} if fac else None, H)
        listo, falt, nota = listo_para_sala(c, slot, H) if slot else (False, ["programación"], None)
        bloqueos = [x["detalle"] for x in et if x["estado"] == "bloqueado"]
        item = {k: c[k] for k in ["sq", "procedimiento", "procedimiento_nombre", "especialidad", "tipo_cirugia", "duracion_min",
                                  "cirujano", "prioridad", "requiere_uci", "aseguradora", "fecha_solicitud", "estado", "hora_citacion",
                                  "dias_espera", "max_espera", "atrasado", "autorizacion", "preparacion", "insumos", "confirmacion",
                                  "ingreso", "chequeo", "tiempos", "notas", "cancelaciones", "requiere_sangre", "requiere_implante",
                                  "solicitud_validada", "observaciones"]}
        if not fin:
            item["insumos"] = [{k: v for k, v in i.items() if k != "costo"} for i in c["insumos"]]
        item["ingreso"] = sorted(c["ingreso"])
        item["chequeo"] = {m: v["completo"] for m, v in c["chequeo"].items()}
        if c["estado"] != "ACTIVO":
            item.pop("observaciones", None)
        item.update(programado=slot, etapas=[[x["estado"], x["detalle"]] for x in et], etapa_actual=actual["n"], etapa_actual_nombre=actual["etapa"],
                    responsable_siguiente=actual["responsable"], bloqueos=bloqueos, listo_para_sala=listo and "LISTO_PARA_SALA" in c["ingreso"],
                    puede_pasar_a_sala=listo, falta_para_sala=falt, nota_sala=nota)
        if fin:
            item["costo_estimado"] = c["costo_estimado"] + sum(i["costo"] for i in c["insumos"])
            item["tarifa"] = c["tarifa"]
            if c["realizado"]:
                sop = soportes_de(c)
                item["soportes"] = sop
                item["riesgo_glosa"] = [CONCEPTO_POR_SOPORTE[k] + " · falta " + k.replace("_", " ") for k, v in sop.items() if not v]
            if fac:
                item["factura"] = {"numero": fac["numero"], "valor": float(fac["valor"]), "estado": fac["estado"],
                                   "fecha_expedicion": fac["fecha_expedicion"].isoformat(),
                                   "fecha_radicacion": _txt(fac["fecha_radicacion"])}
        casos.append(item)
        # ---- Revisión activa: solo lo que necesita acción, con responsable y hora límite
        if c["estado"] != "ACTIVO":
            continue
        fcx = _f(slot["fecha"]) if slot else None
        dia_antes = dia_habil_anterior(fcx, fest) if fcx else None
        if dia_antes and dia_antes < H:        # la cirugía es hoy: el límite es antes de entrar a sala
            dia_antes = None
        lim = (lambda hora: f"{dia_antes.isoformat()} {hora}" if dia_antes else
               f"{fcx.isoformat()} {slot['hora_inicio']} (antes de entrar a sala)" if fcx else "Cierre semanal: jueves 12:00")
        proximo = fcx and H <= fcx <= H + dt.timedelta(days=10)
        if bloqueos and (proximo or not slot):
            revision.append({"urgencia": 1, "tipo": "Cirugía bloqueada", "sq": sq, "detalle": "; ".join(bloqueos),
                             "responsable": next(x["responsable"] for x in et if x["estado"] == "bloqueado"),
                             "hora_limite": lim("12:00") if fcx else "Antes de programar"})
        aut = c["autorizacion"]
        if not (aut and aut["estado"] == "APROBADA") and not (aut and aut["estado"] == "NEGADA"):
            revision.append({"urgencia": 1 if c["prioridad"] == "P1" or proximo else 2, "tipo": "Autorización pendiente", "sq": sq,
                             "detalle": f"{c['prioridad']} · {c['aseguradora']} · {'en trámite' if aut else 'sin solicitar'}",
                             "responsable": "Administrativo",
                             "hora_limite": lim("12:00")})
        if c["solicitud_validada"] is None:
            revision.append({"urgencia": 2, "tipo": "Solicitud sin validar", "sq": sq, "detalle": f"{c['procedimiento_nombre']} · {c['cirujano']}",
                             "responsable": "Administrativo", "hora_limite": "Cierre semanal: jueves 12:00"})
        if proximo:
            pend_i = [i for i in c["insumos"] if i["estado"] == "PENDIENTE"]
            if pend_i:
                revision.append({"urgencia": 2 if fcx > H + dt.timedelta(days=1) else 1, "tipo": "Insumos por verificar", "sq": sq,
                                 "detalle": ", ".join(i["descripcion"] for i in pend_i), "responsable": "Central de esterilización y almacén",
                                 "hora_limite": lim("16:00")})
            pend_p = [k for k, v in c["preparacion"].items() if v == "PENDIENTE"]
            if pend_p:
                revision.append({"urgencia": 2 if fcx > H + dt.timedelta(days=1) else 1, "tipo": "Preparación pendiente", "sq": sq,
                                 "detalle": ", ".join(nom(p) for p in pend_p), "responsable": "Enfermería y anestesia",
                                 "hora_limite": lim("18:00")})
            if not c["confirmacion"]:
                revision.append({"urgencia": 3, "tipo": "Confirmación del cirujano", "sq": sq, "detalle": f"{c['cirujano']} · {slot['fecha']} {slot['hora_inicio']}",
                                 "responsable": "Cirujano", "hora_limite": lim("18:00")})
        if fcx == H and "LISTO_PARA_SALA" not in c["ingreso"] and slot.get("estado_programacion") == "APROBADA":
            cit = c["hora_citacion"] or slot["hora_inicio"]
            revision.append({"urgencia": 1, "tipo": "Paciente sin ingreso", "sq": sq,
                             "detalle": f"Citado a las {cit}; faltan {len([i for i in ITEMS_INGRESO if i not in c['ingreso']])} ítems de ingreso",
                             "responsable": "Admisiones y enfermería", "hora_limite": f"{H.isoformat()} {slot['hora_inicio']}"})
        if c["atrasado"] and not slot:
            revision.append({"urgencia": 1 if c["prioridad"] == "P1" else 2, "tipo": "Atrasado sin programar", "sq": sq,
                             "detalle": f"{c['prioridad']} · {c['dias_espera']} días (máximo {c['max_espera']})",
                             "responsable": "Asistencial (coordinación de cirugía)", "hora_limite": "Cierre semanal: jueves 12:00"})
    alertas = _q(conn, "SELECT id, momento, rol, campo, sq, motivo, extracto FROM alertas_seguridad WHERE NOT revisada ORDER BY momento DESC")
    for a in alertas:
        revision.append({"urgencia": 1, "tipo": "Alerta de seguridad", "sq": a["sq"], "detalle": a["motivo"], "responsable": "TI",
                         "hora_limite": "Inmediato"})
    revision.sort(key=lambda x: (x["urgencia"], x["hora_limite"], x["sq"] or ""))
    prog = None
    vig = borr or aprob
    if vig:
        prog = {"semana": sem.isoformat(), "version": vig["version"], "estado": vig["estado"], "creada_por": vig["creada_por"],
                "aprobada_por": vig["aprobada_por"], "asignaciones": _asig(conn, vig["id"]), "verificacion": vig["verificacion"],
                "hay_aprobada": bool(aprob), "version_aprobada": aprob["version"] if aprob else None}
    salida = {
        "generado": dt.datetime.now(ZoneInfo("America/Bogota")).isoformat(timespec="seconds"), "rol": rol, "hoy": H.isoformat(),
        "semana": sem.isoformat(), "dias_semana": [d.isoformat() for d in dias_habiles(sem, fest)],
        "parametros": {k: v for k, v in P.items()}, "salas": list(ctx["salas"].values()), "equipos": ctx["equipos"],
        "cirujanos": list(ctx["cirujanos"].values()), "novedades": ctx["novedades"],
        "casos": sorted(casos, key=lambda x: ({"P1": 0, "P2": 1, "P3": 2}[x["prioridad"]], -x["dias_espera"])),
        "programacion": prog, "revision_activa": revision, "alertas_seguridad": len(alertas),
        "etapas": [{"n": i + 1, "etapa": ETAPAS[i], "responsable": RESPONSABLES[i]} for i in range(10)],
        "items_chequeo": ITEMS_CHEQUEO, "items_ingreso": ITEMS_INGRESO, "items_preparacion": ITEMS_PREP,
    }
    try:
        salida["indicadores"] = indicadores(conn)
    except Exception as e:  # los indicadores no deben tumbar el tablero
        salida["indicadores"] = {"error": str(e)}
    if fin:
        salida["glosas"] = glosas(conn, solo_abiertas=False)
    return salida


# ------------------------------------------------------------------ ASISTENCIAL: programación
def _guardar(conn, rol, semana, asignaciones, creada_por, observaciones=""):
    ctx = contexto(conn)
    sem = lunes(semana)
    fest = festivos(conn)
    dias = {d.isoformat() for d in dias_habiles(sem, fest)}
    fuera = [a for a in asignaciones if str(a.get("fecha"))[:10] not in dias]
    v = reglas.validar(ctx, asignaciones)
    for a in fuera:
        v["errores"].append({"sq": a.get("sq"), "regla": "Datos", "detalle": f"La fecha {a.get('fecha')} no es un día hábil de la semana del {sem.isoformat()}."})
        v["cumple"] = False
    aviso_txt = revisar_texto(conn, rol, "programaciones.observaciones", observaciones)
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM programaciones WHERE semana = %s", (sem,))
        version = cur.fetchone()[0]
        cur.execute("UPDATE programaciones SET estado = 'REEMPLAZADA' WHERE semana = %s AND estado = 'BORRADOR'", (sem,))
        resumen = {"cumple": v["cumple"], "errores": v["errores"], "avisos": v["avisos"]}
        cur.execute("""INSERT INTO programaciones (semana, version, estado, creada_por, verificacion, observaciones)
                       VALUES (%s, %s, 'BORRADOR', %s, %s::jsonb, %s) RETURNING id""",
                    (sem, version, creada_por, json.dumps(resumen, ensure_ascii=False), observaciones or None))
        pid = cur.fetchone()[0]
        for a in v["asignaciones"]:
            cur.execute("INSERT INTO programacion_casos VALUES (%s, %s, %s, %s, %s, %s)",
                        (pid, a["sq"], a["fecha"], a["sala"], a["hora_inicio"], a["hora_fin"]))
    auditar(conn, rol, "guardar_borrador", creada_por, None, {"semana": sem.isoformat(), "version": version, "casos": len(v["asignaciones"]),
                                                              "errores": len(v["errores"])})
    conn.commit()
    out = {"guardado": True, "semana": sem.isoformat(), "version": version, "estado": "BORRADOR", **v}
    out.pop("por_caso", None)
    if aviso_txt:
        out["aviso_seguridad"] = aviso_txt
    out["siguiente_paso"] = ("Cumple todas las reglas. Puede aprobarse con aprobar_programacion." if v["cumple"]
                             else "Tiene errores: corríjalos (mover_caso o guardar_programacion) antes de aprobar.")
    return out


def datos_para_programar(conn, semana):
    ctx = contexto(conn)
    sem = lunes(semana)
    fest = festivos(conn)
    aprob, borr = programacion_vigente(conn, sem)
    slots = _slot_de(conn, ctx)
    cand = []
    for c in sorted(ctx["casos"].values(), key=lambda c: ({"P1": 0, "P2": 1, "P3": 2}[c["prioridad"]], -c["dias_espera"])):
        if c["estado"] != "ACTIVO":
            continue
        s = slots.get(c["sq"])
        if s and _f(s["fecha"]) < sem:
            continue
        cand.append({k: c[k] for k in ["sq", "prioridad", "dias_espera", "max_espera", "atrasado", "procedimiento_nombre", "especialidad",
                                       "tipo_cirugia", "duracion_min", "equipo_requerido", "cirujano", "requiere_uci", "solicitud_validada"]}
                    | {"autorizacion": (c["autorizacion"] or {}).get("estado", "SIN REGISTRAR"),
                       "insumo_faltante": any(i["estado"] == "FALTANTE" for i in c["insumos"]),
                       "preparacion_pendiente": [k for k, v in c["preparacion"].items() if v == "PENDIENTE"],
                       "programado": s})
    return {"semana": sem.isoformat(), "dias_habiles": [d.isoformat() for d in dias_habiles(sem, fest)], "hoy": ctx["hoy"].isoformat(),
            "reglas": reglas.__doc__, "parametros": ctx["parametros"], "salas": list(ctx["salas"].values()), "equipos": ctx["equipos"],
            "cirujanos": list(ctx["cirujanos"].values()), "novedades": ctx["novedades"], "candidatos": cand,
            "programacion_aprobada": {"version": aprob["version"], "asignaciones": _asig(conn, aprob["id"])} if aprob else None,
            "borrador": {"version": borr["version"], "asignaciones": _asig(conn, borr["id"])} if borr else None}


def proponer_programacion(conn, semana, creada_por, rol="ASISTENCIAL"):
    ctx = contexto(conn)
    sem = lunes(semana)
    aprob, _ = programacion_vigente(conn, sem)
    fijas = [{k: a[k] for k in ("sq", "fecha", "sala", "hora_inicio")} for a in _asig(conn, aprob["id"])] if aprob else []
    # Los casos de semanas anteriores ya programados no se vuelven a proponer
    otros = {r["sq"] for r in _q(conn, """SELECT pc.sq FROM programacion_casos pc JOIN programaciones p ON p.id = pc.programacion_id
                                          WHERE p.estado = 'APROBADA' AND p.semana <> %s AND pc.fecha >= %s""", (sem, ctx["hoy"]))}
    for sq in otros:
        ctx["casos"].pop(sq, None)
    plan, omitidos = reglas.proponer(ctx, dias_habiles(sem, festivos(conn)), fijas)
    out = _guardar(conn, rol, sem, plan, creada_por, "Propuesta automática por prioridad y días de espera")
    out["omitidos"] = omitidos
    return out


def guardar_programacion(conn, semana, asignaciones, creada_por, observaciones="", rol="ASISTENCIAL"):
    return _guardar(conn, rol, semana, asignaciones, creada_por, observaciones)


def mover_caso(conn, semana, sq, cambiado_por, fecha="", sala="", hora_inicio="", quitar=False, rol="ASISTENCIAL"):
    sem = lunes(semana)
    sq = caso_existe(conn, sq)["sq"]
    aprob, borr = programacion_vigente(conn, sem)
    base = borr or aprob
    asig = [{k: a[k] for k in ("sq", "fecha", "sala", "hora_inicio")} for a in _asig(conn, base["id"])] if base else []
    asig = [a for a in asig if a["sq"] != sq]
    if not quitar:
        if not (fecha and sala and hora_inicio):
            raise ValueError("Para mover o agregar un caso indique fecha (AAAA-MM-DD), sala (Q1…Q6) y hora_inicio (HH:MM).")
        asig.append({"sq": sq, "fecha": fecha, "sala": sala.upper(), "hora_inicio": hora_inicio})
    out = _guardar(conn, rol, sem, asig, cambiado_por, f"{'Quitó' if quitar else 'Movió'} {sq}")
    auditar(conn, rol, "quitar_caso" if quitar else "mover_caso", cambiado_por, sq, {"fecha": fecha, "sala": sala, "hora_inicio": hora_inicio})
    conn.commit()
    caso_err = [e for e in out["errores"] if e["sq"] == sq]
    out["resultado_del_caso"] = {"sq": sq, "errores": caso_err, "cumple": not caso_err}
    return out


def ver_programacion(conn, semana, version=None):
    sem = lunes(semana)
    hist = _q(conn, "SELECT id, version, estado, creada_por, creada, aprobada_por, aprobada, observaciones FROM programaciones WHERE semana = %s ORDER BY version", (sem,))
    if not hist:
        return {"semana": sem.isoformat(), "mensaje": "No hay programación para esta semana. Use proponer_programacion o guardar_programacion."}
    if version:
        p = _uno(conn, "SELECT * FROM programaciones WHERE semana = %s AND version = %s", (sem, version))
    else:
        aprob, borr = programacion_vigente(conn, sem)
        p = borr or aprob or _uno(conn, "SELECT * FROM programaciones WHERE semana = %s ORDER BY version DESC LIMIT 1", (sem,))
    asig = _asig(conn, p["id"])
    ctx = contexto(conn)
    for a in asig:
        c = ctx["casos"].get(a["sq"])
        if c:
            a.update(prioridad=c["prioridad"], cirujano=c["cirujano"], procedimiento=c["procedimiento_nombre"], uci=c["requiere_uci"])
    v = reglas.validar(ctx, [{k: a[k] for k in ("sq", "fecha", "sala", "hora_inicio")} for a in asig])
    v.pop("por_caso", None)
    v.pop("asignaciones", None)
    return {"semana": sem.isoformat(), "version": p["version"], "estado": p["estado"], "creada_por": p["creada_por"],
            "aprobada_por": p["aprobada_por"], "asignaciones": asig, "verificacion_actual": v,
            "historial": [{**h, "creada": _txt(h["creada"]), "aprobada": _txt(h["aprobada"])} for h in hist]}


def aprobar_programacion(conn, semana, aprobada_por, rol="ASISTENCIAL"):
    sem = lunes(semana)
    aprob, borr = programacion_vigente(conn, sem)
    if not borr:
        return {"aprobada": False, "mensaje": "No hay un borrador pendiente de aprobación para esta semana."}
    ctx = contexto(conn)
    asig = _asig(conn, borr["id"])
    v = reglas.validar(ctx, [{k: a[k] for k in ("sq", "fecha", "sala", "hora_inicio")} for a in asig])
    if not v["cumple"]:
        return {"aprobada": False, "mensaje": "El borrador incumple reglas de POL-QX-002 y no se puede aprobar. Corrija los errores.",
                "errores": v["errores"]}
    cit = int(ctx["parametros"]["citacion_min_antes"])
    with conn.cursor() as cur:
        if aprob:
            cur.execute("UPDATE programaciones SET estado = 'REEMPLAZADA' WHERE id = %s", (aprob["id"],))
        cur.execute("UPDATE programaciones SET estado = 'APROBADA', aprobada_por = %s, aprobada = now(), verificacion = %s::jsonb WHERE id = %s",
                    (aprobada_por, json.dumps({"cumple": True, "errores": [], "avisos": v["avisos"]}, ensure_ascii=False), borr["id"]))
        for a in asig:
            cur.execute("UPDATE casos SET hora_citacion = %s WHERE sq = %s", (reglas.a_hora(max(reglas.a_min(a["hora_inicio"]) - cit, 5 * 60)), a["sq"]))
    auditar(conn, rol, "aprobar_programacion", aprobada_por, None, {"semana": sem.isoformat(), "version": borr["version"], "casos": len(asig)})
    conn.commit()
    return {"aprobada": True, "semana": sem.isoformat(), "version": borr["version"], "casos": len(asig), "avisos": v["avisos"],
            "mensaje": f"Programación aprobada. Se fijó la hora de citación ({cit} minutos antes). Las áreas ya ven sus pendientes en el tablero."}


# ------------------------------------------------------------------ ASISTENCIAL: preparación, ingreso, sala, egreso
def registrar_preparacion(conn, sq, item, estado, responsable, vence="", rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    item, estado = item.upper().strip(), estado.upper().strip()
    if item not in ITEMS_PREP:
        raise ValueError(f"item debe ser uno de {ITEMS_PREP}.")
    if estado not in ("OK", "PENDIENTE", "NO_APLICA"):
        raise ValueError("estado debe ser OK, PENDIENTE o NO_APLICA.")
    if estado == "OK" and not responsable:
        raise ValueError("Para marcar OK indique el cargo del responsable que lo confirma.")
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO preparacion (sq, item, estado, vence, responsable, momento) VALUES (%s,%s,%s,%s,%s, now())
                       ON CONFLICT (sq, item) DO UPDATE SET estado = EXCLUDED.estado, vence = EXCLUDED.vence,
                       responsable = EXCLUDED.responsable, momento = now()""", (c["sq"], item, estado, vence or None, responsable))
    auditar(conn, rol, "registrar_preparacion", responsable, c["sq"], {"item": item, "estado": estado, "vence": vence})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "item": item, "estado": estado}


def registrar_ingreso(conn, sq, items, responsable, rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    if not responsable:
        raise ValueError("Indique el cargo de quien registra el ingreso.")
    items = [i.upper().strip() for i in items]
    malos = [i for i in items if i not in ITEMS_INGRESO]
    if malos:
        raise ValueError(f"Ítems no válidos: {malos}. Use {ITEMS_INGRESO}. «Listo para sala» se marca con confirmar_listo_para_sala.")
    with conn.cursor() as cur:
        for i in items:
            cur.execute("INSERT INTO ingreso (sq, item, responsable) VALUES (%s,%s,%s) ON CONFLICT (sq, item) DO UPDATE SET responsable = EXCLUDED.responsable, momento = now()",
                        (c["sq"], i, responsable))
    auditar(conn, rol, "registrar_ingreso", responsable, c["sq"], {"items": items})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "items": items}


def confirmar_listo_para_sala(conn, sq, responsable, rol="ASISTENCIAL"):
    ctx = contexto(conn)
    c = ctx["casos"].get(caso_existe(conn, sq)["sq"])
    slot = _slot_de(conn, ctx).get(c["sq"])
    ok, falt, nota = listo_para_sala(c, slot, ctx["hoy"])
    if not ok:
        auditar(conn, rol, "listo_para_sala_rechazado", responsable, c["sq"], {"faltan": falt})
        conn.commit()
        return {"listo": False, "sq": c["sq"], "faltan": falt,
                "mensaje": "No pasa a «Listo para sala»: las etapas 2 a 7 deben estar completas (salvo P1 con autorización en trámite)."}
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ingreso (sq, item, responsable) VALUES (%s,'LISTO_PARA_SALA',%s) ON CONFLICT (sq, item) DO NOTHING", (c["sq"], responsable))
    auditar(conn, rol, "listo_para_sala", responsable, c["sq"], {"nota": nota})
    conn.commit()
    return {"listo": True, "sq": c["sq"], "estado": nota or "Listo para sala"}


def registrar_lista_chequeo(conn, sq, momento, items, responsable, rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    momento = momento.upper().strip()
    if momento not in ITEMS_CHEQUEO:
        raise ValueError(f"momento debe ser {list(ITEMS_CHEQUEO)}.")
    esperados = ITEMS_CHEQUEO[momento]
    faltan = [i for i in esperados if i not in items]
    marc = {i: bool(items.get(i)) for i in esperados}
    completo = all(marc.values())
    if momento != "ANTES_ANESTESIA" and not _uno(conn, "SELECT 1 FROM lista_chequeo WHERE sq=%s AND momento='ANTES_ANESTESIA'", (c["sq"],)):
        raise ValueError("Primero se registra el momento ANTES_ANESTESIA.")
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO lista_chequeo (sq, momento, items, completo, responsable) VALUES (%s,%s,%s::jsonb,%s,%s)
                       ON CONFLICT (sq, momento) DO UPDATE SET items = EXCLUDED.items, completo = EXCLUDED.completo,
                       responsable = EXCLUDED.responsable, registrado = now()""", (c["sq"], momento, json.dumps(marc), completo, responsable))
    auditar(conn, rol, "lista_chequeo", responsable, c["sq"], {"momento": momento, "completo": completo})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "momento": momento, "completo": completo,
            "no_cumplidos": [k for k, v in marc.items() if not v], "items_no_enviados": faltan,
            "items_del_momento": esperados}


def registrar_tiempos(conn, sq, responsable, entrada="", incision="", cierre="", salida="", rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    ctx = contexto(conn)
    slot = _slot_de(conn, ctx).get(c["sq"])
    fecha = slot["fecha"] if slot else ctx["hoy"].isoformat()
    previo = _uno(conn, "SELECT * FROM tiempos WHERE sq = %s", (c["sq"],)) or {}
    vals = {k: (v or (previo.get(k).strftime("%H:%M") if previo.get(k) else None)) for k, v in
            {"entrada": entrada, "incision": incision, "cierre": cierre, "salida": salida}.items()}
    orden = [vals[k] for k in ("entrada", "incision", "cierre", "salida") if vals[k]]
    if orden != sorted(orden, key=reglas.a_min):
        raise ValueError("Las horas deben ir en orden: entrada ≤ incisión ≤ cierre ≤ salida.")
    if vals["entrada"] and "LISTO_PARA_SALA" not in ctx["casos"][c["sq"]]["ingreso"]:
        raise ValueError("El paciente no está en «Listo para sala»; no se puede registrar la entrada.")
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO tiempos (sq, fecha, id_sala, entrada, incision, cierre, salida, registrado_por) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (sq) DO UPDATE SET entrada = EXCLUDED.entrada, incision = EXCLUDED.incision, cierre = EXCLUDED.cierre,
                       salida = EXCLUDED.salida, registrado_por = EXCLUDED.registrado_por""",
                    (c["sq"], fecha, slot["sala"] if slot else None, vals["entrada"], vals["incision"], vals["cierre"], vals["salida"], responsable))
    auditar(conn, rol, "registrar_tiempos", responsable, c["sq"], vals)
    conn.commit()
    return {"registrado": True, "sq": c["sq"], **vals}


def registrar_egreso(conn, sq, destino, responsable, rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    destino = destino.upper().strip()
    if destino not in ("CASA", "HOSPITALIZACION", "UCI"):
        raise ValueError("destino debe ser CASA, HOSPITALIZACION o UCI.")
    t = _uno(conn, "SELECT * FROM tiempos WHERE sq = %s", (c["sq"],))
    if not t or not t["salida"]:
        raise ValueError("Registre primero la hora de salida de sala (registrar_tiempos).")
    with conn.cursor() as cur:
        cur.execute("UPDATE tiempos SET destino = %s WHERE sq = %s", (destino, c["sq"]))
        cur.execute("UPDATE casos SET estado = 'REALIZADO' WHERE sq = %s", (c["sq"],))
    auditar(conn, rol, "registrar_egreso", responsable, c["sq"], {"destino": destino})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "destino": destino, "estado": "REALIZADO", "siguiente": "Facturación (rol contable)."}


def cancelar_cirugia(conn, sq, motivo, responsable, mismo_dia=True, detalle="", vuelve_a_lista=True, rol="ASISTENCIAL"):
    c = caso_existe(conn, sq)
    motivo = motivo.upper().strip()
    aviso = revisar_texto(conn, rol, "cancelaciones.detalle", detalle, c["sq"])
    ctx = contexto(conn)
    slot = _slot_de(conn, ctx).get(c["sq"])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO cancelaciones (sq, fecha, motivo, mismo_dia, detalle, responsable) VALUES (%s,%s,%s,%s,%s,%s)",
                    (c["sq"], slot["fecha"] if slot else ctx["hoy"], motivo, mismo_dia, detalle or None, responsable))
        cur.execute("UPDATE casos SET estado = %s WHERE sq = %s", ("ACTIVO" if vuelve_a_lista else "CANCELADO", c["sq"]))
    auditar(conn, rol, "cancelar_cirugia", responsable, c["sq"], {"motivo": motivo, "mismo_dia": mismo_dia, "vuelve_a_lista": vuelve_a_lista})
    conn.commit()
    out = {"cancelada": True, "sq": c["sq"], "motivo": motivo, "vuelve_a_lista": vuelve_a_lista}
    if slot and vuelve_a_lista:
        mover_caso(conn, slot["semana"], c["sq"], responsable, quitar=True, rol=rol)
        out["nota"] = "Volvió a la lista de espera y se retiró de la programación en un nuevo borrador. Apruebe el borrador para que quede vigente."
    if aviso:
        out["aviso_seguridad"] = aviso
    return out


# ------------------------------------------------------------------ CIRUJANO
def catalogo(conn):
    return {"procedimientos": [{**p, "costo_estimado": None, "tarifa": None} for p in _q(conn, "SELECT * FROM procedimientos ORDER BY codigo")],
            "cirujanos": list(contexto(conn)["cirujanos"].values()),
            "aseguradoras": _q(conn, "SELECT * FROM aseguradoras ORDER BY id_aseguradora")}


def registrar_solicitud(conn, cirujano, procedimiento, prioridad, id_aseguradora, observaciones="", requiere_uci=None, rol="CIRUJANO"):
    cj = _uno(conn, "SELECT * FROM cirujanos WHERE codigo = %s AND activo", (cirujano.upper(),))
    if not cj:
        raise ValueError(f"No existe el cirujano {cirujano}. Use su código (CIR-01…).")
    p = _uno(conn, "SELECT * FROM procedimientos WHERE codigo = %s", (procedimiento.upper(),))
    if not p:
        raise ValueError(f"No existe el procedimiento {procedimiento}. Consulte el catálogo.")
    if p["especialidad"] != cj["especialidad"]:
        raise ValueError(f"{p['nombre']} es de {p['especialidad']}; {cj['codigo']} es de {cj['especialidad']}.")
    prioridad = prioridad.upper()
    if prioridad not in ("P1", "P2", "P3"):
        raise ValueError("prioridad debe ser P1, P2 o P3.")
    if not _uno(conn, "SELECT 1 FROM aseguradoras WHERE id_aseguradora = %s", (id_aseguradora.upper(),)):
        raise ValueError("Aseguradora no válida. Consulte el catálogo.")
    aviso = revisar_texto(conn, rol, "casos.observaciones", observaciones)
    uci = p["tipo_cirugia"] == "UCI" if requiere_uci is None else bool(requiere_uci)
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(substring(sq from 4)::int), 900) + 1 FROM casos")
        sq = f"SQ-{cur.fetchone()[0]:04d}"
        cur.execute("""INSERT INTO casos (sq, procedimiento, cirujano, prioridad, requiere_uci, id_aseguradora, fecha_solicitud, observaciones)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (sq, p["codigo"], cj["codigo"], prioridad, uci, id_aseguradora.upper(), hoy(conn), observaciones or None))
        for it in ITEMS_PREP:
            aplica = (it != "RESERVA_SANGRE" or p["requiere_sangre"]) and (it != "CAMA_UCI" or uci)
            cur.execute("INSERT INTO preparacion (sq, item, estado) VALUES (%s,%s,%s)", (sq, it, "PENDIENTE" if aplica else "NO_APLICA"))
        filas = [("SET_INSTRUMENTAL", p["set_instrumental"], 180000), ("MATERIAL", "Paquete de material quirúrgico estándar", 350000)]
        if p["equipo_requerido"]:
            eq = _uno(conn, "SELECT * FROM equipos_biomedicos WHERE tipo = %s", (p["equipo_requerido"],))
            filas.append(("EQUIPO_BIOMEDICO", f"{eq['descripcion']} ({eq['id_equipo']})", 0))
        if p["requiere_implante"]:
            filas.append(("IMPLANTE", "Implante / osteosíntesis según planeación", int(float(p["costo_estimado"]) * 0.35)))
        for t, d, costo in filas:
            cur.execute("INSERT INTO insumos_caso (sq, tipo, descripcion, costo) VALUES (%s,%s,%s,%s)", (sq, t, d, costo))
    auditar(conn, rol, "registrar_solicitud", cj["codigo"], sq, {"procedimiento": p["codigo"], "prioridad": prioridad})
    conn.commit()
    out = {"registrada": True, "sq": sq, "procedimiento": p["nombre"], "prioridad": prioridad, "requiere_uci": uci,
           "siguiente": "El área administrativa valida la solicitud y tramita la autorización."}
    if aviso:
        out["aviso_seguridad"] = aviso
    return out


def mis_casos(conn, cirujano):
    t = tablero(conn, "CIRUJANO", cirujano=cirujano.upper())
    return {"cirujano": cirujano.upper(), "hoy": t["hoy"],
            "casos": [{k: c[k] for k in ("sq", "procedimiento_nombre", "prioridad", "dias_espera", "atrasado", "programado", "etapa_actual_nombre",
                                         "responsable_siguiente", "bloqueos", "falta_para_sala", "confirmacion", "notas")}
                      for c in t["casos"] if c["estado"] == "ACTIVO"]}


def confirmar_caso(conn, sq, cirujano, confirmado=True, ayudante="", insumos_especiales="", nota="", rol="CIRUJANO"):
    c = caso_existe(conn, sq)
    if c["cirujano"] != cirujano.upper():
        raise ValueError(f"{c['sq']} es de {c['cirujano']}; solo su cirujano puede confirmarlo.")
    aviso = revisar_texto(conn, rol, "confirmaciones.insumos_especiales", insumos_especiales, c["sq"]) or revisar_texto(conn, rol, "notas.texto", nota, c["sq"])
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO confirmaciones (sq, confirmado, ayudante, insumos_especiales) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (sq) DO UPDATE SET confirmado = EXCLUDED.confirmado, ayudante = EXCLUDED.ayudante,
                       insumos_especiales = EXCLUDED.insumos_especiales, momento = now()""", (c["sq"], confirmado, ayudante or None, insumos_especiales or None))
        if insumos_especiales:
            cur.execute("SELECT 1 FROM insumos_caso WHERE sq = %s AND tipo = 'INSUMO_ESPECIAL' AND descripcion = %s", (c["sq"], insumos_especiales))
            if not cur.fetchone():
                cur.execute("INSERT INTO insumos_caso (sq, tipo, descripcion) VALUES (%s, 'INSUMO_ESPECIAL', %s)", (c["sq"], insumos_especiales))
        if nota:
            cur.execute("INSERT INTO notas (sq, autor, texto) VALUES (%s,%s,%s)", (c["sq"], c["cirujano"], nota))
    auditar(conn, rol, "confirmar_caso", c["cirujano"], c["sq"], {"confirmado": confirmado, "insumos_especiales": insumos_especiales})
    conn.commit()
    out = {"registrado": True, "sq": c["sq"], "confirmado": confirmado}
    if insumos_especiales:
        out["nota"] = "El insumo especial quedó como PENDIENTE para la central de esterilización y el almacén."
    if aviso:
        out["aviso_seguridad"] = aviso
    return out


def dejar_nota(conn, sq, autor, texto_nota, rol):
    c = caso_existe(conn, sq)
    aviso = revisar_texto(conn, rol, "notas.texto", texto_nota, c["sq"])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO notas (sq, autor, texto) VALUES (%s,%s,%s)", (c["sq"], autor, texto_nota))
    auditar(conn, rol, "dejar_nota", autor, c["sq"], {})
    conn.commit()
    return {"registrada": True, "sq": c["sq"], **({"aviso_seguridad": aviso} if aviso else {})}


# ------------------------------------------------------------------ ADMINISTRATIVO
def validar_solicitud(conn, sq, valida, responsable, motivo="", rol="ADMINISTRATIVO"):
    c = caso_existe(conn, sq)
    aviso = revisar_texto(conn, rol, "casos.validacion", motivo, c["sq"])
    with conn.cursor() as cur:
        cur.execute("UPDATE casos SET solicitud_validada = %s, validada_por = %s, estado = %s WHERE sq = %s",
                    (valida, responsable, "ACTIVO" if valida else "RECHAZADO", c["sq"]))
    auditar(conn, rol, "validar_solicitud", responsable, c["sq"], {"valida": valida, "motivo": motivo})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "validada": valida, **({"aviso_seguridad": aviso} if aviso else {})}


def autorizaciones_pendientes(conn):
    t = tablero(conn, "ADMINISTRATIVO")
    out = []
    for c in t["casos"]:
        if c["estado"] != "ACTIVO":
            continue
        a = c["autorizacion"]
        if not a or a["estado"] != "APROBADA":
            accion = ("Tramitar en paralelo: P1 se programa igual" if c["prioridad"] == "P1" else
                      "Revisar el motivo de la negación con el cirujano y la aseguradora" if a and a["estado"] == "NEGADA" else
                      "Solicitar o hacer seguimiento a la aseguradora")
            out.append({"sq": c["sq"], "prioridad": c["prioridad"], "dias_espera": c["dias_espera"], "atrasado": c["atrasado"],
                        "aseguradora": c["aseguradora"], "procedimiento": c["procedimiento_nombre"], "estado": a["estado"] if a else "SIN REGISTRAR",
                        "programado": c["programado"], "accion_sugerida": accion})
    return {"hoy": t["hoy"], "pendientes": out}


def registrar_autorizacion(conn, sq, estado, responsable, numero="", vigente_hasta="", observacion="", rol="ADMINISTRATIVO"):
    c = caso_existe(conn, sq)
    estado = estado.upper().strip()
    if estado not in ("APROBADA", "PENDIENTE", "NEGADA"):
        raise ValueError("estado debe ser APROBADA, PENDIENTE o NEGADA.")
    if estado == "APROBADA" and not numero:
        raise ValueError("Una autorización aprobada necesita el número que dio la aseguradora.")
    aviso = revisar_texto(conn, rol, "autorizaciones.observacion", observacion, c["sq"])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO autorizaciones (sq, estado, numero, vigente_hasta, registrada_por, observacion) VALUES (%s,%s,%s,%s,%s,%s)",
                    (c["sq"], estado, numero or None, vigente_hasta or None, responsable, observacion or None))
    auditar(conn, rol, "registrar_autorizacion", responsable, c["sq"], {"estado": estado, "numero": numero})
    conn.commit()
    return {"registrado": True, "sq": c["sq"], "estado": estado, **({"aviso_seguridad": aviso} if aviso else {})}


def pacientes_por_citar(conn, fecha):
    t = tablero(conn, "ADMINISTRATIVO", semana=fecha)
    f = _f(fecha).isoformat()
    return {"fecha": f, "pacientes": [{"sq": c["sq"], "hora_citacion": c["hora_citacion"], "hora_cirugia": c["programado"]["hora_inicio"],
                                       "sala": c["programado"]["sala"], "prioridad": c["prioridad"], "autorizacion": (c["autorizacion"] or {}).get("estado")}
                                      for c in t["casos"] if c["programado"] and c["programado"]["fecha"] == f and c["programado"]["estado_programacion"] == "APROBADA"]}


# ------------------------------------------------------------------ INSUMOS
def insumos_por_verificar(conn, desde="", hasta=""):
    t = tablero(conn, "INSUMOS")
    H = _f(t["hoy"])
    d = _f(desde) if desde else H
    h = _f(hasta) if hasta else H + dt.timedelta(days=10)
    out = []
    for c in t["casos"]:
        p = c["programado"]
        if c["estado"] != "ACTIVO" or not p or not (d <= _f(p["fecha"]) <= h):
            continue
        for i in c["insumos"]:
            vencido = i["vence"] and _f(i["vence"]) < _f(p["fecha"])
            if i["estado"] != "OK" or vencido:
                out.append({"id_insumo": i["id"], "sq": c["sq"], "fecha_cirugia": p["fecha"], "sala": p["sala"], "hora": p["hora_inicio"],
                            "tipo": i["tipo"], "descripcion": i["descripcion"], "estado": i["estado"], "vence": i["vence"],
                            "problema": "vence antes de la cirugía" if vencido else None})
    return {"desde": d.isoformat(), "hasta": h.isoformat(), "por_verificar": sorted(out, key=lambda x: (x["fecha_cirugia"], x["hora"]))}


def verificar_insumo(conn, id_insumo, estado, responsable, vence="", registro="", rol="INSUMOS"):
    i = _uno(conn, "SELECT * FROM insumos_caso WHERE id = %s", (int(id_insumo),))
    if not i:
        raise ValueError(f"No existe el insumo {id_insumo}.")
    estado = estado.upper().strip()
    if estado not in ("OK", "PENDIENTE", "FALTANTE"):
        raise ValueError("estado debe ser OK, PENDIENTE o FALTANTE.")
    if estado == "OK" and not responsable:
        raise ValueError("Para marcar OK indique el cargo de quien lo verificó.")
    if estado == "OK" and i["tipo"] in ("SET_INSTRUMENTAL", "MATERIAL", "IMPLANTE") and not (vence or i["vence"]):
        raise ValueError("Para marcar OK un set, material o implante indique la fecha de vencimiento (esterilización o producto).")
    if estado == "OK" and i["tipo"] == "IMPLANTE" and not (registro or i["registro"]):
        raise ValueError("Para marcar OK un implante indique el lote o registro (sticker).")
    with conn.cursor() as cur:
        cur.execute("UPDATE insumos_caso SET estado = %s, vence = COALESCE(%s, vence), registro = COALESCE(%s, registro), responsable = %s, momento = now() WHERE id = %s",
                    (estado, vence or None, registro or None, responsable, i["id"]))
    auditar(conn, rol, "verificar_insumo", responsable, i["sq"], {"id": i["id"], "estado": estado, "vence": vence, "registro": registro})
    conn.commit()
    return {"registrado": True, "id_insumo": i["id"], "sq": i["sq"], "estado": estado,
            **({"bloquea": "Un insumo FALTANTE bloquea la cirugía hasta que se resuelva (R7)."} if estado == "FALTANTE" else {})}


def registrar_novedad_recurso(conn, fecha, tipo, recurso, descripcion, registrada_por, rol):
    tipo = tipo.upper()
    if tipo not in ("EQUIPO", "SALA", "CIRUJANO"):
        raise ValueError("tipo debe ser EQUIPO, SALA o CIRUJANO.")
    aviso = revisar_texto(conn, rol, "novedades_recursos.descripcion", descripcion)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO novedades_recursos (fecha, tipo, recurso, descripcion, registrada_por) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (fecha, tipo, recurso.upper(), descripcion, registrada_por))
        nid = cur.fetchone()[0]
    auditar(conn, rol, "registrar_novedad_recurso", registrada_por, None, {"fecha": fecha, "tipo": tipo, "recurso": recurso})
    conn.commit()
    ctx = contexto(conn)
    afectados = [sq for sq, s in _slot_de(conn, ctx).items() if s["fecha"] == str(fecha)[:10] and
                 (s["sala"] == recurso.upper() or ctx["casos"][sq]["cirujano"] == recurso.upper() or
                  any(e["id_equipo"] == recurso.upper() and e["id_sala"] == s["sala"] and e["tipo"] == ctx["casos"][sq]["equipo_requerido"] for e in ctx["equipos"]))]
    return {"registrada": True, "id": nid, "casos_afectados": afectados, **({"aviso_seguridad": aviso} if aviso else {}),
            **({"accion": "Hay cirugías programadas afectadas: la coordinación debe moverlas."} if afectados else {})}


# ------------------------------------------------------------------ CONTABLE: facturación y glosas
def cirugias_por_facturar(conn):
    ctx = contexto(conn)
    facts = {r["sq"] for r in _q(conn, "SELECT sq FROM facturas")}
    out = []
    for c in ctx["casos"].values():
        if c["realizado"] and c["sq"] not in facts:
            sop = soportes_de(c)
            falt = [k for k, v in sop.items() if not v]
            out.append({"sq": c["sq"], "fecha": (c["tiempos"] or {}).get("fecha"), "procedimiento": c["procedimiento_nombre"], "aseguradora": c["aseguradora"],
                        "tarifa": c["tarifa"], "costo_estimado": c["costo_estimado"] + sum(i["costo"] for i in c["insumos"]),
                        "soportes": sop, "riesgo_glosa": "ALTO" if falt else "BAJO",
                        "glosas_probables": [f"{CONCEPTO_POR_SOPORTE[k]} ({CONCEPTOS_GLOSA[CONCEPTO_POR_SOPORTE[k]]}): falta {k.replace('_', ' ')}" for k in falt]})
    return {"por_facturar": sorted(out, key=lambda x: x["fecha"] or "")}


def registrar_factura(conn, sq, numero, fecha_expedicion, responsable, valor=None, rol="CONTABLE"):
    ctx = contexto(conn)
    c = ctx["casos"].get(caso_existe(conn, sq)["sq"])
    if not c or not c["realizado"]:
        raise ValueError("Solo se facturan cirugías realizadas (con egreso registrado).")
    if _uno(conn, "SELECT 1 FROM facturas WHERE sq = %s", (c["sq"],)):
        raise ValueError(f"{c['sq']} ya tiene factura.")
    sop = soportes_de(c)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO facturas (numero, sq, id_aseguradora, valor, fecha_expedicion, soportes, registrada_por)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""", (numero.upper(), c["sq"], c["id_aseguradora"], valor or c["tarifa"], fecha_expedicion, json.dumps(sop), responsable))
    auditar(conn, rol, "registrar_factura", responsable, c["sq"], {"numero": numero, "valor": valor or c["tarifa"]})
    conn.commit()
    falt = [k for k, v in sop.items() if not v]
    fest = festivos(conn)
    return {"registrada": True, "numero": numero.upper(), "sq": c["sq"], "valor": valor or c["tarifa"], "soportes": sop,
            "radicar_a_mas_tardar": sumar_habiles(fecha_expedicion, int(ctx["parametros"]["plazo_radicacion_soportes"]), fest).isoformat(),
            **({"riesgo_glosa": "ALTO: completar " + ", ".join(f.replace("_", " ") for f in falt) + " antes de radicar."} if falt else {})}


def radicar_factura(conn, numero, fecha_radicacion, responsable, rol="CONTABLE"):
    f = _uno(conn, "SELECT * FROM facturas WHERE numero = %s", (numero.upper(),))
    if not f:
        raise ValueError(f"No existe la factura {numero}.")
    with conn.cursor() as cur:
        cur.execute("UPDATE facturas SET fecha_radicacion = %s, estado = CASE WHEN estado = 'EXPEDIDA' THEN 'RADICADA' ELSE estado END WHERE numero = %s",
                    (fecha_radicacion, f["numero"]))
    auditar(conn, rol, "radicar_factura", responsable, f["sq"], {"numero": f["numero"], "fecha": fecha_radicacion})
    conn.commit()
    return {"registrado": True, "numero": f["numero"], "fecha_radicacion": fecha_radicacion}


def registrar_glosa(conn, numero_factura, concepto, valor, descripcion, fecha_notificacion, responsable, codigo_especifico="", rol="CONTABLE"):
    f = _uno(conn, "SELECT * FROM facturas WHERE numero = %s", (numero_factura.upper(),))
    if not f:
        raise ValueError(f"No existe la factura {numero_factura}.")
    concepto = concepto.upper().strip()
    if concepto not in CONCEPTOS_GLOSA:
        raise ValueError(f"concepto debe ser uno de {CONCEPTOS_GLOSA} (Resolución 2284 de 2023).")
    if float(valor) > float(f["valor"]):
        raise ValueError("El valor glosado no puede superar el valor de la factura.")
    aviso = revisar_texto(conn, rol, "glosas.descripcion", descripcion, f["sq"])
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO glosas (numero_factura, concepto, codigo_especifico, valor, descripcion, fecha_notificacion, registrada_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (f["numero"], concepto, codigo_especifico or None, valor, descripcion, fecha_notificacion, responsable))
        gid = cur.fetchone()[0]
        cur.execute("UPDATE facturas SET estado = 'GLOSADA' WHERE numero = %s", (f["numero"],))
    auditar(conn, rol, "registrar_glosa", responsable, f["sq"], {"id": gid, "concepto": concepto, "valor": valor})
    conn.commit()
    fest = festivos(conn)
    limite = sumar_habiles(fecha_notificacion, int(parametros(conn)["plazo_respuesta_glosa"]), fest)
    return {"registrada": True, "id_glosa": gid, "numero_factura": f["numero"], "concepto": f"{concepto} · {CONCEPTOS_GLOSA[concepto]}",
            "responder_a_mas_tardar": limite.isoformat(), **({"aviso_seguridad": aviso} if aviso else {})}


def responder_glosa(conn, id_glosa, respuesta, justificacion, responsable, valor_aceptado=0, rol="CONTABLE"):
    g = _uno(conn, "SELECT * FROM glosas WHERE id = %s", (int(id_glosa),))
    if not g:
        raise ValueError(f"No existe la glosa {id_glosa}.")
    if g["estado"] != "ABIERTA":
        raise ValueError(f"La glosa {id_glosa} ya está {g['estado'].lower()}.")
    respuesta = respuesta.upper().strip()
    if respuesta not in ("ACEPTADA_TOTAL", "ACEPTADA_PARCIAL", "NO_ACEPTADA"):
        raise ValueError("respuesta debe ser ACEPTADA_TOTAL, ACEPTADA_PARCIAL o NO_ACEPTADA.")
    va = float(g["valor"]) if respuesta == "ACEPTADA_TOTAL" else 0 if respuesta == "NO_ACEPTADA" else float(valor_aceptado)
    if respuesta == "ACEPTADA_PARCIAL" and not (0 < va < float(g["valor"])):
        raise ValueError("En una aceptación parcial el valor aceptado debe estar entre 0 y el valor glosado.")
    aviso = revisar_texto(conn, rol, "glosas.justificacion", justificacion)
    H = hoy(conn)
    with conn.cursor() as cur:
        cur.execute("""UPDATE glosas SET estado = 'RESPONDIDA', respuesta = %s, valor_aceptado = %s, justificacion = %s, fecha_respuesta = %s,
                       respondida_por = %s WHERE id = %s""", (respuesta, va, justificacion, H, responsable, g["id"]))
    fest = festivos(conn)
    limite = sumar_habiles(g["fecha_notificacion"], int(parametros(conn)["plazo_respuesta_glosa"]), fest)
    auditar(conn, rol, "responder_glosa", responsable, None, {"id": g["id"], "respuesta": respuesta, "valor_aceptado": va})
    conn.commit()
    return {"registrado": True, "id_glosa": g["id"], "respuesta": respuesta, "valor_aceptado": va,
            "a_tiempo": H <= limite, "limite": limite.isoformat(), **({"aviso_seguridad": aviso} if aviso else {})}


def glosas(conn, solo_abiertas=True):
    H = hoy(conn)
    fest = festivos(conn)
    plazo = int(parametros(conn)["plazo_respuesta_glosa"])
    filas = _q(conn, """SELECT g.*, f.sq, f.valor AS valor_factura, a.nombre AS aseguradora FROM glosas g
                        JOIN facturas f ON f.numero = g.numero_factura JOIN aseguradoras a ON a.id_aseguradora = f.id_aseguradora
                        WHERE (%s = FALSE OR g.estado = 'ABIERTA') ORDER BY g.fecha_notificacion""", (solo_abiertas,))
    out = []
    for g in filas:
        limite = sumar_habiles(g["fecha_notificacion"], plazo, fest)
        out.append({"id": g["id"], "numero_factura": g["numero_factura"], "sq": g["sq"], "aseguradora": g["aseguradora"],
                    "concepto": g["concepto"], "concepto_nombre": CONCEPTOS_GLOSA[g["concepto"]], "codigo_especifico": g["codigo_especifico"],
                    "valor": float(g["valor"]), "valor_factura": float(g["valor_factura"]), "descripcion": g["descripcion"],
                    "fecha_notificacion": g["fecha_notificacion"].isoformat(), "limite_respuesta": limite.isoformat(),
                    "dias_habiles_restantes": habiles_entre(H, limite, fest) if g["estado"] == "ABIERTA" else None,
                    "estado": g["estado"], "respuesta": g["respuesta"], "valor_aceptado": float(g["valor_aceptado"]) if g["valor_aceptado"] is not None else None,
                    "justificacion": g["justificacion"]})
    return out


def costos(conn, desde, hasta):
    filas = _q(conn, """SELECT c.sq, t.fecha, p.especialidad, p.tipo_cirugia, p.nombre, p.costo_estimado, p.tarifa,
                               COALESCE((SELECT SUM(costo) FROM insumos_caso i WHERE i.sq = c.sq), 0) AS insumos
                        FROM casos c JOIN procedimientos p ON p.codigo = c.procedimiento JOIN tiempos t ON t.sq = c.sq
                        WHERE c.estado = 'REALIZADO' AND t.fecha BETWEEN %s AND %s ORDER BY t.fecha""", (desde, hasta))
    tot = defaultdict(lambda: {"cirugias": 0, "costo": 0.0, "ingreso_esperado": 0.0})
    for f in filas:
        k = f["tipo_cirugia"]
        tot[k]["cirugias"] += 1
        tot[k]["costo"] += float(f["costo_estimado"]) + float(f["insumos"])
        tot[k]["ingreso_esperado"] += float(f["tarifa"])
    canc = _q(conn, """SELECT x.motivo, count(*) AS n, SUM(p.costo_estimado) * 0.15 AS costo_perdido FROM cancelaciones x
                       JOIN casos c ON c.sq = x.sq JOIN procedimientos p ON p.codigo = c.procedimiento
                       WHERE x.fecha BETWEEN %s AND %s GROUP BY x.motivo ORDER BY n DESC""", (desde, hasta))
    return {"desde": desde, "hasta": hasta, "por_tipo": dict(tot),
            "cancelaciones": [{"motivo": r["motivo"], "casos": r["n"], "costo_perdido_estimado": float(r["costo_perdido"] or 0)} for r in canc],
            "supuesto": "Costo perdido por cancelación = 15 % del costo estimado del procedimiento (sala, personal y material abierto)."}


# ------------------------------------------------------------------ CONSULTA: indicadores, riesgos y datos
def indicadores(conn, desde="", hasta=""):
    H = hoy(conn)
    P = parametros(conn)
    fest = festivos(conn)
    d = _f(desde) if desde else H - dt.timedelta(days=28)
    h = _f(hasta) if hasta else H - dt.timedelta(days=1)
    dias = [d + dt.timedelta(days=i) for i in range((h - d).days + 1)]
    habiles = [x for x in dias if x.weekday() < 5 and x.isoformat() not in fest]
    t = _q(conn, "SELECT * FROM tiempos WHERE fecha BETWEEN %s AND %s AND salida IS NOT NULL", (d, h))
    min_real = sum(reglas.a_min(x["salida"]) - reglas.a_min(x["entrada"]) for x in t if x["id_sala"] != "Q6")
    disp = len(habiles) * int(P["minutos_sala_dia"]) * 5
    util = 100 * min_real / disp if disp else 0
    prog = _q(conn, """SELECT pc.* FROM programacion_casos pc JOIN programaciones p ON p.id = pc.programacion_id
                       WHERE p.estado = 'APROBADA' AND pc.fecha BETWEEN %s AND %s""", (d, h))
    canc = _q(conn, "SELECT * FROM cancelaciones WHERE fecha BETWEEN %s AND %s AND mismo_dia", (d, h))
    canc_pct = 100 * len(canc) / len(prog) if prog else 0
    primeras = {}
    for p in prog:
        k = (p["fecha"], p["id_sala"])
        if k not in primeras or p["hora_inicio"] < primeras[k]["hora_inicio"]:
            primeras[k] = p
    reales = {x["sq"]: x for x in t}
    evaluadas = [p for p in primeras.values() if p["sq"] in reales]
    a_tiempo = [p for p in evaluadas if reglas.a_min(reales[p["sq"]]["entrada"]) <= reglas.a_min(p["hora_inicio"]) + 15]
    punt = 100 * len(a_tiempo) / len(evaluadas) if evaluadas else 0
    ctx = contexto(conn)
    activos = [c for c in ctx["casos"].values() if c["estado"] == "ACTIVO"]
    atr = [c for c in activos if c["atrasado"]]
    atr_pct = 100 * len(atr) / len(activos) if activos else 0
    dur = _q(conn, """SELECT p.tipo_cirugia, p.especialidad, count(*) AS n, AVG(p.duracion_min) AS estimada,
                             AVG(EXTRACT(EPOCH FROM (t.cierre - t.incision))/60 + 20) AS real
                      FROM tiempos t JOIN casos c ON c.sq = t.sq JOIN procedimientos p ON p.codigo = c.procedimiento
                      WHERE t.fecha BETWEEN %s AND %s AND t.cierre IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""", (d, h))
    fac = _uno(conn, "SELECT COALESCE(SUM(valor),0) AS v FROM facturas WHERE fecha_expedicion BETWEEN %s AND %s", (d, h))["v"]
    glo = _uno(conn, """SELECT COALESCE(SUM(g.valor),0) AS v FROM glosas g JOIN facturas f ON f.numero = g.numero_factura
                        WHERE f.fecha_expedicion BETWEEN %s AND %s""", (d, h))["v"]

    def ind(nombre, valor, meta, sentido, formula, n, dnm):
        cumple = valor >= meta if sentido == "min" else valor <= meta
        return {"indicador": nombre, "valor": round(valor, 1), "meta": meta, "sentido": "mínimo" if sentido == "min" else "máximo",
                "resultado": "Cumple" if cumple else "No cumple", "formula": formula, "numerador": n, "denominador": dnm}
    return {"desde": d.isoformat(), "hasta": h.isoformat(), "indicadores": [
        ind("Utilización de salas (Q1–Q5)", util, float(P["meta_utilizacion"]), "min",
            "minutos reales en sala (salida − entrada) ÷ (días hábiles × 720 min × 5 salas) × 100", min_real, disp),
        ind("Cancelación el mismo día", canc_pct, float(P["meta_cancelacion"]), "max",
            "cancelaciones el mismo día ÷ cirugías programadas aprobadas × 100", len(canc), len(prog)),
        ind("Puntualidad de la primera cirugía", punt, float(P["meta_puntualidad"]), "min",
            "primeras cirugías de cada sala y día que entran ≤ 15 min después de la hora programada ÷ primeras cirugías realizadas × 100", len(a_tiempo), len(evaluadas)),
        ind("Pacientes atrasados", atr_pct, float(P["meta_atrasados"]), "max",
            "casos activos con días de espera > máximo de su prioridad ÷ casos activos × 100", len(atr), len(activos)),
    ], "duracion_estimada_vs_real": [{"tipo": r["tipo_cirugia"], "especialidad": r["especialidad"], "cirugias": r["n"],
                                       "estimada_min": round(float(r["estimada"]), 0), "real_min": round(float(r["real"]), 0)} for r in dur],
        "glosas": {"facturado": float(fac), "glosado": float(glo), "porcentaje": round(100 * float(glo) / float(fac), 1) if fac else 0,
                   "formula": "valor glosado ÷ valor facturado en el período × 100"},
        "nota": "Metas y fórmulas de POL-QX-002 (documento ficticio del curso). Se editan en la tabla parametros."}


def riesgos_juridicos(conn):
    ctx = contexto(conn)
    slots = _slot_de(conn, ctx)
    H = ctx["hoy"]
    out = []
    for c in ctx["casos"].values():
        if c["estado"] != "ACTIVO":
            continue
        s = slots.get(c["sq"])
        cons = c["preparacion_detalle"].get("CONSENTIMIENTO_INFORMADO", {})
        if s and c["preparacion"].get("CONSENTIMIENTO_INFORMADO") != "OK":
            out.append({"sq": c["sq"], "riesgo": "Consentimiento informado faltante", "detalle": f"Cirugía {s['fecha']}"})
        elif s and cons.get("vence") and _f(cons["vence"]) < _f(s["fecha"]):
            out.append({"sq": c["sq"], "riesgo": "Consentimiento informado vencido", "detalle": f"Vence {cons['vence']}, cirugía {s['fecha']}"})
        if c["atrasado"]:
            out.append({"sq": c["sq"], "riesgo": "Atraso por encima del máximo", "detalle": f"{c['prioridad']} · {c['dias_espera']} días (máximo {c['max_espera']})"})
        if len(c["cancelaciones"]) >= 2:
            out.append({"sq": c["sq"], "riesgo": "Cancelaciones repetidas", "detalle": f"{len(c['cancelaciones'])} cancelaciones"})
        if c["autorizacion"] and c["autorizacion"]["estado"] == "NEGADA":
            out.append({"sq": c["sq"], "riesgo": "Autorización negada", "detalle": "Posible tutela o queja; revisar con el área jurídica"})
    alertas = _q(conn, "SELECT motivo, count(*) AS n FROM alertas_seguridad GROUP BY motivo")
    return {"hoy": H.isoformat(), "riesgos": out,
            "proteccion_de_datos": {"alertas_de_seguridad": [{"motivo": a["motivo"], "veces": a["n"]} for a in alertas],
                                    "nota": "La base solo guarda códigos SQ: no hay nombres, documentos ni teléfonos de pacientes (Ley 1581 de 2012)."},
            "aviso": "Solo se señalan riesgos. Los conceptos legales los emite el área jurídica."}


def datos_limpios(conn, desde="", hasta=""):
    H = hoy(conn)
    d = _f(desde) if desde else H - dt.timedelta(days=35)
    h = _f(hasta) if hasta else H + dt.timedelta(days=14)
    filas = _q(conn, """SELECT c.sq, c.prioridad, c.estado, c.fecha_solicitud, p.especialidad, p.tipo_cirugia, p.nombre AS procedimiento,
                               c.cirujano, c.requiere_uci, a.nombre AS aseguradora, v.estado AS autorizacion,
                               pc.fecha AS fecha_programada, pc.id_sala AS sala, pc.hora_inicio AS hora_programada, p.duracion_min AS duracion_estimada,
                               t.entrada, t.salida, t.destino, (SELECT count(*) FROM cancelaciones x WHERE x.sq = c.sq) AS cancelaciones,
                               f.numero AS factura, f.valor AS valor_facturado, (SELECT COALESCE(SUM(g.valor),0) FROM glosas g WHERE g.numero_factura = f.numero) AS valor_glosado
                        FROM casos c JOIN procedimientos p ON p.codigo = c.procedimiento JOIN aseguradoras a ON a.id_aseguradora = c.id_aseguradora
                        LEFT JOIN v_autorizacion_vigente v ON v.sq = c.sq
                        LEFT JOIN LATERAL (SELECT pc.* FROM programacion_casos pc JOIN programaciones pr ON pr.id = pc.programacion_id
                                           WHERE pc.sq = c.sq AND pr.estado = 'APROBADA' ORDER BY pr.version DESC LIMIT 1) pc ON TRUE
                        LEFT JOIN tiempos t ON t.sq = c.sq LEFT JOIN facturas f ON f.sq = c.sq
                        WHERE c.estado = 'ACTIVO' OR pc.fecha BETWEEN %s AND %s ORDER BY c.sq""", (d, h))
    cols = list(filas[0].keys()) if filas else []
    return {"columnas": cols, "filas": [[_txt(f[k]) if not isinstance(f[k], (int, float, bool)) or isinstance(f[k], bool) else f[k] for k in cols] for f in filas],
            "nota": "Una fila por caso. Sin datos personales: el paciente es el código SQ."}


# ------------------------------------------------------------------ TI
def estado_sistema(conn):
    filas = {}
    with conn.cursor() as cur:
        for t in ["casos", "autorizaciones", "programaciones", "insumos_caso", "facturas", "glosas", "auditoria", "alertas_seguridad"]:
            cur.execute(f"SELECT count(*) FROM {t}")
            filas[t] = cur.fetchone()[0]
        cur.execute("SELECT current_schema()")
        esquema = cur.fetchone()[0]
    return {"conexion": "ok", "esquema": esquema, "hoy_del_sistema": hoy(conn).isoformat(), "filas": filas,
            "alertas_sin_revisar": _uno(conn, "SELECT count(*) AS n FROM alertas_seguridad WHERE NOT revisada")["n"]}


def ver_auditoria(conn, limite=30, sq=""):
    filas = _q(conn, "SELECT * FROM auditoria WHERE (%s = '' OR sq = %s) ORDER BY momento DESC LIMIT %s", (sq.upper(), sq.upper(), int(limite)))
    return [{**f, "momento": _txt(f["momento"])} for f in filas]


def alertas(conn, solo_sin_revisar=True):
    return [{**a, "momento": _txt(a["momento"])} for a in
            _q(conn, "SELECT * FROM alertas_seguridad WHERE (%s = FALSE OR NOT revisada) ORDER BY momento DESC", (solo_sin_revisar,))]


def marcar_alerta_revisada(conn, id_alerta, revisada_por):
    with conn.cursor() as cur:
        cur.execute("UPDATE alertas_seguridad SET revisada = TRUE WHERE id = %s", (int(id_alerta),))
    auditar(conn, "TI", "alerta_revisada", revisada_por, None, {"id": id_alerta})
    conn.commit()
    return {"registrado": True, "id": id_alerta}


def fijar_fecha_del_curso(conn, fecha, quien):
    if fecha:
        _f(fecha)
    with conn.cursor() as cur:
        cur.execute("UPDATE parametros SET valor = %s WHERE clave = 'fecha_hoy'", (fecha or "",))
    auditar(conn, "TI", "fijar_fecha", quien, None, {"fecha_hoy": fecha})
    conn.commit()
    return {"fecha_hoy": fecha or "fecha real de Colombia"}
