#!/usr/bin/env python3
"""
ingest.py — Ingestor mecánico de normas para Legal AI
=======================================================

Este script es el PROCESO DE INGESTA: se corre aparte, en tu terminal,
antes de usar el área legal. No requiere Claude Code ni gasta tu cupo
de plan de Claude — es código Python puro que hace el trabajo mecánico
de leer, ordenar y vectorizar normas.

El PROCESO DE USO (consultas, revisión de lo ingerido, correcciones) es
un tema aparte y se hace después, en Cowork (claude.ai) — ver el README
principal, Parte B.

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

Lo que este script NO hace (a propósito) — eso es trabajo del CLO y los
gerentes en Cowork, no de este script:
  - Confirmar que una relación de derogación/modificación es correcta.
    La detección por regex es un candidato, no una verdad legal.
  - Decidir si la norma queda "vigente" — el estado se guarda como
    `pendiente_validacion` para que se revise después en Cowork.
  - Cualquier interpretación de fondo del contenido.

Uso — un solo archivo:
    python ingest.py ruta/a/norma.pdf
    python ingest.py ruta/a/norma.pdf --dominio tributario
    python ingest.py ruta/a/norma.pdf --dry-run

Uso — una carpeta completa (busca *.pdf recursivamente):
    python ingest.py ruta/a/carpeta_de_normas/
    python ingest.py ruta/a/carpeta_de_normas/ --dominio laboral

Requisitos del sistema (además de requirements.txt):
    - Ollama corriendo (`ollama serve`) con `ollama pull nomic-embed-text`
    - Tesseract OCR instalado, con el paquete de idioma español:
        macOS:   brew install tesseract tesseract-lang
        Ubuntu:  sudo apt install tesseract-ocr tesseract-ocr-spa
        Windows: instalador de https://github.com/UB-Mannheim/tesseract/wiki
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF
import psycopg2
import pytesseract
import requests
from dotenv import load_dotenv
from PIL import Image
from pgvector.psycopg2 import register_vector
from tqdm import tqdm

# ---------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
# Ollama limita el contexto a 2048 tokens por defecto para cualquier modelo,
# aunque nomic-embed-text soporta hasta 8192. Este override se manda por si
# tu versión de Ollama lo respeta para /api/embeddings, pero varias versiones
# lo ignoran en ese endpoint — por eso NO confiamos solo en esto: ver
# MAX_CHARS_POR_EMBEDDING abajo, que es la protección real.
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
# Umbral de caracteres a partir del cual partimos el chunk en pedazos más
# chicos antes de pedir el embedding. 4000 caracteres de texto legal en
# español equivalen aprox. a 1000-1300 tokens — con margen de sobra bajo
# el límite de 2048 tokens que Ollama aplica por defecto, incluso si
# OLLAMA_NUM_CTX de arriba termina siendo ignorado por tu versión.
MAX_CHARS_POR_EMBEDDING = 4000

# Nomic recomienda prefijar los textos indexados con "search_document: "
# (y las consultas, en el lado de Cowork, con "search_query: ") para
# mejorar la calidad del retrieval.
NOMIC_DOC_PREFIX = "search_document: "

MIN_CHARS_NATIVE_PAGE = 40  # bajo este umbral, la página se considera escaneada

DOMINIOS_VALIDOS = {"tributario", "corporativo", "laboral", "contratos"}

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

TIPO_NORMA_PATTERNS = [
    (r"decreto\s+legislativo", "decreto legislativo"),
    (r"decreto\s+supremo", "decreto supremo"),
    (r"resoluci[oó]n\s+ministerial", "resolución ministerial"),
    (r"resoluci[oó]n\s+de\s+superintendencia", "resolución de superintendencia"),
    (r"resoluci[oó]n\s+legislativa", "resolución legislativa"),
    (r"directiva", "directiva"),
    (r"\bley\s+n[°º]?\s*\d+", "ley"),
]

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

HEADING_PATTERN = re.compile(
    r"^\s*(T[ÍI]TULO\s+[IVXLCDM]+[^\n]*|"
    r"CAP[ÍI]TULO\s+[IVXLCDM]+[^\n]*|"
    r"Art[íi]culo\s+\d+[°º]?\.?[-–—]?[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)

FASES = [
    "Leyendo PDF / OCR",
    "Clasificando metadata",
    "Generando chunks",
    "Generando embeddings",
    "Guardando en Supabase",
    "Detectando relaciones",
]


def fase(n: int, detalle: str = ""):
    """Imprime un encabezado de fase consistente, para que el usuario
    siempre sepa en qué parte del proceso está y nunca piense que el
    script se congeló."""
    total = len(FASES)
    nombre = FASES[n - 1]
    extra = f" — {detalle}" if detalle else ""
    print(f"  [{n}/{total}] {nombre}{extra}")


# ---------------------------------------------------------------------
# 1. Extracción de texto + decisión de OCR
# ---------------------------------------------------------------------

def extract_text_per_page(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    pages_text = []
    ocr_pages = 0

    for page_num in tqdm(range(len(doc)), desc="    páginas", unit="pág", leave=False):
        page = doc[page_num]
        native_text = page.get_text().strip()

        if len(native_text) >= MIN_CHARS_NATIVE_PAGE:
            pages_text.append(native_text)
            continue

        ocr_pages += 1
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr_text = pytesseract.image_to_string(img, lang="spa")
        pages_text.append(ocr_text.strip())

    doc.close()

    if ocr_pages:
        print(f"    OCR aplicado en {ocr_pages}/{len(pages_text)} páginas.")
    else:
        print("    Texto nativo en todas las páginas (sin OCR).")

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
    max_score = max(scores.values())
    return [d for d, s in scores.items() if s >= max_score * 0.3]


def extract_titulo(full_text: str) -> str:
    for line in full_text.splitlines():
        line = line.strip()
        if len(line) > 15:
            return line[:300]
    return "Sin título detectado"


# ---------------------------------------------------------------------
# 3. Chunking jerárquico (Título > Capítulo > Artículo)
# ---------------------------------------------------------------------

def chunk_by_hierarchy(full_text: str) -> list[dict]:
    matches = list(HEADING_PATTERN.finditer(full_text))

    if not matches:
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
            continue
        elif heading_lower.startswith("capítulo") or heading_lower.startswith("capitulo"):
            current_capitulo = heading
            continue

        partes = [p for p in [current_titulo, current_capitulo, heading] if p]
        referencia = ", ".join(partes)
        chunks.append({
            "referencia_jerarquica": referencia,
            "contenido": content,
        })

    if not chunks:
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
        json={
            "model": OLLAMA_EMBED_MODEL,
            "prompt": NOMIC_DOC_PREFIX + text,
            "options": {"num_ctx": OLLAMA_NUM_CTX},
        },
        timeout=120,
    )
    if resp.status_code != 200:
        try:
            detalle = resp.json().get("error", resp.text)
        except Exception:
            detalle = resp.text
        raise RuntimeError(
            f"Ollama devolvió {resp.status_code} al generar el embedding: {detalle}"
        )
    return resp.json()["embedding"]


def _split_por_palabras(texto: str, max_chars: int) -> list[str]:
    """Último recurso: si un párrafo entero (sin saltos de línea internos)
    ya supera max_chars, lo partimos por palabras para no cortar una a la
    mitad."""
    palabras = texto.split(" ")
    partes = []
    actual = ""
    for palabra in palabras:
        candidato = f"{actual} {palabra}" if actual else palabra
        if len(candidato) > max_chars and actual:
            partes.append(actual)
            actual = palabra
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    return partes


def split_texto_largo(texto: str, max_chars: int = MAX_CHARS_POR_EMBEDDING) -> list[str]:
    """Parte un texto largo en trozos que no superen max_chars, para no
    exceder el contexto del modelo de embeddings aunque el chunking
    jerárquico haya agrupado un artículo excepcionalmente largo (incisos
    extensos, tablas, listas, etc.).

    Primero intenta partir por párrafo (saltos de línea). Muchos PDFs de
    normas extraen un artículo entero como una sola línea continua (sin
    \\n internos) — en ese caso, un solo "párrafo" ya supera max_chars por
    sí solo, así que además partimos por palabras como último recurso,
    garantizando que ningún fragmento resultante exceda el límite."""
    if len(texto) <= max_chars:
        return [texto]

    piezas = [p for p in texto.split("\n") if p.strip()] or [texto]

    partes = []
    actual = ""
    for pieza in piezas:
        if len(pieza) > max_chars:
            if actual:
                partes.append(actual)
                actual = ""
            partes.extend(_split_por_palabras(pieza, max_chars))
            continue
        candidato = f"{actual}\n{pieza}" if actual else pieza
        if len(candidato) > max_chars and actual:
            partes.append(actual)
            actual = pieza
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    return partes


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


def match_existing_norma(cur, referencia_texto: str, excluir_id: str | None = None):
    numero = referencia_texto.split("N°")[-1].strip()
    if not numero:
        # referencia_texto termina justo en "N°" sin número después (o está
        # vacío tras el split): sin este guard, el ilike de abajo quedaría
        # como '%%' y matchearía cualquier norma al azar.
        return None
    if excluir_id:
        cur.execute(
            "select id, titulo from normas where titulo ilike %s and id != %s limit 1",
            (f"%{numero}%", excluir_id),
        )
    else:
        cur.execute(
            "select id, titulo from normas where titulo ilike %s limit 1",
            (f"%{numero}%",),
        )
    return cur.fetchone()


def backfill_pending_relations(cur, nueva_norma_id: str, nueva_norma_titulo: str) -> int:
    """Cada vez que se ingiere una norma nueva, esta función revisa si
    alguna relación detectada en una ingesta ANTERIOR se había quedado
    sin destino (norma_afectada_id = null) porque la norma referenciada
    todavía no existía — y si esta norma nueva es justo esa referencia,
    la empareja ahora. Así 'la base de datos tiene más normas que la
    última vez' se traduce automáticamente en relaciones resueltas,
    sin que nadie tenga que volver a correr la ingesta original."""
    cur.execute(
        "select id, referencia_texto from relaciones_normas where norma_afectada_id is null"
    )
    pendientes = cur.fetchall()

    resueltas = 0
    for rel_id, referencia_texto in pendientes:
        if not referencia_texto:
            continue
        numero = referencia_texto.split("N°")[-1].strip()
        if numero and numero.lower() in nueva_norma_titulo.lower():
            cur.execute(
                "update relaciones_normas set norma_afectada_id = %s where id = %s",
                (nueva_norma_id, rel_id),
            )
            resueltas += 1
    return resueltas


# ---------------------------------------------------------------------
# Procesamiento de un solo archivo (reutilizado en modo carpeta)
# ---------------------------------------------------------------------

def process_one(pdf_path: str, args, cur) -> str:
    """Devuelve un string corto con el resultado, para el resumen final."""
    print(f"\n📄 {pdf_path}")

    with open(pdf_path, "rb") as f:
        file_bytes = f.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    if not args.dry_run:
        cur.execute("select id from normas where hash_archivo = %s", (file_hash,))
        existing = cur.fetchone()
        if existing:
            print(f"  ⚠️  Ya existe (id={existing[0]}). Se omite.")
            return "duplicado"

    fase(1)
    pages_text = extract_text_per_page(pdf_path)
    full_text = "\n".join(pages_text)

    if len(full_text.strip()) < 50:
        print("  ❌ No se pudo extraer texto útil (ni nativo ni por OCR). Se omite.")
        return "error_sin_texto"

    fase(2)
    titulo = extract_titulo(full_text)
    tipo_norma = args.tipo_norma or detect_tipo_norma(full_text[:2000])
    fecha_publicacion = detect_fecha(full_text)
    dominios = args.dominio or detect_dominios(full_text)
    entidad_emisora = args.entidad

    if not dominios:
        print("  ⚠️  No se pudo determinar el dominio automáticamente. "
              "Usa --dominio para indicarlo manualmente. Se omite.")
        return "error_sin_dominio"

    print(f"    Título (tentativo): {titulo}")
    print(f"    Tipo de norma: {tipo_norma}")
    print(f"    Fecha detectada: {fecha_publicacion or '(no detectada)'}")
    print(f"    Dominio(s): {dominios}")

    fase(3)
    chunks = chunk_by_hierarchy(full_text)
    print(f"    Chunks generados: {len(chunks)}")

    relacion_candidates = find_relacion_candidates(full_text)

    if args.dry_run:
        print("\n  🔎 --dry-run: no se escribió nada en Supabase.")
        for c in chunks[:2]:
            print(f"    [{c['referencia_jerarquica']}] {c['contenido'][:150]}...")
        return "dry_run"

    cur.execute(
        """
        insert into normas
            (titulo, tipo_norma, familia, entidad_emisora, fecha_publicacion,
             dominios, estado, hash_archivo, resumen)
        values (%s, %s, %s, %s, %s, %s::dominio_legal[], 'pendiente_validacion', %s, %s)
        returning id
        """,
        (
            titulo, tipo_norma, args.familia, entidad_emisora, fecha_publicacion,
            dominios, file_hash, f"Ingerido automáticamente. {len(chunks)} artículo(s).",
        ),
    )
    norma_id = cur.fetchone()[0]

    dominio_principal = dominios[0]

    # Si algún chunk quedó excepcionalmente largo (incisos extensos, tablas,
    # texto sin estructura de artículos clara), lo partimos en sub-fragmentos
    # antes de pedir el embedding, para no superar el contexto del modelo
    # (ver split_texto_largo / OLLAMA_NUM_CTX más arriba).
    chunks_finales = []
    for chunk in chunks:
        partes = split_texto_largo(chunk["contenido"])
        if len(partes) == 1:
            chunks_finales.append(chunk)
        else:
            for idx, parte in enumerate(partes, start=1):
                chunks_finales.append({
                    "referencia_jerarquica":
                        f"{chunk['referencia_jerarquica']} (parte {idx}/{len(partes)})",
                    "contenido": parte,
                })

    detalle_fase4 = f"{len(chunks_finales)} chunk(s)"
    if len(chunks_finales) != len(chunks):
        detalle_fase4 += f" ({len(chunks_finales) - len(chunks)} de más por partición de artículos largos)"
    fase(4, detalle_fase4)

    for i, chunk in enumerate(tqdm(chunks_finales, desc="    embeddings", unit="chunk", leave=False)):
        try:
            chunk["_embedding"] = embed_text(chunk["contenido"])
        except Exception as e:
            preview = chunk["contenido"][:150].replace("\n", " ")
            n_chars = len(chunk["contenido"])
            raise RuntimeError(
                f"Falló el embedding del chunk {i + 1}/{len(chunks_finales)} "
                f"[{chunk['referencia_jerarquica']}] ({n_chars} caracteres): {e}\n"
                f"    Contenido (primeros 150 car.): {preview!r}"
            ) from e

    fase(5)
    for chunk in chunks_finales:
        cur.execute(
            """
            insert into chunks_embeddings
                (norma_id, dominio, referencia_jerarquica, contenido, embedding)
            values (%s, %s, %s, %s, %s)
            """,
            (norma_id, dominio_principal, chunk["referencia_jerarquica"],
             chunk["contenido"], chunk["_embedding"]),
        )
    print(f"    Norma insertada (id={norma_id}, estado=pendiente_validacion)")

    fase(6, f"{len(relacion_candidates)} candidato(s) detectado(s)" if relacion_candidates else "sin candidatos")
    relaciones_con_destino = 0
    relaciones_sin_destino = 0
    for cand in relacion_candidates:
        match = match_existing_norma(cur, cand["referencia_texto"], excluir_id=norma_id)
        norma_afectada_id = match[0] if match else None
        cur.execute(
            """
            insert into relaciones_normas
                (norma_origen_id, norma_afectada_id, referencia_texto,
                 tipo_relacion, propuesto_por, confirmado)
            values (%s, %s, %s, %s, 'bibliotecario_script', false)
            """,
            (norma_id, norma_afectada_id, cand["referencia_texto"], cand["tipo_relacion"]),
        )
        if match:
            relaciones_con_destino += 1
            print(f"    🔗 {cand['tipo_relacion']} → "
                  f"'{match[1][:60]}...' (pendiente de confirmar)")
        else:
            relaciones_sin_destino += 1
            print(f"    ⏳ {cand['tipo_relacion']} → '{cand['referencia_texto']}' "
                  f"(esa norma aún no está en el corpus; quedará pendiente "
                  f"hasta que se ingiera)")

    # Esta norma recién ingresada podría ser, a su vez, la norma que
    # alguna ingesta ANTERIOR estaba esperando encontrar.
    resueltas_retro = backfill_pending_relations(cur, norma_id, titulo)
    if resueltas_retro:
        print(f"    ♻️  {resueltas_retro} relación(es) de ingestas anteriores "
              f"quedaron emparejadas ahora que esta norma existe.")

    print(f"  ✅ Completado: {len(chunks)} chunks, "
          f"{relaciones_con_destino} relación(es) lista(s) para confirmar, "
          f"{relaciones_sin_destino} en espera de su norma referenciada, "
          f"{resueltas_retro} resuelta(s) retroactivamente.")
    return "ok"


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ingestor mecánico de normas (Legal AI) — acepta un "
                     "archivo PDF o una carpeta con varios."
    )
    parser.add_argument("path", help="Ruta a un PDF, o a una carpeta con varios PDFs")
    parser.add_argument("--dominio", action="append", choices=sorted(DOMINIOS_VALIDOS),
                         help="Forzar dominio (puede repetirse para varios). "
                              "Se aplica a TODOS los archivos si es una carpeta.")
    parser.add_argument("--tipo-norma", help="Forzar tipo de norma (ej. 'decreto supremo')")
    parser.add_argument("--entidad", help="Forzar entidad emisora (ej. 'SUNAT')")
    parser.add_argument("--familia", choices=["NEC", "FIDIC", "IFOA", "AIA"],
                         help="Solo para dominio=contratos: familia del estándar")
    parser.add_argument("--dry-run", action="store_true",
                         help="No escribe en Supabase, solo muestra qué haría")
    args = parser.parse_args()

    input_path = Path(args.path)
    if not input_path.exists():
        print(f"❌ No existe la ruta: {args.path}")
        sys.exit(1)

    if input_path.is_dir():
        pdf_files = sorted(input_path.rglob("*.pdf"))
        if not pdf_files:
            print(f"❌ No encontré ningún .pdf dentro de {args.path}")
            sys.exit(1)
        print(f"📂 Carpeta detectada: {len(pdf_files)} PDF(s) encontrados.")
    else:
        pdf_files = [input_path]

    if not args.dry_run:
        if not SUPABASE_DB_URL:
            print("❌ Falta SUPABASE_DB_URL. Copia scripts/.env.example a "
                  "scripts/.env y complétalo.")
            sys.exit(1)
        check_ollama_ready()

    conn = None
    cur = None
    if not args.dry_run:
        conn = psycopg2.connect(SUPABASE_DB_URL)
        register_vector(conn)
        cur = conn.cursor()

    resultados = {}
    archivo_iter = pdf_files if len(pdf_files) == 1 else tqdm(
        pdf_files, desc="Progreso total", unit="archivo"
    )

    try:
        for pdf_path in archivo_iter:
            try:
                resultado = process_one(str(pdf_path), args, cur)
            except Exception as e:
                print(f"  ❌ Error inesperado procesando {pdf_path}: {e}")
                resultado = "error"
                if conn:
                    conn.rollback()
            else:
                if conn:
                    conn.commit()
            resultados[resultado] = resultados.get(resultado, 0) + 1
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    print("\n" + "=" * 50)
    print("RESUMEN")
    print("=" * 50)
    for estado, cantidad in resultados.items():
        print(f"  {estado}: {cantidad}")

    if resultados.get("ok"):
        print("\n👉 Siguiente paso: abre tu chat de Cowork (claude.ai) y pide "
              "al CLO que revise las normas pendientes — ver README, Parte B.")


if __name__ == "__main__":
    main()
