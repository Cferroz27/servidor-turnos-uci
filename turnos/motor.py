"""Motor de programación de turnos (asignación, verificación de reglas y estado de cierre).

Trabaja solo con códigos. Es el mismo motor de la skill de referencia, adaptado para
recibir los datos desde la base de datos en lugar de archivos.
"""
import random, datetime as dt
from collections import defaultdict

class Estado:
    def __init__(self, personas, prev):
        self.h_semana = defaultdict(float)      # (cod, lunes) -> horas
        self.ultimo = {}                        # cod -> (fecha, turno)
        self.racha_dias = defaultdict(int)
        self.racha_noches = defaultdict(int)
        self.noches = defaultdict(int)
        self.findes = defaultdict(int)
        self.horas_mes = defaultdict(float)
        if prev:
            for cod, e in prev.get("personas", {}).items():
                if e.get("ultimo_turno_fecha"):
                    self.ultimo[cod] = (dt.date.fromisoformat(e["ultimo_turno_fecha"]), e["ultimo_turno"])
                self.racha_dias[cod] = e.get("dias_seguidos_al_cierre", 0)
                self.racha_noches[cod] = e.get("noches_seguidas_al_cierre", 0)
                lunes = e.get("semana_abierta_lunes")
                if lunes:
                    self.h_semana[(cod, dt.date.fromisoformat(lunes))] = e.get("horas_semana_abierta", 0)
                # equidad acumulada (arrastre)
                self.noches[cod] = e.get("saldo_noches", 0)
                self.findes[cod] = e.get("saldo_findes", 0)


def lunes_de(d):
    return d - dt.timedelta(days=d.weekday())


def puede(p, d, t, est, P, ausencias, asignado_hoy):
    cod = p["codigo"]
    T = P["turnos"]
    if cod in asignado_hoy:
        return False, "ya tiene turno hoy"
    if d in ausencias.get(cod, {}):
        return False, "novedad"
    if t == "N" and not p["acepta_noche"]:
        return False, "no hace noches"
    tope = min(P["horas_max_semana"], p["horas_contrato"] or P["horas_max_semana"])
    if est.h_semana[(cod, lunes_de(d))] + T[t]["horas"] > tope:
        return False, "tope semanal"
    u = est.ultimo.get(cod)
    if u:
        fu, tu = u
        fin = dt.datetime.combine(fu, dt.time.fromisoformat(T[tu]["inicio"])) + dt.timedelta(hours=T[tu]["horas"])
        ini = dt.datetime.combine(d, dt.time.fromisoformat(T[t]["inicio"]))
        if (ini - fin).total_seconds() / 3600 < P["descanso_min_horas"]:
            return False, "descanso mínimo"
        if fu == d - dt.timedelta(days=1):
            if tu == "N" and t in P["prohibido_despues_de_noche"]:
                return False, "salió de noche"
            if est.racha_dias[cod] >= P["max_dias_seguidos"]:
                return False, "días seguidos"
            if t == "N" and tu == "N" and est.racha_noches[cod] >= P["max_noches_seguidas"]:
                return False, "noches seguidas"
    return True, ""


def registrar(p, d, t, est, P):
    cod = p["codigo"]
    u = est.ultimo.get(cod)
    seguido = bool(u and u[0] == d - dt.timedelta(days=1))
    est.racha_dias[cod] = est.racha_dias[cod] + 1 if seguido else 1
    est.racha_noches[cod] = (est.racha_noches[cod] + 1 if (seguido and u[1] == "N") else 1) if t == "N" else 0
    est.ultimo[cod] = (d, t)
    h = P["turnos"][t]["horas"]
    est.h_semana[(cod, lunes_de(d))] += h
    est.horas_mes[cod] += h
    if t == "N":
        est.noches[cod] += 1


def resolver(personas, dias, P, ausencias, prev, semilla):
    rnd = random.Random(semilla)
    est = Estado(personas, prev)
    cuadro = defaultdict(dict)       # cod -> fecha -> turno
    huecos, motivos = [], []
    festivos = {dt.date.fromisoformat(x) for x in P.get("festivos", [])}
    for d in dias:
        asignado_hoy = set()
        especial = d.weekday() >= 5 or d in festivos
        for t in P["orden_asignacion"]:            # noche primero: es la más difícil de cubrir
            for rol, n in P["cobertura"][t].items():
                cand = []
                for p in personas:
                    if p["rol"] != rol:
                        continue
                    ok, _ = puede(p, d, t, est, P, ausencias, asignado_hoy)
                    if ok:
                        cod = p["codigo"]
                        tope = min(P["horas_max_semana"], p["horas_contrato"] or P["horas_max_semana"])
                        carga = est.horas_mes[cod] / tope            # menos horas relativas → primero
                        eq_noche = est.noches[cod] * 3 if t == "N" else 0
                        eq_finde = est.findes[cod] * 2 if especial else 0
                        # Continuidad: quien hizo noche ayer, preferible repetir noche (bloques)
                        u = est.ultimo.get(cod)
                        bloque = -1.5 if (t == "N" and u and u[0] == d - dt.timedelta(days=1) and u[1] == "N") else 0
                        cand.append((carga + eq_noche + eq_finde + bloque + rnd.random() * 0.5, p))
                cand.sort(key=lambda x: x[0])
                elegidos = [p for _, p in cand[:n]]
                for p in elegidos:
                    registrar(p, d, t, est, P)
                    cuadro[p["codigo"]][d] = t
                    asignado_hoy.add(p["codigo"])
                    if especial:
                        est.findes[p["codigo"]] += 1
                    motivos.append((d, t, p["codigo"], rol))
                if len(elegidos) < n:
                    huecos.append({"fecha": d.isoformat(), "turno": t, "rol": rol, "faltan": n - len(elegidos)})
        # quien no trabajó hoy rompe sus rachas
        for p in personas:
            if p["codigo"] not in asignado_hoy:
                est.racha_dias[p["codigo"]] = 0
                est.racha_noches[p["codigo"]] = 0
    return cuadro, huecos, est


def dispersion(est, personas, campo):
    vals = defaultdict(list)
    for p in personas:
        vals[p["rol"]].append(getattr(est, campo)[p["codigo"]])
    return sum(max(v) - min(v) for v in vals.values() if v)



def _lunes(d):
    return d - dt.timedelta(days=d.weekday())


def verificar(personas, dias, cuadro, ausencias, P, prev=None):
    T = P["turnos"]
    res = []
    rol = {p["codigo"]: p for p in personas}

    # 1. Cobertura mínima
    faltas = []
    for d in dias:
        for t in ("M", "T", "N"):
            for r, n in P["cobertura"][t].items():
                k = sum(1 for p in personas if p["rol"] == r and cuadro[p["codigo"]].get(d) == t)
                if k < n:
                    faltas.append(f"{d.day:02d} {t} {r} ({k}/{n})")
    res.append({"regla": "Cobertura mínima por turno", "ok": not faltas,
                "detalle": "todos los turnos cubiertos" if not faltas else "; ".join(faltas[:12]) + (" …" if len(faltas) > 12 else "")})

    # 2. Novedades respetadas
    malas = [f"{c} {d.day:02d}" for c in cuadro for d, t in cuadro[c].items() if d in ausencias.get(c, {})]
    res.append({"regla": "Nadie trabaja durante vacaciones o novedades", "ok": not malas, "detalle": ", ".join(malas) or "sin cruces"})

    # 3. Noches solo a quien las acepta
    malas = [f"{c} {d.day:02d}" for c in cuadro for d, t in cuadro[c].items() if t == "N" and not rol[c]["acepta_noche"]]
    res.append({"regla": "Noches solo para quien acepta noches", "ok": not malas, "detalle": ", ".join(malas) or "sin cruces"})

    # Línea de tiempo por persona (incluye último turno del mes anterior)
    previo = {}
    if prev:
        for c, e in prev.get("personas", {}).items():
            if e.get("ultimo_turno_fecha"):
                previo[c] = (dt.date.fromisoformat(e["ultimo_turno_fecha"]), e["ultimo_turno"])

    post_noche, descanso, racha_d, racha_n = [], [], [], []
    for c in cuadro:
        turnos = sorted(cuadro[c].items())
        if c in previo:
            turnos = [previo[c]] + turnos
        for (d1, t1), (d2, t2) in zip(turnos, turnos[1:]):
            fin = dt.datetime.combine(d1, dt.time.fromisoformat(T[t1]["inicio"])) + dt.timedelta(hours=T[t1]["horas"])
            ini = dt.datetime.combine(d2, dt.time.fromisoformat(T[t2]["inicio"]))
            if d2 == d1 + dt.timedelta(days=1) and t1 == "N" and t2 in P["prohibido_despues_de_noche"]:
                post_noche.append(f"{c} {d1.day:02d}N→{d2.day:02d}{t2}")
            elif (ini - fin).total_seconds() / 3600 < P["descanso_min_horas"]:
                descanso.append(f"{c} {d1.day:02d}{t1}→{d2.day:02d}{t2}")
        # rachas dentro del mes (más la arrastrada)
        e = (prev or {}).get("personas", {}).get(c, {})
        rd, rn = e.get("dias_seguidos_al_cierre", 0), e.get("noches_seguidas_al_cierre", 0)
        for d in dias:
            t = cuadro[c].get(d)
            if t:
                rd += 1
                rn = rn + 1 if t == "N" else 0
                if rd > P["max_dias_seguidos"]:
                    racha_d.append(f"{c} hasta {d.day:02d}")
                if rn > P["max_noches_seguidas"]:
                    racha_n.append(f"{c} hasta {d.day:02d}")
            else:
                rd, rn = 0, 0
    res.append({"regla": "Quien sale de noche no entra a la mañana (ni tarde) siguiente", "ok": not post_noche,
                "detalle": ", ".join(post_noche) or "sin cruces"})
    res.append({"regla": f"Descanso mínimo de {P['descanso_min_horas']} h entre turnos", "ok": not descanso,
                "detalle": ", ".join(descanso) or "sin cruces"})
    res.append({"regla": f"Máximo {P['max_dias_seguidos']} días seguidos", "ok": not racha_d, "detalle": ", ".join(sorted(set(racha_d))) or "sin excesos"})
    res.append({"regla": f"Máximo {P['max_noches_seguidas']} noches seguidas", "ok": not racha_n, "detalle": ", ".join(sorted(set(racha_n))) or "sin excesos"})

    # Tope semanal
    excesos = []
    for c in cuadro:
        tope = min(P["horas_max_semana"], rol[c]["horas_contrato"] or P["horas_max_semana"])
        h = defaultdict(float)
        e = (prev or {}).get("personas", {}).get(c, {})
        if e.get("semana_abierta_lunes"):
            h[dt.date.fromisoformat(e["semana_abierta_lunes"])] += e.get("horas_semana_abierta", 0)
        for d, t in cuadro[c].items():
            h[_lunes(d)] += T[t]["horas"]
        excesos += [f"{c} sem. {l.isoformat()}: {v:.0f} h" for l, v in h.items() if v > tope]
    res.append({"regla": f"Tope semanal de horas (máx. {P['horas_max_semana']} h o el contrato)", "ok": not excesos,
                "detalle": ", ".join(excesos) or "nadie supera su tope"})

    # Equidad de noches dentro de cada rol (solo entre quienes aceptan noches y no tuvieron novedades largas)
    dif_max = P.get("equidad_max_diferencia_noches", 2)
    detalle, ok = [], True
    for r in ("JEFE", "AUX"):
        grupo = [p["codigo"] for p in personas if p["rol"] == r and p["acepta_noche"] and len(ausencias.get(p["codigo"], {})) < 7
                 and (p["horas_contrato"] or P["horas_max_semana"]) >= P["horas_max_semana"]]
        if grupo:
            saldo = {c: (prev or {}).get("personas", {}).get(c, {}).get("saldo_noches", 0) for c in grupo}
            n = [sum(1 for t in cuadro[c].values() if t == "N") + saldo[c] for c in grupo]
            txt = "noches" + (" (incluye saldo del mes anterior)" if prev else "")
            detalle.append(f"{r}: entre {min(n)} y {max(n)} {txt}")
            ok &= (max(n) - min(n)) <= dif_max
    res.append({"regla": f"Reparto equitativo de noches (diferencia ≤ {dif_max} por rol)", "ok": ok, "detalle": "; ".join(detalle)})
    return res



def estado_cierre(personas, dias, cuadro, P, ausencias, prev=None):
    """Resume el mes para que el siguiente continúe sin errores (mismo formato que la skill)."""
    T = P["turnos"]
    ultimo_dia = dias[-1]
    lunes_abierto = _lunes(ultimo_dia)
    semana_abierta = ultimo_dia.weekday() != 6
    festivos = {dt.date.fromisoformat(x) for x in P.get("festivos", [])}
    prevp = (prev or {}).get("personas", {})
    noches, findes = {}, {}
    for p in personas:
        c = p["codigo"]
        noches[c] = sum(1 for t in cuadro.get(c, {}).values() if t == "N") + prevp.get(c, {}).get("saldo_noches", 0)
        findes[c] = sum(1 for d in cuadro.get(c, {}) if d.weekday() >= 5 or d in festivos) + prevp.get(c, {}).get("saldo_findes", 0)
    out = {"mes": f"{ultimo_dia.year}-{ultimo_dia.month:02d}", "personas": {}}
    for p in personas:
        c = p["codigo"]
        turnos = sorted(cuadro.get(c, {}).items())
        u = turnos[-1] if turnos else None
        if not u and prevp.get(c, {}).get("ultimo_turno_fecha"):
            u = (dt.date.fromisoformat(prevp[c]["ultimo_turno_fecha"]), prevp[c]["ultimo_turno"])
        rd = rn = 0
        d = ultimo_dia
        while d in cuadro.get(c, {}):
            rd += 1
            d -= dt.timedelta(days=1)
        if rd == len(dias):
            rd += prevp.get(c, {}).get("dias_seguidos_al_cierre", 0)
        d = ultimo_dia
        while cuadro.get(c, {}).get(d) == "N":
            rn += 1
            d -= dt.timedelta(days=1)
        horas_abierta = sum(T[t]["horas"] for f, t in cuadro.get(c, {}).items() if _lunes(f) == lunes_abierto)
        ref = [q["codigo"] for q in personas if q["rol"] == p["rol"] and q["acepta_noche"]
               and not ausencias.get(q["codigo"]) and (q["horas_contrato"] or P["horas_max_semana"]) >= P["horas_max_semana"]]
        ref = ref or [q["codigo"] for q in personas if q["rol"] == p["rol"]]
        media_n = sum(noches[x] for x in ref) / len(ref)
        media_f = sum(findes[x] for x in ref) / len(ref)
        out["personas"][c] = {
            "rol": p["rol"],
            "ultimo_turno_fecha": u[0].isoformat() if u else None,
            "ultimo_turno": u[1] if u else None,
            "dias_seguidos_al_cierre": rd,
            "noches_seguidas_al_cierre": rn,
            "semana_abierta_lunes": lunes_abierto.isoformat() if semana_abierta else None,
            "horas_semana_abierta": horas_abierta if semana_abierta else 0,
            "saldo_noches": max(-2, min(2, round(noches[c] - media_n))) if p["acepta_noche"] else 0,
            "saldo_findes": max(-2, min(2, round(findes[c] - media_f))),
        }
    return out


def dias_del_mes(mes):
    a, m = map(int, mes.split("-"))
    d, dias = dt.date(a, m, 1), []
    while d.month == m:
        dias.append(d)
        d += dt.timedelta(days=1)
    return dias


def programar(personas, mes, P, ausencias, prev, semilla=2026, intentos=150):
    """Genera el mejor cuadro entre varias semillas. Devuelve (cuadro, huecos, semilla)."""
    dias = dias_del_mes(mes)
    mejor = None
    for k in range(intentos):
        cuadro, huecos, est = resolver(personas, dias, P, ausencias, prev, semilla + k)
        clave = (sum(h["faltan"] for h in huecos), dispersion(est, personas, "noches") + dispersion(est, personas, "findes"))
        if mejor is None or clave < mejor[0]:
            mejor = (clave, cuadro, huecos, semilla + k)
    return mejor[1], mejor[2], mejor[3]
