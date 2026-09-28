-- =========================================================
-- Legal AI — esquema Supabase (Postgres + pgvector)
-- Memoria compartida del área legal multiagente
-- =========================================================

create extension if not exists vector;

-- ---------------------------------------------------------
-- Dominios válidos (evita strings sueltos por todo el schema)
-- ---------------------------------------------------------
create type dominio_legal as enum
  ('tributario', 'corporativo', 'laboral', 'contratos');

create type estado_norma as enum
  ('pendiente_validacion', 'vigente', 'derogada_total', 'derogada_parcial');

create type tipo_relacion as enum
  ('deroga', 'modifica', 'complementa', 'nueva_edicion');

-- ---------------------------------------------------------
-- normas: catálogo maestro, una fila por documento ingerido
-- ---------------------------------------------------------
create table normas (
  id            uuid primary key default gen_random_uuid(),
  titulo        text not null,
  tipo_norma    text not null,               -- ley, decreto supremo, estandar_contractual...
  familia       text,                        -- NEC / FIDIC / IFOA / AIA (solo dominio=contratos)
  entidad_emisora text,
  fecha_publicacion date,
  dominios      dominio_legal[] not null,    -- puede pertenecer a varios
  estado        estado_norma not null default 'pendiente_validacion',
  hash_archivo  text unique not null,        -- evita duplicados
  validado_por  text,                        -- qué gerente lo confirmó
  fecha_validacion timestamptz,
  resumen       text,
  creado_en     timestamptz not null default now()
);

-- ---------------------------------------------------------
-- chunks_embeddings: fragmentos vectorizados por artículo/capítulo
-- ---------------------------------------------------------
create table chunks_embeddings (
  id            uuid primary key default gen_random_uuid(),
  norma_id      uuid not null references normas(id) on delete cascade,
  dominio       dominio_legal not null,
  referencia_jerarquica text not null,       -- "Título III, Cap. 2, Art. 15"
  contenido     text not null,
  embedding     vector(768) not null,        -- 768 = nomic-embed-text (Ollama). Ajustar si cambias de modelo.
  creado_en     timestamptz not null default now()
);

create index on chunks_embeddings using ivfflat (embedding vector_cosine_ops);
create index on chunks_embeddings (dominio);

-- ---------------------------------------------------------
-- relaciones_normas: el grafo de trazabilidad legal
-- ---------------------------------------------------------
create table relaciones_normas (
  id                uuid primary key default gen_random_uuid(),
  norma_origen_id   uuid not null references normas(id) on delete cascade,
  norma_afectada_id uuid not null references normas(id) on delete cascade,
  tipo_relacion     tipo_relacion not null,
  articulos_afectados text,                  -- ej. "Art. 4, Art. 7 inciso b"
  propuesto_por     text not null default 'bibliotecario',
  confirmado_por    text,                    -- gerente que validó (null = tentativo)
  confirmado        boolean not null default false,
  creado_en         timestamptz not null default now()
);

-- ---------------------------------------------------------
-- criterios_aprendidos: memoria procedural por dominio
-- ---------------------------------------------------------
create table criterios_aprendidos (
  id                uuid primary key default gen_random_uuid(),
  dominio           dominio_legal not null,
  tema              text not null,
  criterio_anterior text,
  criterio_corregido text not null,
  fuente_o_razon    text,
  corregido_por     text not null,           -- gerente responsable
  es_cruce          boolean not null default false,
  dominios_cruce    dominio_legal[],         -- si es_cruce = true
  creado_en         timestamptz not null default now()
);

create index on criterios_aprendidos (dominio, tema);

-- ---------------------------------------------------------
-- historial_consultas: memoria episódica
-- ---------------------------------------------------------
create table historial_consultas (
  id            uuid primary key default gen_random_uuid(),
  pregunta      text not null,
  dominio       dominio_legal,
  respondido_por text,                       -- gerente o 'investigador-web'
  tuvo_match_interno boolean not null,
  respuesta     text,
  creado_en     timestamptz not null default now()
);
