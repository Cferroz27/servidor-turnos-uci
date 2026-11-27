# Servidor MCP del diplomado IA con Claude: turnos UCI y proceso quirúrgico

Un solo servidor en Render con dos sistemas sobre la misma base PostgreSQL:
- **Turnos de la UCI**: cuatro conectores, uno por rol (esquemas `equipo_N`).
- **Proceso quirúrgico** (nuevo): siete conectores, uno por rol (esquemas `qx_equipo_N`). Ver la sección al final.
**Todos los datos son ficticios.**

| Conector | Ruta | Variable de la clave | Herramientas |
|---|---|---|---|
| Talento humano | `/talento-humano/mcp` | `CLAVE_TALENTO_HUMANO` | listar_colaboradores, listar_novedades, registrar_novedad, anular_novedad, registrar_ingreso, registrar_retiro |
| Jefe de la UCI | `/jefe-uci/mcp` | `CLAVE_JEFE_UCI` | obtener_datos_programacion, guardar_borrador, ver_programacion, aprobar_programacion, listar_novedades_del_mes |
| Administración y nómina | `/nomina/mcp` | `CLAVE_NOMINA` | horas_del_mes, prenomina, resumen_costos, parametros_de_nomina |
| TI | `/ti/mcp` | `CLAVE_TI` | cargar_datos_iniciales, estado_del_sistema, ver_auditoria, equipos_configurados |

La clave se envía en el encabezado `X-API-Key` (o `Authorization: Bearer …`) o dentro de la ruta: `https://SERVIDOR/ti/LA_CLAVE/mcp` (para cuentas que no permiten encabezados).

## Variables de entorno
- `CLAVE_TALENTO_HUMANO`, `CLAVE_JEFE_UCI`, `CLAVE_NOMINA`, `CLAVE_TI`: una clave larga y distinta por rol.
- **Modo compartido (recomendado para el curso):** `DATABASE_URL` (una sola base) y `EQUIPOS=0,1,2,3`. Cada equipo trabaja en su esquema `equipo_N`. El equipo 0 es la demostración del docente.
  - `AUTOCARGAR=0`: carga los datos del equipo 0 al arrancar, si está vacío.
  - `CREAR_ROLES_TI=1` y `CLAVE_BD_EQUIPO_N`: crea el usuario de base `ti_equipo_N`, que solo puede entrar al esquema de su equipo (para los scripts de Python de TI).
- **Modo separado:** `EQUIPO_N_DATABASE_URL`, una base por equipo.
- `PYTHON_VERSION`: 3.12.7

## Estructura
- `app.py`: servidor, rutas por rol y control de claves.
- `turnos/esquema.sql`: tablas (cargos, colaboradores, identidad, novedades, programaciones, asignaciones, horas_liquidadas, parámetros, auditoría).
- `turnos/datos/`: la base ficticia (24 colaboradores e historia de agosto a noviembre de 2026).
- `turnos/motor.py`: asignación y verificación de reglas. `turnos/nomina.py`: clasificación de horas y prenómina. `turnos/servicio.py`: reglas de negocio.
- `herramientas_dev/generar_datos.py`: regenera la base ficticia.

## Normas usadas (parámetros en la tabla `parametros_nomina`)
Nocturno de 19:00 a 06:00 con recargo del 35 % (Ley 2466 de 2025) · dominical y festivo 90 % desde el 01/07/2026 y 100 % desde el 01/07/2027 · divisor 210 desde el 15/07/2026 (jornada de 42 h, Ley 2101 de 2021) · salario mínimo 2026: $1.750.905.
No liquida horas extra, incapacidades, licencias, auxilio de transporte ni aportes del empleador.

## Probar en local
```bash
pip install -r requirements.txt
export CLAVE_TI=prueba EQUIPO_1_DATABASE_URL="postgresql://..."
python app.py    # http://localhost:8000/salud
```


---

## Proceso quirúrgico (módulo `quirofanos/`)

Lleva cada cirugía por sus 10 etapas, de la solicitud a la factura y la glosa, con las reglas de POL-QX-002 (ficticia)
verificadas en el servidor. Los pacientes existen solo como código SQ.

| Conector (nombre en Claude) | Ruta | Variable de la clave | Para |
|---|---|---|---|
| `qx asistencial` | `/qx-asistencial/mcp` | `CLAVE_QX_ASISTENCIAL` | Programación (proponer, mover, aprobar), preparación, ingreso, lista de chequeo, horas reales, egreso, cancelación |
| `qx cirujano` | `/qx-cirujano/mcp` | `CLAVE_QX_CIRUJANO` | Solicitudes, mis casos, confirmación, insumos especiales, notas |
| `qx administrativo` | `/qx-administrativo/mcp` | `CLAVE_QX_ADMINISTRATIVO` | Validación de solicitudes, autorizaciones, citación |
| `qx insumos` | `/qx-insumos/mcp` | `CLAVE_QX_INSUMOS` | Central de esterilización, almacén, novedades de equipos y salas |
| `qx contable` | `/qx-contable/mcp` | `CLAVE_QX_CONTABLE` | Costos, facturas, radicación, glosas (Res. 2284 de 2023) |
| `qx consulta` | `/qx-consulta/mcp` | `CLAVE_QX_CONSULTA` | Jurídico, análisis de datos y gerencia (solo lectura) |
| `qx ti` | `/qx-ti/mcp` | `CLAVE_QX_TI` | Carga de datos, estado, auditoría, alertas de seguridad, fecha del curso |

Todos tienen además cinco servicios comunes:
- `tablero_quirurgico`: toda la información que necesita un artefacto, recortada según el rol (sin costos para los roles clínicos).
- `servicios_disponibles`: catálogo de las herramientas del conector (argumentos y para qué sirven) y la estructura del tablero. Es lo primero que se consulta para construir un artefacto.
- `guardar_salida`, `listar_salidas`, `ver_salida`: cada área guarda en el servidor sus informes (texto + datos JSON) y las demás los consultan.

TI tiene `crear_base_del_equipo` (con datos de ejemplo o vacía, solo con catálogos) para que cada grupo trabaje en su propia base.

**Reglas que verifica el servidor** (`quirofanos/reglas.py`): prioridad y días de espera; P2 y P3 solo con autorización aprobada y vigente
(P1 en paralelo); sala con su equipo, disponible y con mantenimiento al día; Q6 solo P1 de 7:00 a 11:00; máximo 2 UCI por día; cirujano
en sus días y horas y nunca en dos salas; duración + 30 min de rotación; última cirugía a las 17:00; insumo faltante o vencido bloquea;
salas simultáneas ≤ anestesiólogos; sin festivos. Una programación con errores **no se puede aprobar**, y «Listo para sala» solo se
marca con las etapas 2 a 7 completas.

**Seguridad**: el texto libre con documentos, celulares o correos se rechaza; el que trae instrucciones («ignora la política…»)
se guarda como dato y genera una alerta para TI. Todo cambio queda en `auditoria` con el rol y el cargo.

**Datos**: `cargar_datos_quirurgicos` (conector TI) genera datos ficticios **relativos a la fecha de carga**: 36 casos en lista de
espera, la semana siguiente por programar y 4 semanas de historia con tiempos, cancelaciones, facturas y glosas. `AUTOCARGAR_QX=0`
carga el equipo 0 al arrancar. `fijar_fecha_del_curso` fija el «hoy» del sistema para practicar un día concreto.

Pruebas de punta a punta: 51 verificaciones con un cliente MCP real (claves por rol, reglas, bloqueos, flujo completo hasta la glosa).
