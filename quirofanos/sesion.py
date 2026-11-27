"""Inicio de sesión por rol (PIN) y pendientes de cada rol para los avisos del artefacto.

El PIN no reemplaza la clave del conector (esa es la verdadera llave): sirve para que el artefacto del equipo
pida un inicio de sesión, muestre solo lo del rol y deje en la auditoría quién entró.
El PIN de cada rol y equipo se deriva de un secreto del servidor; TI lo consulta con pines_de_acceso.
"""
import datetime as dt
import hashlib
import hmac
import os

from . import servicio as s

ROLES = ["ASISTENCIAL", "CIRUJANO", "ADMINISTRATIVO", "INSUMOS", "CONTABLE", "CONSULTA", "TI"]
NIVEL = {1: "ALTA", 2: "MEDIA", 3: "BAJA"}

# Quién atiende cada «responsable» de la revisión activa
RESPONSABLE_A_ROL = {
    "Administrativo": ["ADMINISTRATIVO"],
    "Admisiones y enfermería": ["ADMINISTRATIVO", "ASISTENCIAL"],
    "Asistencial (coordinación de cirugía)": ["ASISTENCIAL"],
    "Asistencial (enfermería y anestesia)": ["ASISTENCIAL"],
    "Enfermería y anestesia": ["ASISTENCIAL"],
    "Enfermería": ["ASISTENCIAL"],
    "Equipo de sala": ["ASISTENCIAL"],
    "Central de esterilización y almacén": ["INSUMOS"],
    "Cirujano": ["ASISTENCIAL"],          # la confirmación del cirujano la registra coordinación
    "Contable (facturación)": ["CONTABLE"],
    "TI": ["TI"],
}

VISTAS = {
    "ASISTENCIAL": ["Mis pendientes", "Pacientes nuevos y habilitados", "Programación", "Preparación", "Ingreso del día", "Lista de chequeo", "Salidas"],
    "CIRUJANO": ["Procedimientos publicados del día", "Mis procedimientos"],
    "ADMINISTRATIVO": ["Mis pendientes", "Solicitudes por validar", "Autorizaciones", "Soportes", "Pacientes por citar", "Salidas"],
    "INSUMOS": ["Mis pendientes", "Insumos por verificar", "Novedades de recursos", "Soportes", "Salidas"],
    "CONTABLE": ["Mis pendientes", "Por facturar", "Facturas", "Glosas", "Costos", "Salidas"],
    "CONSULTA": ["Indicadores", "Riesgos", "Pacientes habilitados", "Salidas"],
    "TI": ["Estado", "Auditoría", "Alertas de seguridad", "PIN por rol", "Carga de datos"],
}


def _secreto():
    return (os.environ.get("SECRETO_PINES") or os.environ.get("CLAVE_QX_TI") or os.environ.get("DATABASE_URL") or "curso").encode()


def pin(equipo, rol):
    h = hmac.new(_secreto(), f"{int(equipo)}:{rol}".encode(), hashlib.sha256).hexdigest()
    return f"{int(h[:12], 16) % 1000000:06d}"


def pines(conn, equipo):
    s.auditar(conn, "TI", "consultar_pines", None, None, {"equipo": equipo})
    conn.commit()
    return {"equipo": equipo, "nota": "Entregue a cada persona solo el PIN de su rol. El PIN no cambia mientras no cambie el secreto del servidor.",
            "pines": [{"rol": r, "pin": pin(equipo, r)} for r in ROLES]}


def iniciar_sesion(conn, equipo, rol, cargo, pin_dado):
    cargo = (cargo or "").strip()
    if not cargo:
        raise ValueError("Escriba su cargo (por ejemplo 'Jefe de enfermería de cirugía'), no su nombre.")
    s.revisar_texto(conn, rol, "cargo", cargo)
    ok = hmac.compare_digest(str(pin_dado).strip(), pin(equipo, rol))
    s.auditar(conn, rol, "iniciar_sesion" if ok else "inicio_sesion_fallido", cargo, None, {"equipo": equipo})
    conn.commit()
    if not ok:
        return {"ok": False, "error": "PIN incorrecto para este rol. Pídale a TI de su grupo el PIN de su rol."}
    return {"ok": True, "rol": rol, "cargo": cargo, "equipo": equipo,
            "inicio": dt.datetime.now(s.ZoneInfo("America/Bogota")).isoformat(timespec="seconds"),
            "vistas_permitidas": VISTAS[rol],
            "solo_lectura": rol in ("CIRUJANO", "CONSULTA")}


def _item(urg, tipo, sq, detalle, limite, accion):
    return {"clave": f"{tipo}|{sq or hashlib.sha1(str(detalle).encode()).hexdigest()[:8]}", "urgencia": urg, "nivel": NIVEL[urg], "tipo": tipo, "sq": sq,
            "detalle": detalle, "hora_limite": limite, "accion_sugerida": accion}


ACCION = {
    "Cirugía bloqueada": "Resolver el bloqueo antes de programar",
    "Autorización pendiente": "Registrar la autorización y subir el soporte",
    "Falta para habilitar": "Completar lo que falta y subir los soportes",
    "Solicitud sin validar": "Validar la solicitud",
    "Insumos por verificar": "Verificar el insumo (disponible, estéril y vigente)",
    "Preparación pendiente": "Registrar la preparación",
    "Confirmación del cirujano": "Registrar la confirmación del cirujano",
    "Paciente sin ingreso": "Registrar el ingreso del paciente",
    "Atrasado sin programar": "Incluirlo en la propuesta de programación",
    "Alerta de seguridad": "Revisar la alerta y marcarla como revisada",
}


def mis_pendientes(conn, rol, cirujano=None):
    """Pendientes del rol ordenados por urgencia (ALTA, MEDIA, BAJA). Liviano: pensado para revisar cada minuto y avisar."""
    out = []
    if rol in ("ASISTENCIAL", "ADMINISTRATIVO", "INSUMOS", "TI", "CONSULTA"):
        t = s.tablero(conn, "ASISTENCIAL")
        for r in t["revision_activa"]:
            roles = RESPONSABLE_A_ROL.get(r["responsable"], ["ASISTENCIAL"])
            if rol == "CONSULTA":
                if r["urgencia"] != 1:
                    continue
            elif rol not in roles:
                continue
            x = _item(r["urgencia"], r["tipo"], r["sq"], r["detalle"], r["hora_limite"], ACCION.get(r["tipo"], "Revisar"))
            if rol == "CONSULTA":
                x["accion_sugerida"] = "Seguimiento (solo lectura): lo resuelve " + r["responsable"]
            out.append(x)
    elif rol == "CIRUJANO":
        hoy = s.hoy(conn)
        t =s.tablero(conn, "CIRUJANO", None, cirujano)
        for c in t["casos"]:
            sl = c.get("programado") or {}
            f = sl.get("fecha")
            if not f:
                continue
            d = (dt.date.fromisoformat(f) - hoy).days
            if 0 <= d <= 7:
                out.append(_item(1 if d == 0 else 2 if d == 1 else 3, "Procedimiento publicado", c["sq"],
                                 f"{c['procedimiento_nombre']} · {f} {sl.get('hora_inicio', '')} · sala {sl.get('sala', '')}",
                                 f"{f} {sl.get('hora_inicio', '')}", "Revisar el procedimiento (solo lectura)"))
    elif rol == "CONTABLE":
        for c in s.cirugias_por_facturar(conn)["por_facturar"]:
            alto = c["riesgo_glosa"] == "ALTO"
            out.append(_item(1 if alto else 2, "Cirugía por facturar", c["sq"],
                             f"{c['procedimiento']} · {c['aseguradora']}" + (" · " + "; ".join(c["glosas_probables"]) if alto else ""),
                             "Radicar dentro de 22 días hábiles", "Completar soportes y registrar la factura" if alto else "Registrar la factura"))
        for g in s.glosas(conn, solo_abiertas=True):
            dr = g["dias_habiles_restantes"]
            out.append(_item(1 if dr is not None and dr <= 3 else 2 if dr is not None and dr <= 7 else 3, "Glosa abierta", g["sq"],
                             f"{g['concepto']} {g['concepto_nombre']} · factura {g['numero_factura']} · ${g['valor']:,.0f}",
                             g["limite_respuesta"], "Responder la glosa (aceptar, aceptar parcial o rechazar con justificación)"))
    out.sort(key=lambda x: (x["urgencia"], str(x["hora_limite"]), x["sq"] or ""))
    resumen = {n: sum(1 for x in out if x["nivel"] == n) for n in ("ALTA", "MEDIA", "BAJA")}
    return {"rol": rol, "generado": dt.datetime.now(s.ZoneInfo("America/Bogota")).isoformat(timespec="seconds"),
            "resumen": resumen, "total": len(out), "pendientes": out}
