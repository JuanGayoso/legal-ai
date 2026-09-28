# ⚖️ Legal AI

Área legal virtual multiagente: un **Chief Legal Officer (CLO)** que orquesta,
4 **gerentes especializados** por materia (tributario, corporativo, laboral,
contratos NEC/FIDIC/IFOA/AIA), y 3 **asistentes transversales** (ingesta,
bibliotecario, laboral-contable) que alimentan y ordenan una base de
conocimiento legal que crece todos los días.

Rama legal de [boardroom-ai](https://github.com/JuanGayoso/boardroom-ai),
con una diferencia clave: la memoria no es un `.md` de una sola sesión — es
**persistente e indefinida**, vive en **Supabase (Postgres + pgvector)**
porque el corpus normativo crece sin parar y necesita búsqueda semántica real.

---

## Qué hace

1. **Le pasas un PDF de una norma nueva** → el sistema hace OCR si hace
   falta, la ordena por capítulo/artículo, genera embeddings, la guarda en
   Supabase, y detecta si deroga, modifica o complementa alguna norma que
   ya tenías.
2. **Le haces una consulta** → responde citando norma/artículo/vigencia,
   buscando primero en tu propio corpus. Si no encuentra nada, te lo dice
   explícitamente antes de ofrecer buscar en internet — nunca alucina.
3. **Lo corriges** → el gerente de esa materia guarda el criterio corregido
   como regla permanente de su dominio, para no repetir el error.

---

## Estructura del repo

```
legal-ai/
├── CLAUDE.md                          ← instrucciones del orquestador (CLO) y los 3 flujos
├── agents/
│   ├── clo/SKILL.md                   ← Chief Legal Officer (orquestador)
│   ├── gerente-tributario/SKILL.md
│   ├── gerente-corporativo/SKILL.md
│   ├── gerente-laboral/SKILL.md
│   ├── gerente-contratos/SKILL.md     ← NEC / FIDIC / IFOA / AIA
│   ├── asistente-ingesta/SKILL.md
│   ├── bibliotecario/SKILL.md         ← OCR, chunking, embeddings, trazabilidad
│   └── asistente-laboral-contable/SKILL.md
├── templates/
│   ├── ficha-norma.md
│   └── criterio-aprendido.md
└── db/
    └── schema.sql                     ← esquema completo de Supabase
```

---

## 1. Requisitos

- Una cuenta de [Supabase](https://supabase.com) (plan gratuito alcanza para
  empezar).
- Acceso a Claude Code o Claude Cowork (donde vas a correr el framework).
- Una API key de un proveedor de embeddings (OpenAI, Voyage, o el que
  prefieras) — el Bibliotecario la necesita para vectorizar cada norma.

---

## 2. Crear el proyecto en Supabase

1. Entra a [supabase.com](https://supabase.com) → **New Project**.
2. Elige nombre (ej. `legal-ai`), contraseña de base de datos, y región
   (idealmente la más cercana a donde trabajas).
3. Espera ~2 minutos a que se aprovisione.
4. Ve a **Project Settings → API** y copia:
   - `Project URL`
   - `anon public key` (para lecturas desde el front, si lo necesitas)
   - `service_role key` (para que el framework escriba en la base — **no la
     compartas ni la subas a git**)
5. Ve a **Project Settings → Database → Connection string** y copia la
   cadena de conexión Postgres (la usarás si te conectas directo con SQL
   en vez de vía MCP).

---

## 3. Crear las tablas (ejecutar `db/schema.sql`)

Tienes dos formas de correrlo:

### Opción A — Editor SQL de Supabase (más simple)
1. En el dashboard de tu proyecto, ve a **SQL Editor → New query**.
2. Pega todo el contenido de [`db/schema.sql`](./db/schema.sql).
3. Dale **Run**. Esto crea:
   - la extensión `vector` (pgvector)
   - los tipos `dominio_legal`, `estado_norma`, `tipo_relacion`
   - las tablas `normas`, `chunks_embeddings`, `relaciones_normas`,
     `criterios_aprendidos`, `historial_consultas`
   - los índices (incluyendo el índice IVFFlat para búsqueda vectorial)

### Opción B — psql / línea de comandos
```bash
psql "postgresql://postgres:<tu-password>@<tu-host>.supabase.co:5432/postgres" \
  -f db/schema.sql
```

> ⚠️ **Dimensión del vector**: `schema.sql` define
> `embedding vector(768)`, que es la dimensión de `nomic-embed-text`
> (el modelo que usa Ollama en este proyecto — ver sección 5). Si más
> adelante cambias a otro modelo de embeddings (OpenAI, Voyage, etc.),
> ajusta ese número **antes** de correr el script — cambiarlo después
> implica recrear la tabla `chunks_embeddings`.

### Verificar que se creó bien
En el SQL Editor, corre:
```sql
select table_name from information_schema.tables
where table_schema = 'public';
```
Deberías ver: `normas`, `chunks_embeddings`, `relaciones_normas`,
`criterios_aprendidos`, `historial_consultas`.

---

## 4. Conectar Supabase al agente

El framework necesita que el LLM pueda leer y escribir en esas tablas en
cada turno — no basta con tenerlas creadas. La forma recomendada es el
**conector MCP de Supabase**:

1. En Claude (claude.ai o Claude Code), busca el conector **Supabase** e
   instálalo/actívalo para tu cuenta o proyecto.
2. Autentícalo con el proyecto que creaste en el paso 2 (te pedirá login
   o el `service_role key`, según el flujo de conexión de Supabase).
3. Confirma que el conector queda activo — el propio `CLAUDE.md` de este
   repo hace que el CLO verifique esto al arrancar, y se detiene si no lo
   encuentra (para no trabajar con memoria falsa o efímera).

Si prefieres no usar el conector MCP, la alternativa es correr un backend
propio (Python/Node) que exponga la lectura/escritura a Supabase como
herramientas — más trabajo, pero te da control total. El diseño de los
`SKILL.md` no depende de cuál elijas, solo de que exista *alguna* forma de
leer/escribir en esas tablas.

---

## 5. Configurar el proveedor de embeddings (Ollama, local y gratis)

Este proyecto usa **Ollama + `nomic-embed-text`** para generar los
embeddings — corre en tu propia máquina o servidor, sin costo por uso y
sin API key. `db/schema.sql` ya está configurado para esto:
`embedding vector(768)`, que es la dimensión de `nomic-embed-text`.

### Instalar Ollama

**macOS:**
```bash
brew install ollama
```
o descarga el instalador desde [ollama.com/download](https://ollama.com/download).

**Windows:** descarga el instalador desde [ollama.com/download](https://ollama.com/download).

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Descargar el modelo de embeddings

```bash
ollama pull nomic-embed-text
```

### Levantar el servicio

```bash
ollama serve
```
Esto deja Ollama escuchando en `http://localhost:11434`. Tiene que estar
**corriendo** cada vez que el Bibliotecario vaya a vectorizar una norma
nueva o a resolver una consulta (que también necesita vectorizar la
pregunta del usuario para buscar por similitud).

### Probar que funciona

```bash
curl http://localhost:11434/api/embeddings -d '{
  "model": "nomic-embed-text",
  "prompt": "artículo de prueba"
}'
```
Debería devolver un JSON con un arreglo `embedding` de 768 números.

### Importante — este proyecto corre con Claude Code local, no Cowork/nube

Ollama corriendo en tu propia computadora **solo es alcanzable si Claude
también corre en esa misma computadora**. Por eso este framework está
pensado para usarse con **Claude Code local** (terminal, en tu máquina),
no con una sesión de Claude Cowork en la nube — una sesión en la nube no
puede llegar a `localhost:11434` de tu laptop.

Si en algún momento prefieres trabajar desde la nube, la alternativa es
cambiar el proveedor de embeddings a uno con API (OpenAI, Voyage) — ahí
sí no importa dónde corre el agente, porque llama a un servicio externo
en vez de a tu propia máquina. Eso implica cambiar `vector(768)` por la
dimensión de ese modelo en `db/schema.sql` antes de crear las tablas.

### Si cambias de modelo más adelante

Todo el corpus ya vectorizado queda con embeddings de una
dimensión/distribución distinta a los nuevos — no son comparables entre
sí. Si cambias de modelo, lo más limpio es **re-vectorizar todo el
corpus existente** en ese momento (volver a generar los embeddings de
`chunks_embeddings` para todas las normas) y, si la dimensión cambia,
ajustar `vector(768)` en `db/schema.sql` antes.

---

## 6. Ejecutarlo (Claude Code local)

Este framework está pensado para correr con **Claude Code en tu propia
computadora** — así Ollama (sección 5) es alcanzable en `localhost`.

### Instalar Claude Code (si no lo tienes)
```bash
npm install -g @anthropic-ai/claude-code
```
Más detalles: [docs de Claude Code](https://docs.claude.com/en/docs/claude-code/overview).

### Clonar este repo
```bash
git clone https://github.com/JuanGayoso/legal-ai.git
cd legal-ai
```

### Verificar que Ollama está corriendo
```bash
ollama serve   # si no lo tenías ya levantado
```

### Abrir Claude Code dentro de la carpeta del repo
```bash
claude
```
Al estar parado dentro de `legal-ai/`, Claude Code carga automáticamente
el `CLAUDE.md` del repo — no necesitas pegarle las instrucciones a mano.
Aun así, el primer mensaje que le mandes puede ser simplemente:

```
Preséntate como el Chief Legal Officer del framework Legal AI. Confirma
que el conector de Supabase está activo y que Ollama responde en
localhost:11434. Si algo falta, dilo y detente. Luego pregúntame si
tengo una norma nueva, una consulta, o una corrección para el sistema.
```

### Conector de Supabase en Claude Code
El conector MCP de Supabase se configura igual que en Claude Cowork —
ve a la configuración de conectores/MCP de Claude Code y agrégalo con
las credenciales de tu proyecto (sección 2 de este README).

---

## 7. Ingesta masiva sin Claude Code (`scripts/ingest.py`)

Para cargar muchas normas de golpe sin gastar el cupo de tu plan de
Claude, `scripts/ingest.py` hace la parte **mecánica** de la ingesta
(la que no necesita criterio legal) directamente en Python:

- Detecta página por página si el PDF necesita OCR (Tesseract) o ya
  tiene texto extraíble.
- Clasifica tipo de norma, fecha y dominio por heurística (con flags
  para forzarlo a mano si se equivoca).
- Hace chunking jerárquico (Título → Capítulo → Artículo).
- Genera los embeddings con Ollama (`nomic-embed-text`, igual que el
  resto del proyecto) y los guarda en Supabase.
- Detecta por expresión regular frases tipo "Derógase el artículo X de
  la Ley N° ..." y dedja esas relaciones como **tentativas**
  (`confirmado = false`) para que las revise el gerente del dominio.

Lo que el script **no** hace — sigue siendo trabajo del CLO y los
gerentes en Claude Code: confirmar si una relación de derogación es
correcta, decidir si una norma queda `vigente`, o cualquier
interpretación de fondo. Todo lo que ingesta el script queda con
`estado = 'pendiente_validacion'`, esperando esa revisión.

### Instalar

```bash
cd scripts
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Instala también Tesseract con el paquete de español (necesario solo
para páginas escaneadas):
```bash
# macOS
brew install tesseract tesseract-lang

# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-spa

# Windows: instalador en https://github.com/UB-Mannheim/tesseract/wiki
```

### Configurar

```bash
cp .env.example .env
```
Edita `.env` y completa `SUPABASE_DB_URL` con la cadena de conexión
directa a Postgres de tu proyecto (sección 3 de este README explica
dónde sacarla). Deja `OLLAMA_URL` y `OLLAMA_EMBED_MODEL` con sus
valores por defecto si no los cambiaste.

### Usar

```bash
# Probar sin escribir nada en Supabase (revisa clasificación y chunking)
python ingest.py ruta/a/norma.pdf --dry-run

# Ingesta real, dejando que el script clasifique el dominio solo
python ingest.py ruta/a/norma.pdf

# Forzando el dominio (recomendado si la heurística se equivoca)
python ingest.py ruta/a/norma.pdf --dominio tributario

# Norma que cruza dos materias
python ingest.py ruta/a/norma.pdf --dominio laboral --dominio tributario

# Estándar contractual (NEC/FIDIC/IFOA/AIA)
python ingest.py ruta/a/contrato.pdf --dominio contratos --familia FIDIC

# Ingesta masiva de una carpeta entera
for f in ruta/a/normas/*.pdf; do python ingest.py "$f"; done
```

### Ver qué quedó pendiente de revisión

```bash
python pendientes.py
```
Lista las normas en `pendiente_validacion` y las relaciones sin
confirmar. Con eso en mano, abres tu sesión de Claude Code y le pides
al CLO que las revise con el gerente correspondiente.

---

## 8. Primeros pasos recomendados

1. Corre `scripts/ingest.py` con `--dry-run` sobre 1-2 normas de prueba
   primero — revisa que la clasificación de dominio y el chunking por
   artículo se vean razonables antes de escribir nada en Supabase.
2. Sin `--dry-run`, ingesta esas mismas 1-2 normas de verdad y corre
   `scripts/pendientes.py` para confirmar que quedaron en
   `pendiente_validacion`.
3. Abre Claude Code en la carpeta del repo y pide al CLO que revise
   esos pendientes con el gerente del dominio correspondiente (usa
   `templates/ficha-norma.md` como referencia de qué debería confirmar).
4. Haz una consulta sobre esas normas y confirma que cita
   correctamente artículo y vigencia.
5. Corrige deliberadamente una respuesta para ver que el gerente
   correspondiente registre el criterio en `criterios_aprendidos`
   (revísalo directo en el SQL Editor de Supabase).
6. Recién después, empieza a cargar el corpus real con
   `scripts/ingest.py` en lote.

---

## Los 3 flujos (resumen)

| Flujo | Dispara con | Agentes involucrados |
|---|---|---|
| **Ingesta** | Subir un PDF de norma nueva | Asistente de Ingesta → Bibliotecario → Gerente(s) del dominio → CLO |
| **Consulta** | Una pregunta | CLO → Gerente del dominio (o Asistente Laboral-Contable si cruza materias) |
| **Aprendizaje** | Corregir una respuesta | Gerente del dominio → `criterios_aprendidos` |

Detalle completo de cada flujo en [`CLAUDE.md`](./CLAUDE.md).

---

## Notas de seguridad

- El `service_role key` de Supabase tiene acceso total a la base — no lo
  pegues en el chat ni lo subas al repo. Los conectores MCP lo manejan por
  fuera del historial de conversación.
- Si trabajas con normas confidenciales o información societaria sensible,
  usa un proyecto de Supabase privado y revisa los permisos (RLS) de cada
  tabla antes de compartir acceso con otros usuarios.

---

## Créditos

Adaptado de [boardroom-ai](https://github.com/JuanGayoso/boardroom-ai) a
la estructura de un área legal corporativa, con memoria semántica
persistente en Supabase en lugar de un documento markdown de sesión única.

MIT License
