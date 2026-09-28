#!/usr/bin/env python3
"""
pendientes.py — Lista qué dejó el ingestor mecánico para que el CLO y
los gerentes revisen en tu siguiente sesión de Cowork (claude.ai).

Uso:
    python pendientes.py
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")


def main():
    if not SUPABASE_DB_URL:
        print("❌ Falta SUPABASE_DB_URL en scripts/.env")
        return

    conn = psycopg2.connect(SUPABASE_DB_URL)
    cur = conn.cursor()

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
        where rn.confirmado = false
        order by rn.creado_en
        """
    )
    relaciones_pendientes = cur.fetchall()

    cur.close()
    conn.close()

    print(f"\n📋 Normas pendientes de validación: {len(normas_pendientes)}")
    for id_, titulo, dominios, creado in normas_pendientes:
        print(f"  - [{id_}] ({', '.join(dominios)}) {titulo[:80]}")

    print(f"\n🔗 Relaciones tentativas sin confirmar: {len(relaciones_pendientes)}")
    for id_, origen, tipo, afectada in relaciones_pendientes:
        print(f"  - [{id_}] '{origen[:50]}' --{tipo}--> '{afectada[:50]}'")

    if normas_pendientes or relaciones_pendientes:
        print("\n👉 Abre tu chat de Cowork (claude.ai) y pide al CLO que "
              "revise estos pendientes con el gerente del dominio "
              "correspondiente.")
    else:
        print("\n✅ No hay nada pendiente de revisión.")


if __name__ == "__main__":
    main()
