#!/usr/bin/env python3
"""
ingest.py — Ingestor mecánico de normas para Legal AI
=======================================================

Reemplaza la parte MECÁNICA de los agentes "Asistente de Ingesta" y
"Bibliotecario" (ver agents/asistente-ingesta/SKILL.md y
agents/bibliotecario/SKILL.md) con código Python puro, sin pasar por
Claude Code. Esto permite correr ingestas masivas sin consumir tu cupo
de plan de Claude.

Lo que SÍ hace este script (mecánico, determinístico):
  1. Calcula el hash del PDF y verifica duplicados contra `normas`.
  2. Decide, página por página, si necesita OCR (PyMuPDF) o el texto
     ya es extraíble directamente.
  3. Extrae metadata básica por heurística (tipo de norma, fecha,
     posible entidad emisora) — con flags para forzarla a mano.
  4. Clasifica el/los dominio(s) por conteo de palabras clave — con
     flag para forzarlo a mano.
  5. Hace chunking jerárquico por Título → Capítulo → Artículo.
  6. Genera embeddings con Ollama (nomic-embed-text, 768 dim).
  7. Guarda todo en Supabase: `normas` (estado=pendiente_validacion) y
     `chunks_embeddings`.
  8. Detecta por REGEX candidatos de derogación/modificación
     ("Derógase el artículo X de la Ley N° ...") y los deja en
     `relaciones_normas` como TENTATIVOS (confirmado=false).

Lo que este script NO hace (a propósito) — sigue siendo trabajo de los
agentes / de un humano:
  - Confirmar que una relación de derogación/modificación es correcta.
    La detección por regex es un candidato, no una verdad legal.
  - Decidir si la norma queda "vigente" — el estado se guarda como
    `pendiente_validacion` para que el CLO / gerente del dominio lo
    revise en tu siguiente sesión de Claude Code.
  - Cualquier interpretación de fondo del contenido.

Uso:
    python ingest.py archivo.pdf
    python ingest.py archivo.pdf --dominio tributario
    python ingest.py archivo.pdf --dominio laboral --dominio tributario
    python ingest.py archivo.pdf --tipo-norma "decreto supremo" --entidad SUNAT
    python ingest.py archivo.pdf --dry-run   # no escribe nada, solo muestra

Requisitos del sistema (además de requirements.txt):
    - Ollama corriendo (`ollama serve`) con `ollama pull nomic-embed-text`
    - Tesseract OCR instalado, con el paquete de idioma español:
        macOS:   brew install tesseract tesseract-lang
        Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-spa
        Windows: instalador de https://github.com/UB-Mannheim/tesseract/wiki
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import date

import fitz  # PyMuPDF
import psycopg2
import pytesseract
import requests
from dotenv import load_dotenv
from PIL import Image
from pgvector.psycopg2 import register_vector

# ---------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Nomic recomienda prefijar los textos indexados con "search_document: "
# (y las consultas, cuando escribas el script de consulta, con
# "search_query: ") para mejorar la calidad del retrieval.
NOMIC_DOC_PREFIX = "search_document: "

MIN_CHARS_NATIVE_PAGE = 40  # bajo este umbral, la página se considera escaneada

DOMINIOS_VALIDOS = {"tributario", "corporativo", "laboral", "contratos"}

# Heurística de clasificación de dominio por palabras clave.
# Es deliberadamente simple — el objetivo es una PROPUESTA que el
# gerente confirma, no una clasificación perfecta.
KEYWORDS_DOMINIO = {
    "tributario": [
        "sunat", "tributari", "impuesto", "igv", "renta", "itan",
        "fiscalizaci", "código tributario", "codigo tributario",
    ],
    "laboral": [
        "trabajador", "planilla", "cts", "gratificaci", "essalud",
        "onp", "afp", "sindical", "jornada laboral", "remuneraci",
    ],
    "corporativo": [
        "sociedad", "directorio", "junta general", "accionista",
        "ley general de sociedades", "gobierno corporativo", "fusión",
        "fusion", "escisión", "escision",
    ],
    "contratos": [
        "fidic", "nec", "ifoa", "aia", "contrato colaborativo",
        "resolución de disputas", "resolucion de disputas",
    ],
}

# Heurística de tipo de norma (Perú)
TIPO_NORMA_PATTERNS = [
    (r"decreto\s+legislativo", "decreto legislativo"),
    (r"decreto\s+supremo", "decreto supremo"),
    (r"resoluci[oó]n\s+ministerial", "resolución ministerial"),
    (r"resoluci[oó]n\s+de\s+superintendencia", "resolución de superintendencia"),
    (r"resoluci[oó]n\s+legislativa", "resolución legislativa"),
    (r"directiva", "directiva"),
    (r"\bley\s+n[°º]?\s*\d+", "ley"),
]

# Detección de candidatos de derogación/modificación por regex.
# Ejemplo que detecta: "Derógase el artículo 5 de la Ley N° 12345"
RELACION_PATTERNS = [
    ("deroga", re.compile(
        r"der[oó]ga[sn]?e?\s+(?:el|los|la|las)?\s*(?:art[ií]culo[s]?\s*[\d°ºy,\s]+\s*(?:de\s+)?)?"
        r"(?:la|el)?\s*(ley|decreto supremo|decreto legislativo|resoluci[oó]n[^.,;]*)"
        r"\s*n[°º]?\s*([\w\-]+)", re.IGNORECASE)),
    ("modifica", re.compile(
        r"modif[ií]ca[sn]?e?\s+(?:el|los|la|las)?\s*(?:art[ií]culo[s]?\s*[\d°ºy,\s]+\s*(?:de\s+)?)?"
        r"(?:la|el)?\s*(ley|decreto supremo|decreto legislativo|resoluci[oó]n[^.,;]*)"
        r"\s*n[°º]?\s*([\w\-]+)", re.IGNORECASE)),
]

FECHA_PATTERN = re.compile(
    r"lima,?\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", re.IGNORECASE
)
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


# ---------------------------------------------------------------------
# 1. Extracción de texto + decisión de OCR
# ---------------------------------------------------------------------

def extract_text_per_page(pdf_path: str) -> list[str]:
    """Devuelve el texto de cada página, usando OCR solo donde haga falta."""
    doc = fitz.open(pdf_path)
    pages_text = []
    ocr_pages = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        native_text = page.get_text().strip()

        if len(native_text) >= MIN_CHARS_NATIVE_PAGE:
            pages_text.append(native_text)
            continue

        # Página probablemente escaneada -> OCR
        ocr_pages += 1
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr_text = pytesseract.image_to_string(img, lang="spa")
        pages_text.append(ocr_text.strip())

    doc.close()

    if ocr_pages:
        print(f"  OCR aplicado en {ocr_pages}/{len(pages_text)} páginas.")
    else:
        print("  Texto nativo en todas las páginas (sin OCR).")

    return pages_text


# ---------------------------------------------------------------------
# 2. Metadata: tipo de norma, fecha, dominio
# ---------------------------------------------------------------------

def detect_tipo_norma(text_head: str) -> str:
    lowered = text_head.lower()
    for pattern, label in TIPO_NORMA_PATTERNS:
        if re.search(pattern, lowered):
            return label
    return "desconocido"


def detect_fecha(full_text: str) -> date | None:
    m = FECHA_PATTERN.search(full_text)
    if not m:
        return None
    dia, mes_str, anio = m.groups()
    mes = MESES.get(mes_str.lower())
    if not mes:
        return None
    try:
        return date(int(anio), mes, int(dia))
    except ValueError:
        return None


def detect_dominios(full_text: str) -> list[str]:
    lowered = full_text.lower()
    scores = {}
    for dominio, keywords in KEYWORDS_DOMINIO.items():
        score = sum(lowered.count(kw) for kw in keywords)
        if score > 0:
            scores[dominio] = score
    if not scores:
        return []
    # Se queda con los dominios que tengan al menos 30% de la señal del
    # dominio más fuerte (para permitir normas que cruzan materias).
    max_score = max(scores.values())
    return [d for d, s in scores.items() if s >= max_score * 0.3]


def extract_titulo(full_text: str) -> str:
    """Primera línea no vacía y razonablemente larga como título tentativo."""
    for line in full_text.splitlines():
        line = line.strip()
        if len(line) > 15:
            return line[:300]
    return "Sin título detectado"


# ---------------------------------------------------------------------
# 3. Chunking jerárquico (Título > Capítulo > Artículo)
# ---------------------------------------------------------------------

HEADING_PATTERN = re.compile(
    r"^\s*(T[ÍI]TULO\s+[IVXLCDM]+[^\n]*|"
    r"CAP[ÍI]TULO\s+[IVXLCDM]+[^\n]*|"
    r"Art[íi]culo\s+\d+[°º]?\.?[-–—]?[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)


def chunk_by_hierarchy(full_text: str) -> list[dict]:
    matches = list(HEADING_PATTERN.finditer(full_text))

    if not matches:
        # Sin estructura de artículos detectable -> un solo chunk.
        return [{
            "referencia_jerarquica": "Texto completo (sin estructura de artículos detectada)",
            "contenido": full_text.strip(),
        }]

    chunks = []
    current_titulo = None
    current_capitulo = None

    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        content = full_text[start:end].strip()

        heading_lower = heading.lower()
        if heading_lower.startswith("título") or heading_lower.startswith("titulo"):
            current_titulo = heading
            current_capitulo = None
            continue  # el título por sí solo no es un chunk consultable
        elif heading_lower.startswith("capítulo") or heading_lower.startswith("capitulo"):
            current_capitulo = heading
            continue  # idem para el capítulo

        # Es un Artículo -> este sí es un chunk
        partes = [p for p in [current_titulo, current_capitulo, heading] if p]
        referencia = ", ".join(partes)
        chunks.append({
            "referencia_jerarquica": referencia,
            "contenido": content,
        })

    if not chunks:
        # Había títulos/capítulos pero ningún "Artículo" -> fallback
        return [{
            "referencia_jerarquica": "Texto completo (sin artículos individuales detectados)",
            "contenido": full_text.strip(),
        }]

    return chunks


# ---------------------------------------------------------------------
# 4. Embeddings vía Ollama
# ---------------------------------------------------------------------

def embed_text(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": OLLAMA_EMBED_MODEL, "prompt": NOMIC_DOC_PREFIX + text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def check_ollama_ready():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(OLLAMA_EMBED_MODEL in m for m in models):
            print(f"⚠️  Ollama está corriendo pero no encuentro el modelo "
                  f"'{OLLAMA_EMBED_MODEL}'. Corre: ollama pull {OLLAMA_EMBED_MODEL}")
            sys.exit(1)
    except requests.RequestException:
        print(f"❌ No pude conectar a Ollama en {OLLAMA_URL}. "
              f"¿Corriste 'ollama serve'?")
        sys.exit(1)


# ---------------------------------------------------------------------
# 5. Detección de relaciones (candidatos, no confirmados)
# ---------------------------------------------------------------------

def find_relacion_candidates(full_text: str) -> list[dict]:
    candidates = []
    for tipo_relacion, pattern in RELACION_PATTERNS:
        for m in pattern.finditer(full_text):
            tipo_doc_ref, numero_ref = m.group(1), m.group(2)
            candidates.append({
                "tipo_relacion": tipo_relacion,
                "referencia_texto": f"{tipo_doc_ref.strip()} N° {numero_ref.strip()}",
            })
    return candidates


def match_existing_norma(cur, referencia_texto: str):
    """Busca en `normas` alguna cuyo título contenga la referencia detectada."""
    numero = referencia_texto.split("N°")[-1].strip()
    cur.execute(
        "select id, titulo from normas where titulo ilike %s limit 1",
        (f"%{numero}%",),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ingestor mecánico de normas (Legal AI)")
    parser.add_argument("pdf_path", help="Ruta al PDF de la norma")
    parser.add_argument("--dominio", action="append", choices=sorted(DOMINIOS_VALIDOS),
                         help="Forzar dominio (puede repetirse para varios)")
    parser.add_argument("--tipo-norma", help="Forzar tipo de norma (ej. 'decreto supremo')")
    parser.add_argument("--entidad", help="Forzar entidad emisora (ej. 'SUNAT')")
    parser.add_argument("--familia", choices=["NEC", "FIDIC", "IFOA", "AIA"],
                         help="Solo para dominio=contratos: familia del estándar")
    parser.add_argument("--dry-run", action="store_true",
                         help="No escribe en Supabase, solo muestra qué haría")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"❌ No existe el archivo: {args.pdf_path}")
        sys.exit(1)

    if not args.dry_run:
        if not SUPABASE_DB_URL:
            print("❌ Falta SUPABASE_DB_URL. Copia scripts/.env.example a "
                  "scripts/.env y complétalo.")
            sys.exit(1)
        check_ollama_ready()

    print(f"\n📄 Procesando: {args.pdf_path}")

    # --- Hash + duplicado ---
    with open(args.pdf_path, "rb") as f:
        file_bytes = f.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    conn = None
    cur = None
    if not args.dry_run:
        conn = psycopg2.connect(SUPABASE_DB_URL)
        register_vector(conn)
        cur = conn.cursor()
        cur.execute("select id from normas where hash_archivo = %s", (file_hash,))
        existing = cur.fetchone()
        if existing:
            print(f"⚠️  Ya existe una norma con este hash (id={existing[0]}). "
                  f"Se omite para no duplicar.")
            cur.close()
            conn.close()
            return

    # --- Extracción de texto (con OCR si hace falta) ---
    pages_text = extract_text_per_page(args.pdf_path)
    full_text = "\n".join(pages_text)

    if len(full_text.strip()) < 50:
        print("❌ No se pudo extraer texto útil del PDF (ni nativo ni por OCR). "
              "Revisa la calidad del escaneo.")
        sys.exit(1)

    # --- Metadata ---
    titulo = extract_titulo(full_text)
    tipo_norma = args.tipo_norma or detect_tipo_norma(full_text[:2000])
    fecha_publicacion = detect_fecha(full_text)
    dominios = args.dominio or detect_dominios(full_text)
    entidad_emisora = args.entidad

    if not dominios:
        print("⚠️  No se pudo determinar el dominio automáticamente. "
              "Usa --dominio para indicarlo manualmente.")
        sys.exit(1)

    print(f"  Título (tentativo): {titulo}")
    print(f"  Tipo de norma: {tipo_norma}")
    print(f"  Fecha detectada: {fecha_publicacion or '(no detectada)'}")
    print(f"  Dominio(s): {dominios}")

    # --- Chunking ---
    chunks = chunk_by_hierarchy(full_text)
    print(f"  Chunks generados: {len(chunks)}")

    # --- Candidatos de relación (derogación/modificación) ---
    relacion_candidates = find_relacion_candidates(full_text)
    if relacion_candidates:
        print(f"  Candidatos de derogación/modificación detectados: "
              f"{len(relacion_candidates)} (quedarán como TENTATIVOS)")

    if args.dry_run:
        print("\n🔎 --dry-run: no se escribió nada en Supabase.")
        print("\nPrimeros 2 chunks de muestra:")
        for c in chunks[:2]:
            print(f"  [{c['referencia_jerarquica']}] {c['contenido'][:150]}...")
        return

    # --- Insertar norma ---
    cur.execute(
        """
        insert into normas
            (titulo, tipo_norma, familia, entidad_emisora, fecha_publicacion,
             dominios, estado, hash_archivo, resumen)
        values (%s, %s, %s, %s, %s, %s, 'pendiente_validacion', %s, %s)
        returning id
        """,
        (
            titulo, tipo_norma, args.familia, entidad_emisora, fecha_publicacion,
            dominios, file_hash, f"Ingerido automáticamente. {len(chunks)} artículo(s).",
        ),
    )
    norma_id = cur.fetchone()[0]
    print(f"  ✅ Norma insertada (id={norma_id}, estado=pendiente_validacion)")

    # --- Embeddings + chunks ---
    # Nota: dominios[0] se usa para el chunk si es multi-dominio; si necesitas
    # chunks por dominio separado, corre el script una vez por dominio o
    # ajusta esta línea a tu criterio.
    dominio_principal = dominios[0]
    for i, chunk in enumerate(chunks, 1):
        embedding = embed_text(chunk["contenido"])
        cur.execute(
            """
            insert into chunks_embeddings
                (norma_id, dominio, referencia_jerarquica, contenido, embedding)
            values (%s, %s, %s, %s, %s)
            """,
            (norma_id, dominio_principal, chunk["referencia_jerarquica"],
             chunk["contenido"], embedding),
        )
        if i % 10 == 0 or i == len(chunks):
            print(f"    ...{i}/{len(chunks)} chunks vectorizados")

    # --- Relaciones tentativas ---
    relaciones_guardadas = 0
    for cand in relacion_candidates:
        match = match_existing_norma(cur, cand["referencia_texto"])
        if match:
            norma_afectada_id, titulo_afectada = match
            cur.execute(
                """
                insert into relaciones_normas
                    (norma_origen_id, norma_afectada_id, tipo_relacion,
                     propuesto_por, confirmado)
                values (%s, %s, %s, 'bibliotecario_script', false)
                """,
                (norma_id, norma_afectada_id, cand["tipo_relacion"]),
            )
            relaciones_guardadas += 1
            print(f"    🔗 Candidato: {cand['tipo_relacion']} → "
                  f"'{titulo_afectada[:60]}...' (pendiente de confirmar)")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n✅ Listo. {len(chunks)} chunks guardados, "
          f"{relaciones_guardadas} relación(es) tentativa(s) para revisar.")
    print("👉 Siguiente paso: abre tu sesión de Claude Code y pide al CLO "
          "que revise las normas 'pendiente_validacion' y confirme o "
          "rechace las relaciones tentativas.")


if __name__ == "__main__":
    main()
