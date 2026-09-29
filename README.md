# ⚖️ Legal AI

Área legal virtual multiagente: un **Chief Legal Officer (CLO)** que orquesta,
5 **gerentes especializados** por materia (tributario, corporativo —incluye
propiedad horizontal/amparo—, laboral —HR, SST, sindical, litigios/SUNAFIL,
migratorio, DEI—, contratos NEC/FIDIC/IFOA/AIA/JCT, y edificaciones
—habilitaciones urbanas y real estate—), y 3 **asistentes transversales**
(ingesta, bibliotecario, laboral-contable) que alimentan y ordenan una base
de conocimiento legal que crece todos los días.

Rama legal de [boardroom-ai](https://github.com/JuanGayoso/boardroom-ai),
con una diferencia clave: la memoria no es un `.md` de una sola sesión — es
**persistente e indefinida**, vive en **Supabase (Postgres + pgvector)**
porque el corpus normativo crece sin parar y necesita búsqueda semántica real.

---

## Las dos partes de este proyecto

Son procesos separados, con herramientas distintas — no los mezcles:

| | **Parte A — Ingesta** | **Parte B — Uso diario** |
|---|---|---|
| Qué hace | Lee PDFs, OCR si hace falta, ordena por artículo, vectoriza, guarda en Supabase | Consultas, revisar lo que dejó la ingesta, corregir criterios |
| Dónde corre | Tu terminal, `scripts/ingest.py` (Python) | **Cowork** (claude.ai) — sin terminal, sin instalar nada |
| Quién decide de fondo | Nadie — todo queda "pendiente de revisión" | El CLO y los gerentes, en la conversación de Cowork |
| Con qué frecuencia | Cada vez que sale una norma nueva (una por una o en lote) | Cada vez que consultas o revisas pendientes |

La razón de separarlas: la ingesta es trabajo mecánico y puede ser
masivo (decenas de PDFs) — hacerlo en Cowork gastaría tu cupo de plan
en tareas que no necesitan criterio legal. El uso diario sí necesita
razonamiento, y ahí es donde entra Cowork.

---

# Parte A — Ingesta de normas

## A1. Requisitos

- Una cuenta de [Supabase](https://supabase.com) (plan gratuito alcanza
  para empezar).
- [Ollama](https://ollama.com) instalado (gratis, corre en tu compu).
- Python 3.10+ instalado en tu computadora.

## A2. Crear el proyecto en Supabase

1. Entra a [supabase.com](https://supabase.com) → **New Project**.
2. Elige nombre (ej. `legal-ai`), contraseña de base de datos, y región.
3. Espera ~2 minutos a que se aprovisione.
4. Ve a **Project Settings → Database → Connection string → URI** y
   copia esa línea completa — la vas a necesitar en el paso A5
   (reemplazando `[YOUR-PASSWORD]` por tu contraseña real).

## A3. Crear las tablas

En el dashboard de tu proyecto, ve a **SQL Editor → New query**, pega
todo el contenido de [`db/schema.sql`](./db/schema.sql) y dale **Run**.
Esto crea las 5 tablas (`normas`, `chunks_embeddings`,
`relaciones_normas`, `criterios_aprendidos`, `historial_consultas`) y
el índice de búsqueda vectorial.

> ⚠️ **Dimensión del vector**: `schema.sql` define `embedding
> vector(768)`, la dimensión de `nomic-embed-text` (el modelo que usa
> Ollama, ver A4). Si cambias de modelo de embeddings más adelante,
> ajusta ese número antes de correr el script otra vez.

Verifica que se creó bien:
```sql
select table_name from information_schema.tables where table_schema = 'public';
```

## A4. Instalar Ollama (embeddings, gratis)

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: instalador en ollama.com/download
```

Descarga el modelo y déjalo corriendo:
```bash
ollama pull nomic-embed-text
ollama serve
```
Ollama debe estar corriendo (`ollama serve`) cada vez que uses el
ingestor. Prueba que responde:
```bash
curl http://localhost:11434/api/embeddings -d '{"model": "nomic-embed-text", "prompt": "prueba"}'
```

## A5. Instalar el ingestor

```bash
git clone https://github.com/JuanGayoso/legal-ai.git
cd legal-ai/scripts
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Instala Tesseract (OCR), con el paquete de español:
```bash
# macOS
brew install tesseract tesseract-lang

# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-spa

# Windows: instalador en https://github.com/UB-Mannheim/tesseract/wiki
```

Configura la conexión a Supabase:
```bash
cp .env.example .env
```
Edita `.env` y pega tu `SUPABASE_DB_URL` (paso A2).

## A6. Usar el ingestor — un archivo o una carpeta entera

**Un solo PDF:**
```bash
python ingest.py ruta/a/norma.pdf
```

**Una carpeta completa** (busca todos los `.pdf`, incluyendo subcarpetas):
```bash
python ingest.py ruta/a/carpeta_de_normas/
```

**Probar primero sin escribir nada en Supabase:**
```bash
python ingest.py ruta/a/norma.pdf --dry-run
```

**Forzar el dominio** (recomendado si la clasificación automática se
equivoca, o si quieres asegurarte):
```bash
python ingest.py ruta/a/norma.pdf --dominio tributario
python ingest.py ruta/a/norma.pdf --dominio laboral --dominio tributario   # materia cruzada
python ingest.py ruta/a/contrato.pdf --dominio contratos --familia FIDIC
python ingest.py ruta/a/licencia.pdf --dominio edificaciones
```

### Qué vas a ver en pantalla — las fases del proceso

El script siempre te dice en qué fase está y qué está procesando, para
que nunca pienses que se congeló:

```
📂 Carpeta detectada: 12 PDF(s) encontrados.
Progreso total:  25%|████▎          | 3/12 [00:42<02:15, archivo/s]

📄 ruta/a/carpeta_de_normas/decreto_1234.pdf
  [1/6] Leyendo PDF / OCR
    páginas: 100%|██████████| 18/18 [00:08<00:00, 2.1pág/s]
    OCR aplicado en 3/18 páginas.
  [2/6] Clasificando metadata
    Título (tentativo): DECRETO SUPREMO QUE MODIFICA EL REGLAMENTO...
    Tipo de norma: decreto supremo
    Fecha detectada: 2026-03-14
    Dominio(s): ['tributario']
  [3/6] Generando chunks
    Chunks generados: 24
  [4/6] Generando embeddings — 24 chunk(s)
    embeddings: 100%|██████████| 24/24 [00:11<00:00, 2.2chunk/s]
  [5/6] Guardando en Supabase
    Norma insertada (id=..., estado=pendiente_validacion)
  [6/6] Detectando relaciones — 1 candidato(s) detectado(s)
    🔗 modifica → 'Reglamento de la Ley del IGV...' (pendiente de confirmar)
  ✅ Completado: 24 chunks, 1 lista(s) para confirmar, 0 en espera, 0 resuelta(s) retroactivamente.
```

Cada fase (1 a 6) siempre aparece en el mismo orden, con barras de
progreso en los pasos que tardan más (páginas de OCR, embeddings, y el
progreso total si procesas una carpeta). Si algo se traba de verdad
(por ejemplo, Ollama dejó de responder), vas a ver el mismo paso
repetirse sin avanzar — ahí sí sabes exactamente dónde mirar.

### Sobre las normas que se referencian entre sí

Si una norma menciona "deroga el artículo X de la Ley N° 12345" pero
esa Ley 12345 todavía no la has ingerido, la relación **no se pierde**:
queda guardada como "en espera". Cada vez que ingieres una norma nueva,
el script revisa automáticamente si resuelve alguna relación que
quedó en espera de ingestas anteriores — no importa el orden en que
cargues los PDFs. Puedes ver el estado de todo esto con
`python pendientes.py` en cualquier momento, o dejar que el CLO lo
revise solo la próxima vez que abras Cowork (Parte B).

### Ver qué quedó pendiente de revisión

```bash
python pendientes.py
```
Lista las normas en `pendiente_validacion` y las relaciones sin
confirmar — con eso vas a Cowork (Parte B) a que el CLO las revise.

---

# Parte B — Uso diario (Cowork, sin instalar nada)

Esta parte no necesita terminal, ni Python, ni Claude Code — corre
directamente en tu navegador, en [claude.ai](https://claude.ai), en una
conversación normal (Cowork). Es donde haces consultas, revisas lo que
dejó la ingesta, y corriges al sistema cuando se equivoca.

## B1. Conectar Supabase en Cowork

1. En claude.ai, ve a **Settings → Connectors** (o el ícono de
   conectores dentro de una conversación).
2. Busca **Supabase** y actívalo.
3. Autentícalo con la cuenta donde está tu proyecto `legal-ai` (el
   mismo que configuraste en la Parte A2).
4. Confirma que quedó activo. El propio `CLAUDE.md` hace que el CLO
   verifique esto al arrancar, y se detiene si no lo encuentra.

Esto se configura **una sola vez** — queda conectado para tus próximas
conversaciones.

## B2. Arrancar al CLO

Abre una conversación nueva en claude.ai y pega:

```
Actúa como el Chief Legal Officer del framework Legal AI.
Lee las instrucciones en:
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/CLAUDE.md

Luego lee cada SKILL.md de los agentes desde:
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/clo/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/gerente-tributario/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/gerente-corporativo/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/gerente-laboral/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/gerente-contratos/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/gerente-edificaciones/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/asistente-ingesta/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/bibliotecario/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/asistente-laboral-contable/SKILL.md

Confirma que el conector de Supabase está activo. Si no lo está, dilo y
detente. Revisa si hay normas pendientes de validación o relaciones sin
confirmar. Luego preséntate y pregúntame qué necesito.
```

## B3. Los tres usos

1. **Revisar pendientes de una ingesta reciente** — le dices al CLO
   "revisa lo que acabo de ingestar" y te muestra las normas
   `pendiente_validacion` y las relaciones tentativas para que el
   gerente del dominio las confirme o corrija.
2. **Hacer una consulta** — le preguntas algo de una materia y responde
   citando norma/artículo/vigencia, buscando primero en tu corpus. Si
   no encuentra nada, te lo dice antes de ofrecer buscar en internet.
3. **Corregir al sistema** — si una respuesta está mal, se lo dices y
   el gerente de esa materia guarda el criterio corregido como regla
   permanente de su dominio.

---

## Parte C — Desplegar el corpus en otra computadora / para otra persona

Si quieres que otra persona (otro estudio, otra oficina, un colega) use este
mismo framework pero **con su propia copia del corpus**, hay dos formas de
hacerlo. En casi todos los casos conviene la copia independiente: cada quien
termina con su propia base de datos, que desde ese momento crece por
separado — lo que uno ingiera o corrija no le llega al otro.

La alternativa (agregarla como colaboradora a tu mismo proyecto de Supabase,
para que ambos lean y escriban la misma base en tiempo real) solo tiene
sentido si son literalmente el mismo equipo trabajando sobre el mismo
corpus — y esa persona vería también tus `criterios_aprendidos` internos.

### C1. Copia independiente (recomendado)

No necesitas compartir tu `SUPABASE_DB_URL` con nadie — solo un archivo
`.sql` con los datos. Usa `pg_dump`/`psql` (vienen con Postgres.app en Mac,
o instálalos con `brew install libpq` y agrégalo a tu PATH).

**1. La otra persona crea su propio proyecto en Supabase** (gratis, su
propia cuenta) y sigue la Parte A2 de este README para obtener su propio
`SUPABASE_DB_URL`.

**2. Ella aplica el mismo schema** (crea las tablas vacías, el enum de
dominios completo, la extensión `vector`, todo):
```bash
psql "postgresql://postgres:SU_PASSWORD@SU_HOST.supabase.co:5432/postgres" -f db/schema.sql
```

**3. Tú exportas solo los datos** (no el schema, no credenciales) desde tu
máquina, usando tu propio `SUPABASE_DB_URL` (el de tu `scripts/.env`):
```bash
pg_dump "$SUPABASE_DB_URL" \
  --data-only --no-owner --no-privileges \
  --table=public.normas \
  --table=public.chunks_embeddings \
  --table=public.relaciones_normas \
  --table=public.criterios_aprendidos \
  --table=public.historial_consultas \
  -f corpus_legal_ai.sql
```
Si **no** quieres compartir tus `criterios_aprendidos` (el criterio
jurídico aprendido de tus correcciones, que puede ser específico de tu
estudio), quita esa línea `--table=public.criterios_aprendidos` del
comando — el resto del corpus normativo sí viaja igual.

**4. Le envías `corpus_legal_ai.sql`** por el medio que prefieras — es solo
datos, sin ninguna contraseña ni connection string adentro.

**5. Ella lo restaura en su propio proyecto**:
```bash
psql "postgresql://postgres:SU_PASSWORD@SU_HOST.supabase.co:5432/postgres" \
  -c "SET session_replication_role = replica;" \
  -f corpus_legal_ai.sql \
  -c "SET session_replication_role = DEFAULT;"
```
El `session_replication_role = replica` desactiva temporalmente la
verificación de llaves foráneas durante la carga, para que el orden en que
`pg_dump` escribió las tablas no cause errores.

Desde ahí, su copia es 100% independiente: lo que ella ingiera o corrija
con su propio CLO en Cowork no te afecta a ti, ni viceversa. Ella solo
necesita, además, su propio Ollama corriendo localmente (Parte A4) para
seguir ingiriendo normas nuevas desde su computadora.

### C2. Base de datos compartida (mismo equipo)

Si en cambio quieres que trabaje sobre tu misma base en tiempo real:
agrégala como colaboradora de tu proyecto de Supabase (Project Settings →
Team → Invite member) para que use el mismo conector MCP en su propia
sesión de Cowork. No hay nada que exportar ni restaurar — pero ten en
cuenta que verá y podrá modificar todo lo que hay ahí, incluidos los
`criterios_aprendidos` de cada gerente.

---

## Estructura del repo

```
legal-ai/
├── CLAUDE.md                          ← instrucciones para Cowork (CLO + flujos de uso)
├── agents/
│   ├── clo/SKILL.md
│   ├── gerente-tributario/SKILL.md
│   ├── gerente-corporativo/SKILL.md
│   ├── gerente-laboral/SKILL.md
│   ├── gerente-contratos/SKILL.md     ← NEC / FIDIC / IFOA / AIA / JCT
│   ├── gerente-edificaciones/SKILL.md ← habilitaciones urbanas, edificación y real estate
│   ├── asistente-ingesta/SKILL.md     ← referencia de lo que hace scripts/ingest.py
│   └── bibliotecario/SKILL.md         ← referencia de lo que hace scripts/ingest.py
│   └── asistente-laboral-contable/SKILL.md
├── scripts/
│   ├── ingest.py                      ← Parte A: ingestor (archivo o carpeta)
│   ├── pendientes.py                  ← lista qué falta revisar en Cowork
│   ├── requirements.txt
│   └── .env.example
├── templates/
│   ├── ficha-norma.md
│   └── criterio-aprendido.md
└── db/
    └── schema.sql                     ← esquema completo de Supabase
```

---

## Notas de seguridad

- La cadena de conexión de Supabase (`SUPABASE_DB_URL`) da acceso total
  a tu base de datos — vive solo en tu `scripts/.env` local (ya está en
  `.gitignore`), nunca la pegues en un chat ni la subas a git.
- Si trabajas con normas confidenciales o información societaria
  sensible, revisa los permisos (RLS) de cada tabla en Supabase antes
  de compartir acceso con otros usuarios.

---

## Créditos

Adaptado de [boardroom-ai](https://github.com/JuanGayoso/boardroom-ai) a
la estructura de un área legal corporativa, con memoria semántica
persistente en Supabase en lugar de un documento markdown de sesión única.

MIT License
