-- Base de datos del sistema de turnos UCI · Clínica ficticia "Salud del Valle"
-- Todo el contenido es ficticio, creado para el diplomado.

CREATE TABLE IF NOT EXISTS cargos (
  id_cargo        TEXT PRIMARY KEY,
  nombre          TEXT NOT NULL,
  rol             TEXT NOT NULL CHECK (rol IN ('JEFE','AUX','COORD')),
  salario_base    NUMERIC(12,0) NOT NULL,
  hace_turnos     BOOLEAN NOT NULL
);

-- Datos laborales: lo único que ve Claude (por código)
CREATE TABLE IF NOT EXISTS colaboradores (
  codigo                 TEXT PRIMARY KEY,
  id_cargo               TEXT NOT NULL REFERENCES cargos(id_cargo),
  servicio               TEXT NOT NULL,
  tipo_contrato          TEXT NOT NULL CHECK (tipo_contrato IN ('INDEFINIDO','FIJO')),
  fecha_ingreso          DATE NOT NULL,
  fecha_retiro           DATE,
  horas_contrato_semana  NUMERIC(4,1) NOT NULL,
  acepta_noche           BOOLEAN NOT NULL DEFAULT TRUE,
  activo                 BOOLEAN NOT NULL DEFAULT TRUE
);

-- Datos personales: NUNCA salen por el conector. Solo los usa nómina dentro de la institución.
CREATE TABLE IF NOT EXISTS identidad (
  codigo            TEXT PRIMARY KEY REFERENCES colaboradores(codigo),
  tipo_documento    TEXT NOT NULL,
  numero_documento  TEXT NOT NULL UNIQUE,
  nombres           TEXT NOT NULL,
  apellidos         TEXT NOT NULL,
  fecha_nacimiento  DATE,
  celular           TEXT,
  correo            TEXT,
  direccion         TEXT,
  ciudad            TEXT,
  eps               TEXT,
  fondo_pensiones   TEXT,
  banco             TEXT,
  tipo_cuenta       TEXT,
  numero_cuenta     TEXT
);

CREATE TABLE IF NOT EXISTS tipos_turno (
  codigo       TEXT PRIMARY KEY,
  nombre       TEXT NOT NULL,
  hora_inicio  INT NOT NULL,
  horas        INT NOT NULL
);

CREATE TABLE IF NOT EXISTS cobertura (
  turno   TEXT REFERENCES tipos_turno(codigo),
  rol     TEXT NOT NULL,
  minimo  INT NOT NULL,
  PRIMARY KEY (turno, rol)
);

CREATE TABLE IF NOT EXISTS festivos (
  fecha   DATE PRIMARY KEY,
  nombre  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parametros_programacion (
  clave  TEXT PRIMARY KEY,
  valor  TEXT NOT NULL,
  descripcion TEXT
);

CREATE TABLE IF NOT EXISTS parametros_nomina (
  vigente_desde      DATE PRIMARY KEY,
  divisor_horas      INT NOT NULL,
  recargo_nocturno   NUMERIC(5,4) NOT NULL,
  recargo_dominical  NUMERIC(5,4) NOT NULL,
  hora_inicio_nocturna INT NOT NULL,
  hora_fin_nocturna  INT NOT NULL,
  aporte_salud       NUMERIC(5,4) NOT NULL,
  aporte_pension     NUMERIC(5,4) NOT NULL,
  smmlv              NUMERIC(12,0) NOT NULL,
  fuente             TEXT
);

CREATE TABLE IF NOT EXISTS novedades (
  id              SERIAL PRIMARY KEY,
  codigo          TEXT NOT NULL REFERENCES colaboradores(codigo),
  tipo            TEXT NOT NULL CHECK (tipo IN ('VAC','LIC','INC','AUS')),
  desde           DATE NOT NULL,
  hasta           DATE NOT NULL,
  observacion     TEXT,
  registrada_por  TEXT NOT NULL,
  registrada_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
  anulada         BOOLEAN NOT NULL DEFAULT FALSE,
  CHECK (hasta >= desde)
);

CREATE TABLE IF NOT EXISTS programaciones (
  id            SERIAL PRIMARY KEY,
  mes           TEXT NOT NULL,
  version       INT NOT NULL,
  estado        TEXT NOT NULL CHECK (estado IN ('BORRADOR','APROBADA','REEMPLAZADA','DESCARTADA')),
  semilla       INT,
  observaciones TEXT,
  verificacion  JSONB,
  estado_cierre JSONB,
  creada_por    TEXT NOT NULL,
  creada_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
  aprobada_por  TEXT,
  aprobada_en   TIMESTAMPTZ,
  justificacion_excepcion TEXT,
  UNIQUE (mes, version)
);

CREATE TABLE IF NOT EXISTS asignaciones (
  programacion_id  INT NOT NULL REFERENCES programaciones(id) ON DELETE CASCADE,
  codigo           TEXT NOT NULL REFERENCES colaboradores(codigo),
  fecha            DATE NOT NULL,
  turno            TEXT NOT NULL REFERENCES tipos_turno(codigo),
  PRIMARY KEY (programacion_id, codigo, fecha)
);

-- Horas clasificadas por día calendario (la noche se reparte entre los dos días que toca)
CREATE TABLE IF NOT EXISTS horas_liquidadas (
  programacion_id        INT NOT NULL REFERENCES programaciones(id) ON DELETE CASCADE,
  codigo                 TEXT NOT NULL REFERENCES colaboradores(codigo),
  fecha                  DATE NOT NULL,
  ordinarias_diurnas     NUMERIC(5,1) NOT NULL DEFAULT 0,
  ordinarias_nocturnas   NUMERIC(5,1) NOT NULL DEFAULT 0,
  dominicales_diurnas    NUMERIC(5,1) NOT NULL DEFAULT 0,
  dominicales_nocturnas  NUMERIC(5,1) NOT NULL DEFAULT 0,
  PRIMARY KEY (programacion_id, codigo, fecha)
);

CREATE TABLE IF NOT EXISTS auditoria (
  id       SERIAL PRIMARY KEY,
  momento  TIMESTAMPTZ NOT NULL DEFAULT now(),
  rol      TEXT NOT NULL,
  accion   TEXT NOT NULL,
  detalle  JSONB
);

-- Horas por mes CALENDARIO (la noche del 31 que termina el día 1 cuenta en el mes siguiente)
CREATE OR REPLACE VIEW v_horas_mes AS
SELECT to_char(h.fecha, 'YYYY-MM') AS mes, h.codigo, c.id_cargo,
       SUM(h.ordinarias_diurnas)    AS ordinarias_diurnas,
       SUM(h.ordinarias_nocturnas)  AS ordinarias_nocturnas,
       SUM(h.dominicales_diurnas)   AS dominicales_diurnas,
       SUM(h.dominicales_nocturnas) AS dominicales_nocturnas
FROM horas_liquidadas h
JOIN programaciones p ON p.id = h.programacion_id AND p.estado = 'APROBADA'
JOIN colaboradores c ON c.codigo = h.codigo
GROUP BY to_char(h.fecha, 'YYYY-MM'), h.codigo, c.id_cargo;
