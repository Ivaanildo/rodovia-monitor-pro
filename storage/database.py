"""
Fabrica de engine PostgreSQL — RodoviaMonitor Pro (Supabase)

Conecta ao Supabase PostgreSQL via SUPABASE_DB_URL.
Compatible com SQLAlchemy 2.0 Core (sem ORM).
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_engine() -> Engine:
    """Cria engine PostgreSQL usando SUPABASE_DB_URL.

    A variavel de ambiente SUPABASE_DB_URL deve conter a connection string
    do Supabase no formato:
        postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres

    Returns:
        Engine SQLAlchemy configurada com pool_pre_ping para reconexao automatica.

    Raises:
        RuntimeError: Se SUPABASE_DB_URL nao estiver definida.
    """
    db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL nao definida. "
            "Configure a variavel de ambiente com a connection string do Supabase."
        )

    engine = create_engine(
        db_url,
        echo=False,
        pool_pre_ping=True,
    )

    return engine
