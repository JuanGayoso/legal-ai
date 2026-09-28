#!/usr/bin/env python3
"""
pendientes.py — Lista qué dejó el ingestor mecánico para que el CLO y
los gerentes revisen en tu siguiente sesión de Cowork (claude.ai).

Antes de listar, intenta emparejar automáticamente cualquier relación
que se haya quedado "esperando su norma" (norma_afectada_id = null)
por si algo se ingirió después que ahora sí calza — es la misma lógica
de backfill que corre ingest.py tras cada norma nueva, disponible acá
por si quieres re-chequear sin volver a ingestar nada.

Uso:
    python pendientes.py
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")


def resolver_relaciones_en_espera(cur) -> int:
    cur.execute(
        "select id, referencia_texto from relaciones_normas where norma_afectada_id is null"
    )
    en_espera = cur.fetchall()
    resueltas = 0
    for rel_id, referencia_texto in en_espera:
        if not referencia_texto:
            continue
        numero = referencia_texto.split("N°")[-1].strip()
        if not numero:
            continue
        cur.execute(
            "select id from normas where titulo ilike %s limit 1",
            (f"%{numero}%",),
        )
        match = cur.fetchone()
        if match:
            cur.execute(
                "update relaciones_normas set norma_afectada_id = %s where id = %s",
                (match[0], rel_id),
            )
            resueltas += 1
    return resueltas


def main():
    if not SUPABASE_DB_URL:
        print("❌ Falta SUPABASE_DB_URL en scripts/.env")
        return

    conn = psycopg2.connect(SUPABASE_DB_URL)
    cur = conn.cursor()

    resueltas = resolver_relaciones_en_espera(cur)
    conn.commit()
    if resueltas:
        print(f"♻️  {resueltas} relación(es) que esperaban su norma quedaron "
              f"emparejadas ahora.\n")

    cur.execute(
        "select id, titulo, dominios, creado_en from normas "
        "where estado = 'pendiente_validacion' order by creado_en"
    )
    normas_pendientes = cur.fetchall()

    cur.execute(
        """
        select rn.id, n1.titulo, rn.tipo_relacion, n2.titulo
        from relaciones_normas rn
        join normas n1 on n1.id = rn.norma_origen_id
        join normas n2 on n2.id = rn.norma_afectada_id
        where rn.confirmado = false and rn.norma_afectada_id is not null
        order by rn.creado_en
        """
    )
    relaciones_pendientes = cur.fetchall()

    cur.execute(
        """
        select rn.id, n1.titulo, rn.tipo_relacion, rn.referencia_texto
        from relaciones_normas rn
        join normas n1 on n1.id = rn.norma_origen_id
        where rn.norma_afectada_id is null
        order by rn.creado_en
        """
    )
    relaciones_en_espera = cur.fetchall()

    cur.close()
    conn.close()

    print(f"📋 Normas pendientes de validación: {len(normas_pendientes)}")
    for id_, titulo, dominios, creado in normas_pendientes:
        print(f"  - [{id_}] ({', '.join(dominios)}) {titulo[:80]}")

    print(f"\n🔗 Relaciones tentativas listas para confirmar: {len(relaciones_pendientes)}")
    for id_, origen, tipo, afectada in relaciones_pendientes:
        print(f"  - [{id_}] '{origen[:50]}' --{tipo}--> '{afectada[:50]}'")

    print(f"\n⏳ Relaciones esperando que su norma referenciada se ingiera: "
          f"{len(relaciones_en_espera)}")
    for id_, origen, tipo, referencia in relaciones_en_espera:
        print(f"  - [{id_}] '{origen[:50]}' --{tipo}--> '{referencia}' (no está en el corpus todavía)")

    if normas_pendientes or relaciones_pendientes:
        print("\n👉 Abre tu chat de Cowork (claude.ai) y pide al CLO que "
              "revise estos pendientes con el gerente del dominio "
              "correspondiente.")
    elif not relaciones_en_espera:
        print("\n✅ No hay nada pendiente de revisión.")


if __name__ == "__main__":
    main()
