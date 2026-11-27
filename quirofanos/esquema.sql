-- Base de datos del proceso quirúrgico · IPS ficticia "Salud del Valle" (diplomado IA con Claude)
-- Todo el contenido es ficticio. Los pacientes solo existen como código SQ: no hay nombres, documentos ni teléfonos.
-- Política de referencia: POL-QX-002 (ficticia). Glosas: conceptos de la Resolución 2284 de 2023 (simplificado para el curso).

-- ============================================================ CATÁLOGOS Y RECURSOS
CREATE TABLE IF NOT EXISTS parametros (
  clave        TEXT PRIMARY KEY,
  valor        TEXT NOT NULL,
  descripcion  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS salas (
  id_sala      TEXT PRIMARY KEY,                 -- Q1 … Q6
  descripcion  TEXT NOT NULL,
  solo_p1      BOOLEAN NOT NULL DEFAULT FALSE,   -- Q6: urgencias, programable solo P1
  hora_inicio  TIME NOT NULL DEFAULT '07:00',
  hora_fin     TIME NOT NULL DEFAULT '19:00'
);

CREATE TABLE IF NOT EXISTS equipos_biomedicos (
  id_equipo            TEXT PRIMARY KEY,         -- EQ-LAP-01
  tipo                 TEXT NOT NULL,            -- LAPAROSCOPIA, ARCO_C, …
  descripcion          TEXT NOT NULL,
  id_sala              TEXT NOT NULL REFERENCES salas(id_sala),
  mantenimiento_vence  DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS cirujanos (
  codigo        TEXT PRIMARY KEY,                -- CIR-01
  especialidad  TEXT NOT NULL,
  dias          TEXT NOT NULL,                   -- 1=lunes … 5=viernes, separados por coma: '1,3,5'
  hora_fin      TIME NOT NULL DEFAULT '19:00',   -- algunos solo operan hasta las 12:00
  activo        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS aseguradoras (
  id_aseguradora  TEXT PRIMARY KEY,              -- ASG-01
  nombre          TEXT NOT NULL,                 -- ficticias
  tipo            TEXT NOT NULL CHECK (tipo IN ('EPS','PREPAGADA','SOAT','PARTICULAR'))
);

CREATE TABLE IF NOT EXISTS procedimientos (
  codigo             TEXT PRIMARY KEY,           -- PX-101 (código ficticio, no es CUPS)
  nombre             TEXT NOT NULL,
  especialidad       TEXT NOT NULL,
  tipo_cirugia       TEXT NOT NULL CHECK (tipo_cirugia IN ('AMBULATORIA','HOSPITALIZACION','UCI')),
  duracion_min       INT NOT NULL,
  equipo_requerido   TEXT,                       -- tipo de equipo biomédico (NULL = básicos)
  set_instrumental   TEXT NOT NULL,
  requiere_implante  BOOLEAN NOT NULL DEFAULT FALSE,
  requiere_sangre    BOOLEAN NOT NULL DEFAULT FALSE,
  costo_estimado     NUMERIC(12,0) NOT NULL,     -- costo interno
  tarifa             NUMERIC(12,0) NOT NULL      -- valor pactado con la aseguradora
);

CREATE TABLE IF NOT EXISTS novedades_recursos (
  id           SERIAL PRIMARY KEY,
  fecha        DATE NOT NULL,
  tipo         TEXT NOT NULL CHECK (tipo IN ('EQUIPO','SALA','CIRUJANO')),
  recurso      TEXT NOT NULL,                    -- EQ-ARC-01, Q3, CIR-09
  descripcion  TEXT NOT NULL,
  registrada_por TEXT NOT NULL,
  creada       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================ EL CASO QUIRÚRGICO (una fila por paciente-cirugía)
CREATE TABLE IF NOT EXISTS casos (
  sq                  TEXT PRIMARY KEY,           -- SQ-0101: único dato del paciente que circula
  procedimiento       TEXT NOT NULL REFERENCES procedimientos(codigo),
  cirujano            TEXT NOT NULL REFERENCES cirujanos(codigo),
  prioridad           TEXT NOT NULL CHECK (prioridad IN ('P1','P2','P3')),
  requiere_uci        BOOLEAN NOT NULL DEFAULT FALSE,
  id_aseguradora      TEXT NOT NULL REFERENCES aseguradoras(id_aseguradora),
  fecha_solicitud     DATE NOT NULL,
  solicitud_validada  BOOLEAN,                    -- NULL = sin revisar · TRUE · FALSE (rechazada)
  validada_por        TEXT,
  estado              TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','REALIZADO','CANCELADO','RECHAZADO')),
  hora_citacion       TIME,
  observaciones       TEXT,
  creado              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS autorizaciones (
  id              SERIAL PRIMARY KEY,
  sq              TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  estado          TEXT NOT NULL CHECK (estado IN ('APROBADA','PENDIENTE','NEGADA')),
  numero          TEXT,                           -- número de autorización de la aseguradora
  vigente_hasta   DATE,
  registrada_por  TEXT NOT NULL,
  observacion     TEXT,
  momento         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- La autorización vigente de un caso es la última registrada.

-- ============================================================ PROGRAMACIÓN (borradores y aprobadas, con versiones)
CREATE TABLE IF NOT EXISTS programaciones (
  id              SERIAL PRIMARY KEY,
  semana          DATE NOT NULL,                  -- lunes de la semana
  version         INT NOT NULL,
  estado          TEXT NOT NULL CHECK (estado IN ('BORRADOR','APROBADA','REEMPLAZADA')),
  creada_por      TEXT NOT NULL,
  creada          TIMESTAMPTZ NOT NULL DEFAULT now(),
  aprobada_por    TEXT,
  aprobada        TIMESTAMPTZ,
  verificacion    JSONB,
  observaciones   TEXT,
  UNIQUE (semana, version)
);

CREATE TABLE IF NOT EXISTS programacion_casos (
  programacion_id  INT NOT NULL REFERENCES programaciones(id) ON DELETE CASCADE,
  sq               TEXT NOT NULL REFERENCES casos(sq),
  fecha            DATE NOT NULL,
  id_sala          TEXT NOT NULL REFERENCES salas(id_sala),
  hora_inicio      TIME NOT NULL,
  hora_fin         TIME NOT NULL,                 -- inicio + duración estimada (la rotación de 30 min va después)
  PRIMARY KEY (programacion_id, sq)
);

-- ============================================================ ETAPAS 4 A 9
-- Preparación prequirúrgica (enfermería y anestesia)
CREATE TABLE IF NOT EXISTS preparacion (
  sq           TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  item         TEXT NOT NULL CHECK (item IN ('VALORACION_PREANESTESICA','EXAMENES','CONSENTIMIENTO_INFORMADO','RESERVA_SANGRE','CAMA_UCI')),
  estado       TEXT NOT NULL CHECK (estado IN ('OK','PENDIENTE','NO_APLICA')),
  vence        DATE,                              -- vigencia del consentimiento o de la valoración
  responsable  TEXT,                              -- cargo que confirma
  momento      TIMESTAMPTZ,
  PRIMARY KEY (sq, item)
);

-- Verificación de insumos (central de esterilización y almacén)
CREATE TABLE IF NOT EXISTS insumos_caso (
  id           SERIAL PRIMARY KEY,
  sq           TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  tipo         TEXT NOT NULL CHECK (tipo IN ('SET_INSTRUMENTAL','MATERIAL','EQUIPO_BIOMEDICO','IMPLANTE','INSUMO_ESPECIAL')),
  descripcion  TEXT NOT NULL,
  estado       TEXT NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('OK','PENDIENTE','FALTANTE')),
  vence        DATE,                              -- vencimiento de la esterilización o del producto
  registro     TEXT,                              -- lote, registro INVIMA ficticio o sticker del implante
  responsable  TEXT,
  momento      TIMESTAMPTZ,
  costo        NUMERIC(12,0) NOT NULL DEFAULT 0
);

-- Confirmación del cirujano
CREATE TABLE IF NOT EXISTS confirmaciones (
  sq                  TEXT PRIMARY KEY REFERENCES casos(sq) ON DELETE CASCADE,
  confirmado          BOOLEAN NOT NULL,
  ayudante            TEXT,                       -- código del ayudante (CIR-xx o AYU-xx)
  insumos_especiales  TEXT,
  momento             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ingreso del paciente el día de la cirugía (admisiones y enfermería)
CREATE TABLE IF NOT EXISTS ingreso (
  sq           TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  item         TEXT NOT NULL CHECK (item IN ('LLEGADA','IDENTIFICACION','AYUNO','CONSENTIMIENTO_FIRMADO','RETIRO_PROTESIS_JOYAS','MARCACION_SITIO','LISTO_PARA_SALA')),
  responsable  TEXT NOT NULL,
  momento      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sq, item)
);

-- Lista de chequeo de cirugía segura (tres momentos)
CREATE TABLE IF NOT EXISTS lista_chequeo (
  sq           TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  momento      TEXT NOT NULL CHECK (momento IN ('ANTES_ANESTESIA','ANTES_INCISION','ANTES_SALIR')),
  items        JSONB NOT NULL,                    -- {"identidad_confirmada": true, …}
  completo     BOOLEAN NOT NULL,
  responsable  TEXT NOT NULL,
  registrado   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sq, momento)
);

-- Horas reales y egreso
CREATE TABLE IF NOT EXISTS tiempos (
  sq        TEXT PRIMARY KEY REFERENCES casos(sq) ON DELETE CASCADE,
  fecha     DATE NOT NULL,
  id_sala   TEXT REFERENCES salas(id_sala),
  entrada   TIME,
  incision  TIME,
  cierre    TIME,
  salida    TIME,
  destino   TEXT CHECK (destino IN ('CASA','HOSPITALIZACION','UCI')),
  registrado_por TEXT
);

CREATE TABLE IF NOT EXISTS cancelaciones (
  id           SERIAL PRIMARY KEY,
  sq           TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  fecha        DATE NOT NULL,
  motivo       TEXT NOT NULL CHECK (motivo IN ('PACIENTE_NO_ASISTE','SIN_AUTORIZACION','FALTA_INSUMO','PREPARACION_INCOMPLETA',
                                               'CIRUGIA_ANTERIOR_EXTENDIDA','SIN_CAMA_UCI','DECISION_CLINICA','OTRO')),
  mismo_dia    BOOLEAN NOT NULL DEFAULT FALSE,
  detalle      TEXT,
  responsable  TEXT NOT NULL,
  momento      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notas (
  id        SERIAL PRIMARY KEY,
  sq        TEXT NOT NULL REFERENCES casos(sq) ON DELETE CASCADE,
  autor     TEXT NOT NULL,                        -- cargo o código (CIR-03), nunca nombres
  texto     TEXT NOT NULL,
  momento   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================ ETAPA 10: FACTURACIÓN Y GLOSAS
CREATE TABLE IF NOT EXISTS facturas (
  numero          TEXT PRIMARY KEY,               -- FEV-000123 (ficticio)
  sq              TEXT NOT NULL UNIQUE REFERENCES casos(sq),
  id_aseguradora  TEXT NOT NULL REFERENCES aseguradoras(id_aseguradora),
  valor           NUMERIC(12,0) NOT NULL,
  fecha_expedicion DATE NOT NULL,
  fecha_radicacion DATE,
  soportes        JSONB NOT NULL,                 -- {"autorizacion": true, "descripcion_quirurgica": true, …}
  estado          TEXT NOT NULL DEFAULT 'EXPEDIDA' CHECK (estado IN ('EXPEDIDA','RADICADA','GLOSADA','PAGADA')),
  registrada_por  TEXT NOT NULL
);

-- Conceptos generales de glosa (Res. 2284 de 2023): FA facturación, TA tarifas, SO soportes, AU autorización,
-- CO cobertura, CL calidad, SA seguimiento a los acuerdos de voluntades.
CREATE TABLE IF NOT EXISTS glosas (
  id                 SERIAL PRIMARY KEY,
  numero_factura     TEXT NOT NULL REFERENCES facturas(numero),
  concepto           TEXT NOT NULL CHECK (concepto IN ('FA','TA','SO','AU','CO','CL','SA')),
  codigo_especifico  TEXT,                        -- p. ej. SO21 (opcional, según el manual)
  valor              NUMERIC(12,0) NOT NULL,
  descripcion        TEXT NOT NULL,
  fecha_notificacion DATE NOT NULL,
  estado             TEXT NOT NULL DEFAULT 'ABIERTA' CHECK (estado IN ('ABIERTA','RESPONDIDA','CERRADA')),
  respuesta          TEXT CHECK (respuesta IN ('ACEPTADA_TOTAL','ACEPTADA_PARCIAL','NO_ACEPTADA')),
  valor_aceptado     NUMERIC(12,0),
  justificacion      TEXT,
  fecha_respuesta    DATE,
  registrada_por     TEXT NOT NULL,
  respondida_por     TEXT
);

-- ============================================================ TRAZABILIDAD
CREATE TABLE IF NOT EXISTS auditoria (
  id       SERIAL PRIMARY KEY,
  momento  TIMESTAMPTZ NOT NULL DEFAULT now(),
  rol      TEXT NOT NULL,                         -- conector que hizo el cambio
  quien    TEXT,                                  -- cargo declarado
  accion   TEXT NOT NULL,
  sq       TEXT,
  detalle  JSONB
);

CREATE TABLE IF NOT EXISTS alertas_seguridad (
  id       SERIAL PRIMARY KEY,
  momento  TIMESTAMPTZ NOT NULL DEFAULT now(),
  rol      TEXT NOT NULL,
  campo    TEXT NOT NULL,
  sq       TEXT,
  motivo   TEXT NOT NULL,                         -- instrucción incrustada, dato personal bloqueado…
  extracto TEXT NOT NULL,
  revisada BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_aut_sq ON autorizaciones(sq, momento DESC);
CREATE INDEX IF NOT EXISTS ix_ins_sq ON insumos_caso(sq);
CREATE INDEX IF NOT EXISTS ix_pc_fecha ON programacion_casos(fecha);
CREATE INDEX IF NOT EXISTS ix_aud ON auditoria(momento DESC);

-- Autorización vigente (la última) por caso
CREATE OR REPLACE VIEW v_autorizacion_vigente AS
SELECT DISTINCT ON (sq) sq, estado, numero, vigente_hasta, registrada_por, momento
FROM autorizaciones ORDER BY sq, momento DESC, id DESC;

-- ============================================================ SALIDAS POR ROL (las genera cada artefacto o chat y quedan en el servidor)
CREATE TABLE IF NOT EXISTS salidas (
  id          SERIAL PRIMARY KEY,
  rol         TEXT NOT NULL,                      -- conector que la guardó
  titulo      TEXT NOT NULL,
  periodo     TEXT,                               -- '2026-11-30' o 'semana 2026-11-30'
  contenido   TEXT NOT NULL,                      -- texto o markdown: asunto, resumen y detalle (solo códigos SQ)
  datos       JSONB,                              -- opcional: tabla o cifras que respaldan la salida
  creada_por  TEXT NOT NULL,                      -- cargo
  momento     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_salidas ON salidas(rol, momento DESC);
