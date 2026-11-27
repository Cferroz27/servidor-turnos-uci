"""Motor de reglas de la programación quirúrgica (POL-QX-002, ficticia).

Errores: bloquean la aprobación. Avisos: se informan, pero no bloquean.
Reglas:
  R1 Atrasado = días de espera > máximo de su prioridad (P1 7, P2 30, P3 90).
  R2 Se programa por prioridad y, dentro de cada prioridad, por días de espera.
  R3 P2 y P3 solo con autorización aprobada y vigente. P1 se programa y la autorización se tramita en paralelo.
  R4 Cada cirugía va en la sala que tiene su equipo, disponible y con mantenimiento al día. Q6 solo P1, de 7:00 a 11:00.
  R5 Máximo 2 casos con UCI por día. Ningún cirujano en dos salas a la vez, y solo en sus días y horas disponibles.
  R6 Duración estimada + 30 minutos de rotación. La última cirugía inicia a más tardar a las 17:00.
  R7 Todo insumo vencido, no estéril o faltante bloquea la cirugía hasta que se resuelva.
  R8 No se usan más salas a la vez que anestesiólogos disponibles (5). No se programa en fines de semana ni festivos.
"""
import datetime as dt
from collections import defaultdict


def a_min(h):
    if isinstance(h, dt.time):
        return h.hour * 60 + h.minute
    h = str(h)
    hh, mm = h.split(":")[:2]
    return int(hh) * 60 + int(mm)


def a_hora(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _f(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def validar(ctx, asignaciones):
    """ctx: contexto de Servicio.contexto(). asignaciones: [{sq, fecha, sala, hora_inicio}].
    Devuelve {"errores": [...], "avisos": [...], "por_caso": {sq: {"errores": [], "avisos": []}}, "asignaciones": [...normalizadas]}"""
    P = ctx["parametros"]
    rot = int(P["rotacion_min"])
    ultima = a_min(P["ultima_hora_inicio"])
    q6_fin = a_min(P["q6_hora_fin"])
    max_uci = int(P["max_uci_dia"])
    anest = int(P["anestesiologos_dia"])
    festivos = set(x for x in P["festivos"].split(",") if x)
    por_caso = defaultdict(lambda: {"errores": [], "avisos": []})
    errores, avisos = [], []

    def err(sq, regla, texto):
        por_caso[sq]["errores"].append(f"{regla}: {texto}")
        errores.append({"sq": sq, "regla": regla, "detalle": texto})

    def av(sq, regla, texto):
        por_caso[sq]["avisos"].append(f"{regla}: {texto}")
        avisos.append({"sq": sq, "regla": regla, "detalle": texto})

    norm, vistos = [], set()
    for a in asignaciones:
        sq = str(a.get("sq", "")).strip().upper()
        c = ctx["casos"].get(sq)
        if not c:
            errores.append({"sq": sq, "regla": "Datos", "detalle": "El código SQ no existe."})
            continue
        if sq in vistos:
            err(sq, "Datos", "El caso aparece dos veces en la programación.")
            continue
        vistos.add(sq)
        try:
            fecha = _f(a["fecha"])
            ini = a_min(a["hora_inicio"])
        except Exception:
            err(sq, "Datos", "Fecha (AAAA-MM-DD) u hora de inicio (HH:MM) inválida.")
            continue
        sala = str(a.get("sala", "")).strip().upper()
        dur = c["duracion_min"]
        norm.append({"sq": sq, "fecha": fecha, "sala": sala, "ini": ini, "fin": ini + dur, "c": c})

    for x in norm:
        sq, c, fecha, sala, ini, fin = x["sq"], x["c"], x["fecha"], x["sala"], x["ini"], x["fin"]
        if c["estado"] != "ACTIVO":
            err(sq, "Datos", f"El caso está {c['estado'].lower()}; no se puede programar.")
        if not c.get("habilitado", True):
            falta = "; ".join(c.get("falta_para_agendar", []))
            if c["prioridad"] == "P1":
                av(sq, "Habilitación", f"P1 agendado sin estar habilitado (falta: {falta}).")
            else:
                err(sq, "Habilitación", f"No está habilitado para agendar (falta: {falta}).")
        if c["solicitud_validada"] is None:
            err(sq, "Etapa 1", "La solicitud aún no ha sido validada por el área administrativa.")
        elif c["solicitud_validada"] is False:
            err(sq, "Etapa 1", "La solicitud fue rechazada por el área administrativa.")
        if fecha.weekday() >= 5 or fecha.isoformat() in festivos:
            err(sq, "R8", f"{fecha.isoformat()} no es día hábil (fin de semana o festivo).")
        # R3 autorización
        aut = c["autorizacion"]
        vigente = aut and aut["estado"] == "APROBADA" and (not aut.get("vigente_hasta") or _f(aut["vigente_hasta"]) >= fecha)
        if c["prioridad"] in ("P2", "P3") and not vigente:
            estado = aut["estado"].lower() if aut else "sin registrar"
            if aut and aut["estado"] == "APROBADA":
                estado = f"vencida el {aut['vigente_hasta']}"
            err(sq, "R3", f"{c['prioridad']} sin autorización aprobada y vigente (autorización {estado}).")
        elif c["prioridad"] == "P1" and not vigente:
            av(sq, "R3", "P1 programado con la autorización en trámite: se gestiona en paralelo.")
        # R4 sala y equipo
        s = ctx["salas"].get(sala)
        if not s:
            err(sq, "R4", f"La sala {sala or '(vacía)'} no existe.")
            continue
        req = c["equipo_requerido"]
        if s["solo_p1"]:
            if c["prioridad"] != "P1":
                err(sq, "R4", f"{sala} solo recibe P1 (es la sala de urgencias).")
            if fin > q6_fin:
                err(sq, "R4", f"En {sala} la cirugía debe terminar a más tardar a las {P['q6_hora_fin']} (termina {a_hora(fin)}).")
        if req:
            eq = [e for e in ctx["equipos"] if e["id_sala"] == sala and e["tipo"] == req]
            if not eq:
                err(sq, "R4", f"{sala} no tiene el equipo requerido ({req.replace('_', ' ').lower()}).")
            else:
                e = eq[0]
                if _f(e["mantenimiento_vence"]) < fecha:
                    err(sq, "R4", f"El mantenimiento de {e['id_equipo']} venció el {e['mantenimiento_vence']}.")
                for n in ctx["novedades"]:
                    if n["tipo"] == "EQUIPO" and n["recurso"] == e["id_equipo"] and _f(n["fecha"]) == fecha:
                        err(sq, "R4", f"{e['id_equipo']} no está disponible ese día: {n['descripcion']}.")
        for n in ctx["novedades"]:
            if n["tipo"] == "SALA" and n["recurso"] == sala and _f(n["fecha"]) == fecha:
                err(sq, "R4", f"{sala} no está disponible ese día: {n['descripcion']}.")
        # R6 horario
        if ini < a_min(s["hora_inicio"]):
            err(sq, "R6", f"Inicia antes de que abra la sala ({s['hora_inicio']}).")
        if ini > ultima:
            err(sq, "R6", f"Inicia a las {a_hora(ini)}; la última cirugía debe iniciar a más tardar a las {P['ultima_hora_inicio']}.")
        if fin > a_min(s["hora_fin"]) and not s["solo_p1"]:
            err(sq, "R6", f"Termina a las {a_hora(fin)}, después del cierre de la sala ({s['hora_fin']}).")
        # R5 cirujano
        cj = ctx["cirujanos"][c["cirujano"]]
        if str(fecha.isoweekday()) not in cj["dias"].split(","):
            err(sq, "R5", f"{c['cirujano']} no opera ese día de la semana (días: {cj['dias_texto']}).")
        if fin > a_min(cj["hora_fin"]):
            err(sq, "R5", f"{c['cirujano']} solo opera hasta las {a_hora(a_min(cj['hora_fin']))}.")
        for n in ctx["novedades"]:
            if n["tipo"] == "CIRUJANO" and n["recurso"] == c["cirujano"] and _f(n["fecha"]) == fecha:
                err(sq, "R5", f"{c['cirujano']} tiene una novedad ese día: {n['descripcion']}.")
        # R7 insumos
        for i in c["insumos"]:
            if i["estado"] == "FALTANTE":
                err(sq, "R7", f"Insumo faltante: {i['descripcion']}.")
            elif i.get("vence") and _f(i["vence"]) < fecha:
                err(sq, "R7", f"{i['descripcion']} vence ({i['vence']}) antes de la cirugía.")
        # Avisos de etapa
        if c["atrasado"]:
            av(sq, "R1", f"Atrasado: {c['dias_espera']} días de espera (máximo {c['max_espera']}).")
        pend = [k for k, v in c["preparacion"].items() if v == "PENDIENTE"]
        if pend:
            av(sq, "Etapa 4", "Preparación pendiente: " + ", ".join(p.replace("_", " ").lower() for p in pend) + ".")
        if any(i["estado"] == "PENDIENTE" for i in c["insumos"]):
            av(sq, "Etapa 5", "Insumos sin verificar por la central de esterilización o el almacén.")

    # Reglas entre casos
    por_sala = defaultdict(list)
    por_cir = defaultdict(list)
    uci = defaultdict(list)
    for x in norm:
        por_sala[(x["fecha"], x["sala"])].append(x)
        por_cir[(x["fecha"], x["c"]["cirujano"])].append(x)
        if x["c"]["requiere_uci"]:
            uci[x["fecha"]].append(x["sq"])
    for (fecha, sala), lst in por_sala.items():
        lst.sort(key=lambda y: y["ini"])
        for a, b in zip(lst, lst[1:]):
            if b["ini"] < a["fin"] + rot:
                err(b["sq"], "R6", f"En {sala} el {fecha.isoformat()} se cruza con {a['sq']} (termina {a_hora(a['fin'])} + {rot} min de rotación).")
    for (fecha, cir), lst in por_cir.items():
        lst.sort(key=lambda y: y["ini"])
        for a, b in zip(lst, lst[1:]):
            if b["ini"] < a["fin"]:
                err(b["sq"], "R5", f"{cir} estaría en dos salas a la vez ({a['sq']} en {a['sala']} y {b['sq']} en {b['sala']}).")
    for fecha, lst in uci.items():
        if len(lst) > max_uci:
            for sq in lst[max_uci:]:
                err(sq, "R5", f"Supera el cupo de UCI del {fecha.isoformat()} ({len(lst)} casos con UCI; máximo {max_uci}).")
    # R8 salas simultáneas
    por_dia = defaultdict(list)
    for x in norm:
        por_dia[x["fecha"]].append(x)
    for fecha, lst in por_dia.items():
        for x in lst:
            simult = {y["sala"] for y in lst if y["ini"] <= x["ini"] < y["fin"]}
            if len(simult) > anest:
                err(x["sq"], "R8", f"A las {a_hora(x['ini'])} del {fecha.isoformat()} habría {len(simult)} salas operando y solo hay {anest} anestesiólogos.")
                break

    salida = [{"sq": x["sq"], "fecha": x["fecha"].isoformat(), "sala": x["sala"], "hora_inicio": a_hora(x["ini"]),
               "hora_fin": a_hora(x["fin"]), "prioridad": x["c"]["prioridad"], "cirujano": x["c"]["cirujano"],
               "procedimiento": x["c"]["procedimiento_nombre"], "uci": x["c"]["requiere_uci"]} for x in norm]
    return {"cumple": not errores, "total_casos": len(salida), "errores": errores, "avisos": avisos,
            "por_caso": dict(por_caso), "asignaciones": salida}


def proponer(ctx, dias, fijas=None):
    """Propuesta automática (R2): por prioridad y días de espera, en el primer hueco válido.
    dias: fechas hábiles de la semana. fijas: asignaciones que se respetan (p. ej. ya aprobadas)."""
    plan = list(fijas or [])
    ya = {a["sq"] for a in plan}
    orden = {"P1": 0, "P2": 1, "P3": 2}
    cands = sorted([c for c in ctx["casos"].values() if c["estado"] == "ACTIVO" and c["sq"] not in ya and not c["realizado"]],
                   key=lambda c: (orden[c["prioridad"]], -c["dias_espera"]))
    omitidos = []
    base_err = len(validar(ctx, plan)["errores"]) if plan else 0
    for c in cands:
        motivo = None
        if not c.get("habilitado", True):
            motivo = "no habilitado para agendar · falta: " + "; ".join(c.get("falta_para_agendar", []))
        elif any(i["estado"] == "FALTANTE" for i in c["insumos"]):
            motivo = "insumo faltante: " + ", ".join(i["descripcion"] for i in c["insumos"] if i["estado"] == "FALTANTE")
        elif dias and any(i.get("vence") and _f(i["vence"]) < dias[0] for i in c["insumos"]):
            motivo = "insumo vencido antes de la semana (re-esterilizar o reponer): " + ", ".join(
                i["descripcion"] for i in c["insumos"] if i.get("vence") and _f(i["vence"]) < dias[0])
        if motivo:
            omitidos.append({"sq": c["sq"], "prioridad": c["prioridad"], "motivo": motivo})
            continue
        salas = [s for s in ctx["salas"].values() if not s["solo_p1"]] + [s for s in ctx["salas"].values() if s["solo_p1"]]
        cj = ctx["cirujanos"][c["cirujano"]]
        req = c["equipo_requerido"]
        rot = int(ctx["parametros"]["rotacion_min"])
        ultima = a_min(ctx["parametros"]["ultima_hora_inicio"])
        hecho = False
        for d in dias:
            if str(d.isoweekday()) not in cj["dias"].split(","):
                continue
            for s in salas:
                if s["solo_p1"] and c["prioridad"] != "P1":
                    continue
                if req and not any(e["id_sala"] == s["id_sala"] and e["tipo"] == req for e in ctx["equipos"]):
                    continue
                ocupados = sorted((a_min(a["hora_inicio"]), a_min(a["hora_inicio"]) + ctx["casos"][a["sq"]]["duracion_min"])
                                  for a in plan if a["fecha"] == d.isoformat() and a["sala"] == s["id_sala"])
                t = a_min(s["hora_inicio"])
                for i_, f_ in ocupados:
                    t = max(t, f_ + rot)
                while t <= ultima:
                    prueba = plan + [{"sq": c["sq"], "fecha": d.isoformat(), "sala": s["id_sala"], "hora_inicio": a_hora(t)}]
                    v = validar(ctx, prueba)
                    if not v["por_caso"].get(c["sq"], {}).get("errores") and len(v["errores"]) <= base_err:
                        plan = prueba
                        hecho = True
                        break
                    t += 30
                if hecho:
                    break
            if hecho:
                break
        if not hecho:
            omitidos.append({"sq": c["sq"], "prioridad": c["prioridad"],
                             "motivo": "no cupo en la semana con las reglas vigentes (salas con su equipo, días del cirujano, cupo de UCI y horario)"})
    return plan, omitidos
