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
> `embedding vector(1536)`, que es la dimensión de
> `text-embedding-3-small` de OpenAI. Si usas otro modelo de embeddings
> (Voyage, Cohere, uno local), ajusta ese número **antes** de correr el
> script — cambiarlo después implica recrear la tabla `chunks_embeddings`.

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

### Importante — dónde corre Ollama vs. dónde corre el agente

- Si trabajas con **Claude Code localmente** (en tu computadora), Ollama
  corriendo en esa misma máquina es suficiente — el agente le pega a
  `localhost:11434` directo.
- Si trabajas desde **Claude Cowork / la nube**, Ollama en tu laptop
  **no es alcanzable** desde ahí. En ese caso necesitas Ollama corriendo
  en un servidor con IP/dominio accesible (un VPS, por ejemplo), y
  apuntar al agente a esa dirección en vez de `localhost`.
- Si esto se vuelve una limitación, la alternativa más simple sigue
  siendo OpenAI (pago mínimo, sin servidor que mantener) — ver el punto
  anterior de este README para el costo aproximado.

### Si cambias de modelo más adelante

Todo el corpus ya vectorizado queda con embeddings de una
dimensión/distribución distinta a los nuevos — no son comparables entre
sí. Si cambias de modelo, lo más limpio es **re-vectorizar todo el
corpus existente** en ese momento (volver a generar los embeddings de
`chunks_embeddings` para todas las normas) y, si la dimensión cambia,
ajustar `vector(768)` en `db/schema.sql` antes.

---

## 6. Ejecutarlo

En una tarea nueva de Claude Code o Claude Cowork, con el conector de
Supabase ya activo, pega:

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
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/asistente-ingesta/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/bibliotecario/SKILL.md
https://raw.githubusercontent.com/JuanGayoso/legal-ai/main/agents/asistente-laboral-contable/SKILL.md

Confirma que el conector de Supabase está activo. Si no lo está, dilo y
detente. Luego preséntate y pregúntame si tengo una norma nueva, una
consulta, o una corrección para el sistema.
```

O, si ya clonaste este repo localmente (con Claude Code corriendo dentro
de la carpeta), basta con abrir la sesión ahí: `CLAUDE.md` se carga solo.

---

## 7. Primeros pasos recomendados

1. Carga 1-2 normas de prueba de un mismo dominio y revisa la ficha que
   genera el Bibliotecario (usa `templates/ficha-norma.md` como referencia
   de qué debería salir).
2. Haz una consulta sobre esas normas y confirma que cita
   correctamente artículo y vigencia.
3. Corrige deliberadamente una respuesta para ver que el gerente
   correspondiente registre el criterio en `criterios_aprendidos`
   (revísalo directo en el SQL Editor de Supabase).
4. Recién después, empieza a cargar el corpus real.

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
