"""Servidor MCP del sistema de turnos UCI (curso). Cuatro conectores, uno por rol:

  /talento-humano/mcp   /jefe-uci/mcp   /nomina/mcp   /ti/mcp

Cada ruta exige su propia clave (variable de entorno CLAVE_<ROL>) en el encabezado
X-API-Key, en "Authorization: Bearer <clave>", como ?clave=<clave> o dentro de la ruta: /ti/<clave>/mcp.
Base de datos: modo compartido con DATABASE_URL (un esquema equipo_N por equipo, EQUIPOS=0,1,2,3)
o modo separado con EQUIPO_N_DATABASE_URL (una base por equipo). AUTOCARGAR=0 carga el equipo 0 al arrancar.
"""
import contextlib, hmac, json, os, re, datetime as dt
from decimal import Decimal

import psycopg
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from turnos import carga, servicio as s

SEGURIDAD = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _json(x):
    def conv(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return str(o)
    return json.dumps(x, ensure_ascii=False, default=conv, indent=1)


def _separados():
    return sorted(int(k.split("_")[1]) for k in os.environ if k.startswith("EQUIPO_") and k.endswith("_DATABASE_URL") and os.environ[k])


def _compartidos():
    if not os.environ.get("DATABASE_URL"):
        return []
    return sorted(int(x) for x in os.environ.get("EQUIPOS", "0,1,2,3").split(",") if x.strip())


def _equipos():
    return sorted(set(_separados()) | set(_compartidos()))


def esquema(equipo):
    return f"equipo_{int(equipo)}"


@contextlib.contextmanager
def conexion(equipo):
    """Modo separado: EQUIPO_N_DATABASE_URL (una base por equipo).
    Modo compartido: DATABASE_URL (una sola base) con un esquema equipo_N por equipo."""
    equipo = int(equipo)
    url = os.environ.get(f"EQUIPO_{equipo}_DATABASE_URL", "")
    if url:
        with psycopg.connect(url, connect_timeout=15) as conn:
            yield conn
        return
    if equipo in _compartidos():
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {esquema(equipo)}")
                cur.execute(f"SET search_path TO {esquema(equipo)}")
            conn.commit()
            yield conn
        return
    raise ValueError(f"El equipo {equipo} no tiene base configurada. Equipos disponibles: {_equipos()}.")


def ejecutar(equipo, fn, *a, **k):
    try:
        with conexion(equipo) as conn:
            return _json(fn(conn, *a, **k))
    except Exception as e:
        return _json({"error": str(e)})


def nuevo(nombre, instrucciones):
    return FastMCP(nombre, instructions=instrucciones, stateless_http=True, json_response=True, transport_security=SEGURIDAD)


COMUN = ("Sistema de turnos de la UCI (datos ficticios de un curso). Todas las herramientas piden el número de equipo (1, 2 o 3). "
         "Solo se manejan códigos de colaborador (EMP-01…), nunca nombres ni documentos. ")

# ------------------------------------------------------------------ TALENTO HUMANO
th = nuevo("Turnos UCI · Talento humano", COMUN + "Rol: Talento humano. Gestiona el personal y sus novedades.")


@th.tool()
def listar_colaboradores(equipo: int, servicio: str = "UCI", incluir_retirados: bool = False) -> str:
    """Lista el personal por código con cargo, rol, contrato, horas semanales, si acepta noches y si su identidad está registrada.
    servicio: 'UCI' (por defecto), 'Urgencias', 'Hospitalización' o '' para todos."""
    return ejecutar(equipo, s.colaboradores, servicio, incluir_retirados)


@th.tool()
def listar_novedades(equipo: int, mes: str = "", incluir_anuladas: bool = False) -> str:
    """Lista las novedades (vacaciones VAC, licencias LIC, incapacidades INC, ausencias AUS). mes: AAAA-MM o vacío para las últimas 100."""
    return ejecutar(equipo, s.listar_novedades, mes or None, incluir_anuladas)


@th.tool()
def registrar_novedad(equipo: int, codigo: str, tipo: str, desde: str, hasta: str, observacion: str, registrada_por: str) -> str:
    """Registra una novedad por código. tipo: VAC, LIC, INC o AUS. Fechas AAAA-MM-DD. No escriba diagnósticos ni datos de salud
    en la observación. registrada_por: cargo de quien registra (no su nombre). Avisa si ya hay programaciones afectadas."""
    return ejecutar(equipo, s.registrar_novedad, codigo, tipo, desde, hasta, observacion, registrada_por)


@th.tool()
def anular_novedad(equipo: int, id_novedad: int, motivo: str) -> str:
    """Anula (no borra) una novedad registrada por error. Queda en la auditoría."""
    return ejecutar(equipo, s.anular_novedad, id_novedad, motivo)


@th.tool()
def registrar_ingreso(equipo: int, id_cargo: str, fecha_ingreso: str, tipo_contrato: str, horas_contrato_semana: float,
                      acepta_noche: bool, registrada_por: str) -> str:
    """Registra una persona nueva SOLO con datos laborales y devuelve su código. id_cargo: ENF_JEFE, AUX_ENF o COORD_UCI.
    tipo_contrato: INDEFINIDO o FIJO. Nunca envíe nombres ni documentos: la identidad la carga TI dentro de la institución."""
    return ejecutar(equipo, s.registrar_ingreso, id_cargo, fecha_ingreso, tipo_contrato, horas_contrato_semana, acepta_noche, registrada_por)


@th.tool()
def registrar_retiro(equipo: int, codigo: str, fecha_retiro: str, registrada_por: str) -> str:
    """Registra la fecha de retiro (último día trabajado, AAAA-MM-DD) de un colaborador."""
    return ejecutar(equipo, s.registrar_retiro, codigo, fecha_retiro, registrada_por)


# ------------------------------------------------------------------ JEFE UCI
jefe = nuevo("Turnos UCI · Jefe de la UCI", COMUN + "Rol: Jefe o coordinación de la UCI. Programa, revisa y aprueba los turnos.")


@jefe.tool()
def obtener_datos_programacion(equipo: int, mes: str) -> str:
    """Devuelve TODO lo necesario para programar un mes (AAAA-MM): parámetros y reglas, cobertura, festivos, plantilla de
    personal con turnos (por código), novedades día por día y el estado de cierre del mes anterior aprobado."""
    return ejecutar(equipo, s.datos_programacion, mes)


@jefe.tool()
def guardar_borrador(equipo: int, mes: str, cuadro: dict[str, str], creada_por: str, semilla: int | None = None, observaciones: str = "") -> str:
    """Guarda un cuadro de turnos como BORRADOR (reemplaza el borrador anterior del mes). cuadro: {"EMP-01": "MT-NN--…"}, una letra
    por día del mes: M mañana, T tarde, N noche, - libre. El servidor verifica TODAS las reglas de nuevo y devuelve el resultado."""
    return ejecutar(equipo, s.guardar_borrador, mes, cuadro, creada_por, semilla, observaciones)


@jefe.tool()
def ver_programacion(equipo: int, mes: str, version: int | None = None) -> str:
    """Muestra la programación del mes (el borrador vigente o la aprobada), su verificación y el historial de versiones."""
    return ejecutar(equipo, s.ver_programacion, mes, version)


@jefe.tool()
def aprobar_programacion(equipo: int, mes: str, aprobada_por: str, justificacion_excepcion: str = "") -> str:
    """Aprueba el borrador vigente del mes. Si incumple reglas, solo se aprueba con una justificación de excepción.
    Al aprobar, el servidor clasifica las horas (diurnas, nocturnas, dominicales y festivas) y las publica para nómina.
    Antes de llamarla, confirme con la persona usuaria que quiere aprobar."""
    return ejecutar(equipo, s.aprobar, mes, aprobada_por, justificacion_excepcion)


@jefe.tool()
def listar_novedades_del_mes(equipo: int, mes: str) -> str:
    """Novedades del mes (solo lectura) para revisar antes de programar."""
    return ejecutar(equipo, s.listar_novedades, mes)


# ------------------------------------------------------------------ NÓMINA
nom = nuevo("Turnos UCI · Administración y nómina", COMUN + "Rol: Administración, contabilidad y nómina. Solo lectura.")


@nom.tool()
def horas_del_mes(equipo: int, mes: str) -> str:
    """Horas trabajadas por código en el mes calendario, clasificadas en ordinarias diurnas, ordinarias nocturnas (19:00–06:00),
    dominicales/festivas diurnas y dominicales/festivas nocturnas. Solo cuenta programaciones APROBADAS."""
    return ejecutar(equipo, s.horas_mes, mes)


@nom.tool()
def prenomina(equipo: int, mes: str) -> str:
    """Prenómina del mes por código: salario básico, valor hora (salario/divisor), recargos nocturno (35 %) y dominical/festivo
    (según la tabla de parámetros vigente), devengado, deducciones de salud y pensión (4 % y 4 %) y neto. Incluye las novedades
    que nómina debe procesar aparte."""
    return ejecutar(equipo, s.prenomina, mes)


@nom.tool()
def resumen_costos(equipo: int, meses: list[str]) -> str:
    """Totales por mes (horas por tipo, recargos, devengado, deducciones, neto) para comparar varios meses. meses: ["2026-09", ...]."""
    return ejecutar(equipo, s.resumen_costos, meses)


@nom.tool()
def parametros_de_nomina(equipo: int) -> str:
    """Tabla de parámetros legales con su vigencia (divisor de horas, recargos, aportes, salario mínimo) y su fuente normativa."""
    return ejecutar(equipo, lambda c: s._q(c, "SELECT * FROM parametros_nomina ORDER BY vigente_desde"))


# ------------------------------------------------------------------ TI
ti = nuevo("Turnos UCI · TI", COMUN + "Rol: TI. Carga inicial, estado del sistema y auditoría.")


@ti.tool()
def cargar_datos_iniciales(equipo: int, confirmar: bool = False, reiniciar: bool = False) -> str:
    """Crea las tablas y carga la base ficticia del curso (colaboradores, identidad, parámetros, festivos e historia de
    agosto a noviembre de 2026) en la base del equipo. Requiere confirmar=true. reiniciar=true BORRA todo y vuelve a cargar."""
    if not confirmar:
        return _json({"mensaje": "Vuelva a llamar con confirmar=true para cargar los datos."})
    try:
        with conexion(equipo) as conn:
            log = []
            res = carga.cargar(conn, reiniciar=reiniciar, informar=log.append)
            return _json({"cargado": True, "filas": res, "detalle": log})
    except Exception as e:
        return _json({"error": str(e)})


@ti.tool()
def estado_del_sistema(equipo: int) -> str:
    """Prueba la conexión con la base del equipo y muestra filas por tabla, programaciones y colaboradores sin identidad."""
    return ejecutar(equipo, s.estado_sistema)


@ti.tool()
def ver_auditoria(equipo: int, limite: int = 30) -> str:
    """Últimos eventos de auditoría: quién (rol), qué acción, cuándo y con qué detalle."""
    return ejecutar(equipo, s.ver_auditoria, limite)


@ti.tool()
def equipos_configurados() -> str:
    """Lista los equipos que tienen base registrada en el servidor (sin mostrar las URL)."""
    return _json({"equipos": _equipos()})


# ------------------------------------------------------------------ app y seguridad
SERVIDORES = {"talento-humano": (th, "CLAVE_TALENTO_HUMANO"), "jefe-uci": (jefe, "CLAVE_JEFE_UCI"),
              "nomina": (nom, "CLAVE_NOMINA"), "ti": (ti, "CLAVE_TI")}


class ConClave:
    """Envuelve una app ASGI y exige la clave del rol."""

    def __init__(self, app, var):
        self.app, self.var = app, var

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            esperada = os.environ.get(self.var, "")
            h = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            dada = h.get("x-api-key") or h.get("authorization", "").removeprefix("Bearer ").strip()
            if not dada:
                from urllib.parse import parse_qs
                dada = parse_qs(scope.get("query_string", b"").decode()).get("clave", [""])[0]
            if not esperada or not hmac.compare_digest(dada, esperada):
                await JSONResponse({"error": "Clave inválida o ausente para este rol."}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


PREPARACION = {}


def preparar_bases():
    """Al arrancar: crea los esquemas, los usuarios de base de TI (opcional) y carga los equipos indicados en AUTOCARGAR."""
    auto = {int(x) for x in os.environ.get("AUTOCARGAR", "").split(",") if x.strip()}
    for n in _compartidos():
        estado = {}
        try:
            with conexion(n) as conn:
                clave_bd = os.environ.get(f"CLAVE_BD_EQUIPO_{n}", "")
                if os.environ.get("CREAR_ROLES_TI") == "1" and clave_bd:
                    rol = f"ti_equipo_{n}"
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (rol,))
                            verbo = "ALTER" if cur.fetchone() else "CREATE"
                            cur.execute(f"{verbo} ROLE {rol} LOGIN PASSWORD '" + clave_bd.replace("'", "''") + "'")
                            cur.execute(f"GRANT USAGE, CREATE ON SCHEMA {esquema(n)} TO {rol}")
                            cur.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA {esquema(n)} TO PUBLIC")
                            cur.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA {esquema(n)} TO PUBLIC")
                        conn.commit()
                        estado["usuario_bd_ti"] = rol
                    except Exception as e:
                        conn.rollback()
                        estado["usuario_bd_ti"] = f"no se pudo crear ({type(e).__name__})"
                if n in auto:
                    if carga.tablas_con_datos(conn):
                        estado["autocarga"] = "ya tenía datos"
                    else:
                        carga.cargar(conn, informar=lambda *_: None)
                        estado["autocarga"] = "cargado"
                estado["conexion"] = "ok"
        except Exception as e:
            estado["conexion"] = f"error: {type(e).__name__}"
        PREPARACION[n] = estado


async def salud(request):
    return JSONResponse({"servicio": "turnos-uci", "estado": "activo", "equipos_configurados": _equipos(),
                         "modo": "compartido (un esquema por equipo)" if _compartidos() else "una base por equipo",
                         "preparacion": PREPARACION,
                         "conectores": [f"/{r}/mcp" for r in SERVIDORES]})


@contextlib.asynccontextmanager
async def ciclo_de_vida(app):
    preparar_bases()
    async with contextlib.AsyncExitStack() as pila:
        for srv, _ in SERVIDORES.values():
            await pila.enter_async_context(srv.session_manager.run())
        yield


_starlette = Starlette(
    routes=[Route("/", salud), Route("/salud", salud)]
    + [Mount(f"/{r}", app=ConClave(srv.streamable_http_app(), var)) for r, (srv, var) in SERVIDORES.items()],
    lifespan=ciclo_de_vida,
)

_RUTA_CON_CLAVE = re.compile(r"^/(" + "|".join(re.escape(r) for r in SERVIDORES) + r")/([^/]+)/mcp/?$")


async def app(scope, receive, send):
    """Permite poner la clave dentro de la ruta: /ti/<clave>/mcp (para clientes que no envían encabezados
    ni conservan ?clave= en la URL). Se reescribe a /ti/mcp con el encabezado X-API-Key."""
    if scope["type"] == "http":
        m = _RUTA_CON_CLAVE.match(scope.get("path", ""))
        if m and m.group(2) != "mcp":
            scope = dict(scope)
            nueva = f"/{m.group(1)}/mcp"
            scope["path"] = nueva
            scope["raw_path"] = nueva.encode()
            scope["headers"] = [h for h in scope.get("headers", []) if h[0].lower() != b"x-api-key"] + [(b"x-api-key", m.group(2).encode())]
    await _starlette(scope, receive, send)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
