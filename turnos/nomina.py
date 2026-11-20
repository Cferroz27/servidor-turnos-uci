"""Clasificación de horas y prenómina según la norma colombiana vigente en 2026.

- Trabajo nocturno: de 19:00 a 06:00 (Ley 2466 de 2025, desde el 25/12/2025). Recargo 35 %.
- Domingos y festivos: recargo del 90 % desde el 01/07/2026 (100 % desde el 01/07/2027).
- Valor de la hora ordinaria: salario mensual / 210 desde el 15/07/2026 (jornada de 42 h).
Todos los valores salen de la tabla parametros_nomina: si la ley cambia, se cambia la tabla, no el código.
Esta versión no liquida horas extra, incapacidades ni licencias: se reportan para que nómina las procese.
"""
import datetime as dt
from collections import defaultdict

TIPOS = ("ordinarias_diurnas", "ordinarias_nocturnas", "dominicales_diurnas", "dominicales_nocturnas")


def clasificar_turno(fecha, hora_inicio, horas, festivos, ini_noc=19, fin_noc=6):
    """Reparte las horas de un turno por día calendario y tipo. Devuelve {fecha: {tipo: horas}}."""
    out = defaultdict(lambda: defaultdict(float))
    inicio = dt.datetime.combine(fecha, dt.time(hora_inicio))
    for k in range(int(horas)):
        h = inicio + dt.timedelta(hours=k)
        dia = h.date()
        nocturna = h.hour >= ini_noc or h.hour < fin_noc
        especial = dia.weekday() == 6 or dia in festivos
        tipo = ("dominicales_" if especial else "ordinarias_") + ("nocturnas" if nocturna else "diurnas")
        out[dia][tipo] += 1
    return out


def liquidar_horas(asignaciones, turnos, festivos, ini_noc=19, fin_noc=6):
    """asignaciones: [(codigo, fecha, turno)] → {(codigo, fecha_calendario): {tipo: horas}}.
    Las horas de la noche que pasan a otro día se registran en el día en que ocurren."""
    res = defaultdict(lambda: defaultdict(float))
    for codigo, fecha, turno in asignaciones:
        t = turnos[turno]
        for dia, tipos in clasificar_turno(fecha, t["hora_inicio"], t["horas"], festivos, ini_noc, fin_noc).items():
            for tipo, h in tipos.items():
                res[(codigo, dia)][tipo] += h
    return res


def prenomina_persona(salario, horas, par, dias_laborados=30):
    """horas: {tipo: total del mes}. par: fila de parametros_nomina. Valores en pesos, redondeados."""
    divisor = float(par["divisor_horas"])
    rn, rd = float(par["recargo_nocturno"]), float(par["recargo_dominical"])
    valor_hora = float(salario) / divisor
    basico = float(salario) * dias_laborados / 30
    rec_noc = horas.get("ordinarias_nocturnas", 0) * valor_hora * rn
    rec_dd = horas.get("dominicales_diurnas", 0) * valor_hora * rd
    rec_dn = horas.get("dominicales_nocturnas", 0) * valor_hora * (rd + rn)
    devengado = basico + rec_noc + rec_dd + rec_dn
    salud = devengado * float(par["aporte_salud"])
    pension = devengado * float(par["aporte_pension"])
    r = lambda x: int(round(x))
    return {
        "salario_base": r(salario), "dias_laborados": dias_laborados, "valor_hora_ordinaria": r(valor_hora),
        "salario_basico_mes": r(basico),
        "recargo_nocturno": r(rec_noc), "recargo_dominical_diurno": r(rec_dd), "recargo_dominical_nocturno": r(rec_dn),
        "total_recargos": r(rec_noc + rec_dd + rec_dn), "total_devengado": r(devengado),
        "deduccion_salud": r(salud), "deduccion_pension": r(pension), "neto_a_pagar": r(devengado - salud - pension),
    }
