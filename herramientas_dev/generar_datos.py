"""Genera la base ficticia (CSV) del sistema de turnos UCI, con historia de agosto a noviembre de 2026.
Uso: python herramientas_dev/generar_datos.py   (escribe en turnos/datos/)
"""
import csv, json, random, sys, datetime as dt
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from turnos import motor, nomina

OUT = RAIZ / "turnos" / "datos"
OUT.mkdir(parents=True, exist_ok=True)
rnd = random.Random(2026)


def guardar(nombre, cab, filas):
    with open(OUT / nombre, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cab)
        w.writerows(filas)


cargos = [
    ("ENF_JEFE", "Enfermero(a) jefe", "JEFE", 4800000, True),
    ("AUX_ENF", "Auxiliar de enfermería", "AUX", 2300000, True),
    ("COORD_UCI", "Coordinador(a) de enfermería UCI", "COORD", 7200000, False),
]
guardar("cargos.csv", ["id_cargo", "nombre", "rol", "salario_base", "hace_turnos"], cargos)

nombres = [
    ("Luz Marina", "Ocampo Ríos"), ("Andrés Felipe", "Cárdenas Mora"), ("Gloria Patricia", "Sánchez Vélez"),
    ("Jhon Jairo", "Restrepo Gil"), ("Diana Carolina", "Mejía Torres"), ("Héctor Fabio", "Lozano Pérez"),
    ("Yuliana Andrea", "Castaño López"), ("María Fernanda", "Giraldo Ruiz"), ("Carlos Alberto", "Muñoz Arias"),
    ("Paola Andrea", "Quintero Díaz"), ("Sandra Milena", "Valencia Rojas"), ("Leidy Johana", "Escobar Marín"),
    ("Juan David", "Hoyos Cifuentes"), ("Natalia", "Ospina Bermúdez"), ("Wilmer Arley", "Gómez Cano"),
    ("Viviana Marcela", "Agudelo Soto"), ("Claudia Lorena", "Bedoya Franco"), ("Erika Tatiana", "Zapata Henao"),
    ("Brayan Stiven", "Rincón Vargas"), ("Ana Lucía", "Morales Patiño"), ("Martha Cecilia", "Salazar Duque"),
    ("Óscar Iván", "Benítez Lasso"), ("Kelly Johana", "Riascos Mina"), ("Fredy Alexander", "Caicedo Angulo"),
]
colab, ident = [], []
for i, (n, a) in enumerate(nombres, start=1):
    cod = f"EMP-{i:02d}"
    if i <= 6:
        cargo, serv = "ENF_JEFE", "UCI"
    elif i <= 20:
        cargo, serv = "AUX_ENF", "UCI"
    elif i == 21:
        cargo, serv = "COORD_UCI", "UCI"
    elif i == 22:
        cargo, serv = "ENF_JEFE", "Urgencias"
    else:
        cargo, serv = "AUX_ENF", "Hospitalización"
    contrato = "FIJO" if i in (18, 19, 24) else "INDEFINIDO"
    ingreso = dt.date(2015 + (i * 7) % 11, 1 + (i * 5) % 12, 1 + (i * 3) % 27)
    horas = 24 if i == 17 else (42 if i == 21 else 40)
    noche = "false" if i in (3, 21) else "true"
    colab.append((cod, cargo, serv, contrato, ingreso.isoformat(), "", horas, noche, "true"))
    doc = str(29000000 + i * 137911 if i % 3 else 1100000000 + i * 9137)
    nac = dt.date(1972 + (i * 3) % 27, 1 + (i * 7) % 12, 1 + (i * 11) % 27)
    u = (n.split()[0] + "." + a.split()[0]).lower()
    for x, y in zip("áéíóúñÓ", "aeiounO"):
        u = u.replace(x, y)
    ident.append((cod, "CC", doc, n, a, nac.isoformat(), f"3{(10 + i * 7) % 90 + 10} {(450 + i * 31) % 900 + 100} {(1000 + i * 77) % 9000 + 1000}",
                  f"{u.lower()}@correo-ejemplo.co", f"Calle {10 + i * 3} # {20 + i}-{i + 5}", ["Cali", "Jamundí", "Palmira", "Yumbo"][i % 4],
                  ["EPS Valle Salud", "EPS Andina", "EPS Pacífico"][i % 3], ["Fondo Porvenir Ficticio", "Colpensiones (ficticio)", "Fondo Protección Ficticio"][i % 3],
                  ["Banco Ejemplo", "Banco Demostración"][i % 2], ["AHORROS", "CORRIENTE"][i % 2], f"FICT-{100000 + i * 4513}"))
guardar("colaboradores.csv", ["codigo", "id_cargo", "servicio", "tipo_contrato", "fecha_ingreso", "fecha_retiro",
                              "horas_contrato_semana", "acepta_noche", "activo"], colab)
guardar("identidad.csv", ["codigo", "tipo_documento", "numero_documento", "nombres", "apellidos", "fecha_nacimiento", "celular",
                          "correo", "direccion", "ciudad", "eps", "fondo_pensiones", "banco", "tipo_cuenta", "numero_cuenta"], ident)

turnos = [("M", "Mañana", 7, 6), ("T", "Tarde", 13, 6), ("N", "Noche", 19, 12)]
guardar("tipos_turno.csv", ["codigo", "nombre", "hora_inicio", "horas"], turnos)
cob = [("M", "JEFE", 1), ("M", "AUX", 3), ("T", "JEFE", 1), ("T", "AUX", 3), ("N", "JEFE", 1), ("N", "AUX", 2)]
guardar("cobertura.csv", ["turno", "rol", "minimo"], cob)

festivos = [("2026-01-01", "Año Nuevo"), ("2026-01-12", "Reyes Magos"), ("2026-03-23", "San José"), ("2026-04-02", "Jueves Santo"),
            ("2026-04-03", "Viernes Santo"), ("2026-05-01", "Día del Trabajo"), ("2026-05-18", "Ascensión"), ("2026-06-08", "Corpus Christi"),
            ("2026-06-15", "Sagrado Corazón"), ("2026-06-29", "San Pedro y San Pablo"), ("2026-07-20", "Independencia"),
            ("2026-08-07", "Batalla de Boyacá"), ("2026-08-17", "Asunción de la Virgen"), ("2026-10-12", "Día de la Raza"),
            ("2026-11-02", "Todos los Santos"), ("2026-11-16", "Independencia de Cartagena"), ("2026-12-08", "Inmaculada Concepción"),
            ("2026-12-25", "Navidad"), ("2027-01-01", "Año Nuevo"), ("2027-01-11", "Reyes Magos")]
guardar("festivos.csv", ["fecha", "nombre"], festivos)

pprog = [("horas_max_semana", "40", "Tope semanal de la institución (la ley permite 42 desde el 15/07/2026)"),
         ("descanso_min_horas", "12", "Horas mínimas entre el fin de un turno y el inicio del siguiente"),
         ("prohibido_despues_de_noche", "M,T", "Turnos que no se asignan el día después de una noche"),
         ("max_dias_seguidos", "6", "Máximo de días seguidos trabajados"),
         ("max_noches_seguidas", "2", "Máximo de noches seguidas"),
         ("equidad_max_diferencia_noches", "2", "Diferencia máxima de noches entre personas del mismo rol")]
guardar("parametros_programacion.csv", ["clave", "valor", "descripcion"], pprog)

pnom = [("2026-01-01", 220, 0.35, 0.80, 19, 6, 0.04, 0.04, 1750905, "Ley 2466/2025: nocturno desde 19:00; dominical 80 %; Ley 2101/2021: 44 h"),
        ("2026-07-01", 220, 0.35, 0.90, 19, 6, 0.04, 0.04, 1750905, "Dominical/festivo 90 % desde 01/07/2026"),
        ("2026-07-15", 210, 0.35, 0.90, 19, 6, 0.04, 0.04, 1750905, "Jornada 42 h: divisor 210 desde 15/07/2026"),
        ("2027-07-01", 210, 0.35, 1.00, 19, 6, 0.04, 0.04, 1750905, "Dominical/festivo 100 % desde 01/07/2027 (actualizar SMMLV 2027)")]
guardar("parametros_nomina.csv", ["vigente_desde", "divisor_horas", "recargo_nocturno", "recargo_dominical", "hora_inicio_nocturna",
                                  "hora_fin_nocturna", "aporte_salud", "aporte_pension", "smmlv", "fuente"], pnom)

nov = [(1, "EMP-08", "VAC", "2026-08-10", "2026-08-23", "Vacaciones aprobadas", "Talento humano (histórico)", "false"),
       (2, "EMP-03", "VAC", "2026-09-14", "2026-09-27", "Vacaciones aprobadas", "Talento humano (histórico)", "false"),
       (3, "EMP-15", "INC", "2026-10-05", "2026-10-07", "Incapacidad (sin diagnóstico: dato reservado)", "Talento humano (histórico)", "false"),
       (4, "EMP-10", "LIC", "2026-10-20", "2026-10-20", "Licencia no remunerada", "Talento humano (histórico)", "false"),
       (5, "EMP-19", "VAC", "2026-11-09", "2026-11-22", "Vacaciones aprobadas", "Talento humano (histórico)", "false")]
guardar("novedades.csv", ["id", "codigo", "tipo", "desde", "hasta", "observacion", "registrada_por", "anulada"], nov)

# ---- historia: programaciones aprobadas de agosto a noviembre ----
P = {k: (int(v) if v.isdigit() else v.split(",")) for k, v, _ in pprog}
P["turnos"] = {c: {"nombre": n, "inicio": f"{h:02d}:00", "horas": hs, "hora_inicio": h} for c, n, h, hs in turnos}
P["orden_asignacion"] = ["N", "M", "T"]
P["cobertura"] = {}
for t, r, m in cob:
    P["cobertura"].setdefault(t, {})[r] = m
P["festivos"] = [f for f, _ in festivos]
personas = [{"codigo": c[0], "rol": "JEFE" if c[1] == "ENF_JEFE" else "AUX", "horas_contrato": float(c[6]), "acepta_noche": c[7] == "true"}
            for c in colab if c[2] == "UCI" and c[1] != "COORD_UCI"]
fest = {dt.date.fromisoformat(f) for f in P["festivos"]}
progs, asig, horas = [], [], []
prev = None
for k, mes in enumerate(["2026-08", "2026-09", "2026-10", "2026-11"], start=1):
    dias = motor.dias_del_mes(mes)
    aus = {}
    for _, cod, tipo, d1, d2, *_ in nov:
        a, b = dt.date.fromisoformat(d1), dt.date.fromisoformat(d2)
        d = a
        while d <= b:
            if d in dias:
                aus.setdefault(cod, {})[d] = tipo
            d += dt.timedelta(days=1)
    cuadro, huecos, sem = motor.programar(personas, mes, P, aus, prev, semilla=100 * k, intentos=60)
    ver = motor.verificar(personas, dias, cuadro, aus, P, prev)
    cierre = motor.estado_cierre(personas, dias, cuadro, P, aus, prev)
    aprob = dt.datetime.combine(dias[0] - dt.timedelta(days=6), dt.time(10, 30)).isoformat()
    progs.append((k, mes, 1, "APROBADA", sem, "Programación histórica", json.dumps(ver, ensure_ascii=False),
                  json.dumps(cierre, ensure_ascii=False), "Jefe UCI (histórico)", aprob, "Coordinación UCI (histórico)", aprob, ""))
    filas = [(c, d, t) for c in cuadro for d, t in cuadro[c].items()]
    for c, d, t in sorted(filas):
        asig.append((k, c, d.isoformat(), t))
    liq = nomina.liquidar_horas(filas, P["turnos"], fest)
    for (c, d), tp in sorted(liq.items()):
        horas.append((k, c, d.isoformat()) + tuple(tp.get(x, 0) for x in nomina.TIPOS))
    prev = cierre
    print(mes, "huecos", sum(h["faltan"] for h in huecos), "incumple", [r["regla"] for r in ver if not r["ok"]])
guardar("programaciones.csv", ["id", "mes", "version", "estado", "semilla", "observaciones", "verificacion", "estado_cierre", "creada_por",
                               "creada_en", "aprobada_por", "aprobada_en", "justificacion_excepcion"], progs)
guardar("asignaciones.csv", ["programacion_id", "codigo", "fecha", "turno"], asig)
guardar("horas_liquidadas.csv", ["programacion_id", "codigo", "fecha"] + list(nomina.TIPOS), horas)
print("Listo:", OUT)
