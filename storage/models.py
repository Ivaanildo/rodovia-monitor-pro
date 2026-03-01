"""
Definição de tabelas — SQLAlchemy Core (sem ORM).

Esquema:
  ciclos          — Um registro por ciclo de coleta completo
  snapshots_rotas — Um registro por (ciclo × trecho), FK → ciclos(id) CASCADE
"""
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)

metadata = MetaData()

# ---------------------------------------------------------------------------
# ciclos — metadados de cada ciclo de coleta
# ---------------------------------------------------------------------------
ciclos = Table(
    "ciclos",
    metadata,
    Column("id",            Integer, primary_key=True, autoincrement=True),
    Column("ts",            Text,    nullable=False),        # "DD/MM/YYYY HH:MM:SS"
    Column("ts_iso",        Text,    nullable=False),        # "2026-02-25T14:30:00"
    Column("fontes",        Text,    nullable=False, default="[]"),   # JSON list
    Column("total_trechos", Integer, nullable=False, default=0),
)

Index("idx_ciclos_ts_iso", ciclos.c.ts_iso)

# ---------------------------------------------------------------------------
# snapshots_rotas — estado de cada trecho em cada ciclo
# ---------------------------------------------------------------------------
snapshots_rotas = Table(
    "snapshots_rotas",
    metadata,
    Column("id",             Integer, primary_key=True, autoincrement=True),
    Column(
        "ciclo_id", Integer,
        ForeignKey("ciclos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("trecho",          Text,    nullable=False),
    Column("rodovia",         Text,    nullable=True),
    Column("sentido",         Text,    nullable=True),
    Column("status",          Text,    nullable=False),
    Column("ocorrencia",      Text,    nullable=True),
    Column("atraso_min",      Float,   nullable=True),
    Column("confianca_pct",   Float,   nullable=True),
    Column("conflito_fontes", Integer, nullable=False, default=0),
    # Desnormalizado para queries rápidas sem JOIN
    Column("ts_iso",          Text,    nullable=False),
)

# Índice composto — padrão de query mais frequente
Index("idx_sr_trecho_ts",  snapshots_rotas.c.trecho,  snapshots_rotas.c.ts_iso)
Index("idx_sr_ts_iso",     snapshots_rotas.c.ts_iso)
