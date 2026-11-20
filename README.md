# Servidor de turnos UCI (MCP) · Diplomado IA con Claude

Servidor MCP con cuatro conectores, uno por rol, sobre una base PostgreSQL por equipo.
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
