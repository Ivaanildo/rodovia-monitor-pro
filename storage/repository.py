"""
RotaRepository — camada de acesso a dados para histórico de coletas.

Operações:
  salvar_ciclo(dados, fontes_ativas)          → ciclo_id (int)
  buscar_historico_trecho(trecho, horas)       → list[dict]
  buscar_tendencias_hora(trecho, horas)        → list[dict] (agrupado por hora)
  buscar_historico_csv(horas)                  → str (CSV completo)
  purgar_antigos(retencao_dias)                → int (registros removidos)
  contar_ciclos()                              → int
  ultimo_ciclo_ts()                            → str | None
"""
import csv
import io
import json
import logging
from datetime import datetime, timedelta, timezone

_BRT = timezone(timedelta(hours=-3))

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.engine import Engine

from storage.models import ciclos, snapshots_rotas

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(_BRT).isoformat(timespec="seconds")


def _cutoff_iso(horas: int) -> str:
    return (datetime.now(_BRT) - timedelta(hours=horas)).isoformat(timespec="seconds")


def _cutoff_days_iso(dias: int) -> str:
    return (datetime.now(_BRT) - timedelta(days=dias)).isoformat(timespec="seconds")


class RotaRepository:
    """Repositorio de dados historicos — PostgreSQL via SQLAlchemy Core."""

    def __init__(self, engine: Engine):
        self._engine = engine
        logger.info("RotaRepository: conectado ao banco.")

    # ── Escrita ──────────────────────────────────────────────────────────────

    def salvar_ciclo(
        self, dados: list[dict], fontes_ativas: list[str] | None = None
    ) -> int:
        """Persiste um ciclo completo.

        Returns:
            ciclo_id (int) do registro inserido em `ciclos`.
        """
        if not dados:
            return 0

        agora    = datetime.now(_BRT)
        ts_fmt   = agora.strftime("%d/%m/%Y %H:%M:%S")
        ts_iso   = agora.isoformat(timespec="seconds")
        fontes_j = json.dumps(fontes_ativas or [], ensure_ascii=False)

        with self._engine.begin() as conn:
            result = conn.execute(
                insert(ciclos).values(
                    ts=ts_fmt,
                    ts_iso=ts_iso,
                    fontes=fontes_j,
                    total_trechos=len(dados),
                )
            )
            ciclo_id = result.inserted_primary_key[0]

            rows = [
                {
                    "ciclo_id":       ciclo_id,
                    "trecho":         d.get("trecho") or "",
                    "rodovia":        d.get("rodovia") or "",
                    "sentido":        d.get("sentido") or "",
                    "status":         d.get("status") or "Sem dados",
                    "ocorrencia":     d.get("ocorrencia") or "",
                    "atraso_min":     d.get("atraso_min"),
                    "confianca_pct":  d.get("confianca_pct"),
                    "conflito_fontes": 1 if d.get("conflito_fontes") else 0,
                    "descricao":       d.get("descricao") or "",
                    "ts_iso":         ts_iso,
                }
                for d in dados
                if d.get("trecho")
            ]

            if rows:
                conn.execute(insert(snapshots_rotas), rows)

        logger.debug(
            f"RotaRepository: ciclo {ciclo_id} salvo "
            f"({len(rows)} trechos, fontes={fontes_ativas})"
        )
        return ciclo_id

    # ── Leitura — histórico bruto ─────────────────────────────────────────────

    def buscar_historico_trecho(
        self, trecho: str, horas: int = 24
    ) -> list[dict]:
        """Retorna pontos históricos de um trecho nas últimas N horas."""
        cutoff = _cutoff_iso(horas)
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    snapshots_rotas.c.ts_iso,
                    snapshots_rotas.c.status,
                    snapshots_rotas.c.ocorrencia,
                    snapshots_rotas.c.atraso_min,
                    snapshots_rotas.c.confianca_pct,
                    snapshots_rotas.c.conflito_fontes,
                )
                .where(
                    snapshots_rotas.c.trecho  == trecho,
                    snapshots_rotas.c.ts_iso  >= cutoff,
                )
                .order_by(snapshots_rotas.c.ts_iso)
            )
            return [dict(r._mapping) for r in rows]

    # ── Leitura — tendências por hora ────────────────────────────────────────

    def buscar_tendencias_hora(
        self, trecho: str, periodo_horas: int = 168
    ) -> list[dict]:
        """Agrega dados por hora: status mais frequente + atraso médio.

        Args:
            trecho:        Nome exato do trecho.
            periodo_horas: Janela de tempo (padrão 168 h = 7 dias).

        Returns:
            list[dict] com chaves: hora, status_mais_frequente, atraso_medio, n_pontos.
        """
        rows = self.buscar_historico_trecho(trecho, periodo_horas)

        by_hour: dict[str, list] = {}
        for row in rows:
            hora = row["ts_iso"][:13]  # "2026-02-25T14"
            by_hour.setdefault(hora, []).append(row)

        result = []
        for hora in sorted(by_hour.keys()):
            pts     = by_hour[hora]
            atrasos = [p["atraso_min"] for p in pts if p["atraso_min"] is not None]
            statuses = [p["status"] for p in pts]
            status_freq = max(set(statuses), key=statuses.count) if statuses else "Sem dados"
            result.append({
                "hora":                   hora + ":00",   # ISO completo "2026-02-25T14:00"
                "status_mais_frequente":  status_freq,
                "atraso_medio":           round(sum(atrasos) / len(atrasos), 1) if atrasos else None,
                "n_pontos":               len(pts),
            })
        return result

    # ── Exportação CSV ────────────────────────────────────────────────────────

    def buscar_historico_csv(self, horas: int = 24) -> str:
        """Gera CSV de todos os trechos no período.

        Colunas: ts_iso, trecho, rodovia, sentido, status, ocorrencia,
                 atraso_min, confianca_pct, conflito_fontes.
        """
        cutoff = _cutoff_iso(horas)
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    snapshots_rotas.c.ts_iso,
                    snapshots_rotas.c.trecho,
                    snapshots_rotas.c.rodovia,
                    snapshots_rotas.c.sentido,
                    snapshots_rotas.c.status,
                    snapshots_rotas.c.ocorrencia,
                    snapshots_rotas.c.atraso_min,
                    snapshots_rotas.c.confianca_pct,
                    snapshots_rotas.c.conflito_fontes,
                )
                .where(snapshots_rotas.c.ts_iso >= cutoff)
                .order_by(snapshots_rotas.c.ts_iso, snapshots_rotas.c.trecho)
            )
            data = [dict(r._mapping) for r in rows]

        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "ts_iso", "trecho", "rodovia", "sentido", "status",
                "ocorrencia", "atraso_min", "confianca_pct", "conflito_fontes",
            ],
        )
        writer.writeheader()
        writer.writerows(data)
        return buf.getvalue()

    # ── Manutenção ───────────────────────────────────────────────────────────

    def purgar_antigos(self, retencao_dias: int = 90) -> int:
        """Remove ciclos (e snapshots via CASCADE) mais antigos que retencao_dias.

        Returns:
            Número de ciclos removidos.
        """
        cutoff = _cutoff_days_iso(retencao_dias)
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(ciclos).where(ciclos.c.ts_iso < cutoff)
            )
            removidos = result.rowcount

        if removidos:
            logger.info(f"RotaRepository: {removidos} ciclo(s) purgado(s) (>{retencao_dias}d)")
        return removidos

    # ── Consultas auxiliares ──────────────────────────────────────────────────

    def contar_ciclos(self) -> int:
        """Total de ciclos persistidos no banco."""
        with self._engine.connect() as conn:
            row = conn.execute(select(func.count()).select_from(ciclos)).scalar()
        return row or 0

    def ultimo_ciclo_ts(self) -> str | None:
        """Timestamp ISO do último ciclo salvo, ou None."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(ciclos.c.ts_iso)
                .order_by(ciclos.c.ts_iso.desc())
                .limit(1)
            ).scalar()
        return row

    def listar_trechos(self) -> list[str]:
        """Retorna nomes distintos de trechos com dados no banco."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(snapshots_rotas.c.trecho)
                .distinct()
                .order_by(snapshots_rotas.c.trecho)
            )
            return [r[0] for r in rows]

    def buscar_ultimo_snapshot_para_dashboard(self) -> tuple[list[dict], str | None, list[str]]:
        """Retorna o último ciclo completo para exibição no dashboard.

        Returns:
            (dados, ultimo_ciclo_ts, fontes_ativas)
            dados: lista de dicts com trecho, rodovia, sentido, status, ocorrencia, atraso_min, confianca_pct, conflito_fontes
            ultimo_ciclo_ts: string "DD/MM/YYYY HH:MM:SS" ou None
            fontes_ativas: lista de strings (vazia se não houver)
        """
        with self._engine.connect() as conn:
            row_ciclo = conn.execute(
                select(ciclos.c.id, ciclos.c.ts, ciclos.c.ts_iso, ciclos.c.fontes)
                .order_by(ciclos.c.ts_iso.desc())
                .limit(1)
            ).first()
            if not row_ciclo:
                return [], None, []
            ciclo_id = row_ciclo[0]
            ts_display = row_ciclo[1]
            try:
                fontes = json.loads(row_ciclo[3] or "[]")
            except (TypeError, ValueError):
                fontes = []

            rows = conn.execute(
                select(
                    snapshots_rotas.c.trecho,
                    snapshots_rotas.c.rodovia,
                    snapshots_rotas.c.sentido,
                    snapshots_rotas.c.status,
                    snapshots_rotas.c.ocorrencia,
                    snapshots_rotas.c.atraso_min,
                    snapshots_rotas.c.confianca_pct,
                    snapshots_rotas.c.conflito_fontes,
                    snapshots_rotas.c.descricao,
                )
                .where(snapshots_rotas.c.ciclo_id == ciclo_id)
                .order_by(snapshots_rotas.c.trecho)
            )
            dados = [
                {
                    "trecho": r[0] or "",
                    "rodovia": r[1] or "",
                    "sentido": r[2] or "",
                    "status": r[3] or "Sem dados",
                    "ocorrencia": r[4] or "",
                    "atraso_min": r[5],
                    "confianca_pct": r[6],
                    "conflito_fontes": bool(r[7]),
                    "descricao": r[8] or "",
                }
                for r in rows
            ]
            return dados, ts_display, fontes
