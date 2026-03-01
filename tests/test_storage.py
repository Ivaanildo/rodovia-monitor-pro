"""
Testes — Persistência SQLite (Fase 5)

Cobertura:
  1.  salvar_ciclo: persiste ciclo e snapshots
  2.  salvar_ciclo: retorna ciclo_id > 0
  3.  salvar_ciclo: ignora dados sem campo 'trecho'
  4.  salvar_ciclo: lista vazia retorna 0
  5.  buscar_historico_trecho: retorna pontos no período correto
  6.  buscar_historico_trecho: respeita filtro de horas
  7.  buscar_historico_trecho: trecho inexistente retorna []
  8.  buscar_tendencias_hora: agrega por hora corretamente
  9.  buscar_tendencias_hora: status mais frequente
 10.  buscar_historico_csv: cabeçalho correto
 11.  buscar_historico_csv: conteúdo correto
 12.  purgar_antigos: remove ciclos antigos via CASCADE
 13.  purgar_antigos: preserva ciclos recentes
 14.  contar_ciclos: conta corretamente
 15.  ultimo_ciclo_ts: retorna ISO mais recente
 16.  listar_trechos: retorna trechos distintos ordenados
 17.  múltiplos ciclos: histórico contém pontos de todos os ciclos
 18.  thread safety: escrita concorrente não corrompe dados
"""
import csv
import io
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.database import get_engine
from storage.repository import RotaRepository


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def repo():
    """Repositório em memória — isolado por teste."""
    engine = get_engine(":memory:", wal_mode=False)
    return RotaRepository(engine)


def _dado(trecho="BR-101 SP-RJ", status="Normal", atraso=0.0):
    return {
        "trecho":        trecho,
        "rodovia":       "BR-101",
        "sentido":       "Sul",
        "status":        status,
        "ocorrencia":    "Teste",
        "atraso_min":    atraso,
        "confianca_pct": 80,
        "conflito_fontes": False,
    }


# ── 1. salvar_ciclo persiste ciclo e snapshots ────────────────────────────────

def test_salvar_ciclo_persiste(repo):
    dados = [_dado("BR-101", "Normal"), _dado("BR-116", "Intenso", 30)]
    ciclo_id = repo.salvar_ciclo(dados, ["HERE", "Google Maps"])
    assert ciclo_id > 0
    assert repo.contar_ciclos() == 1


# ── 2. salvar_ciclo retorna ciclo_id crescente ────────────────────────────────

def test_salvar_ciclo_id_crescente(repo):
    id1 = repo.salvar_ciclo([_dado()], [])
    id2 = repo.salvar_ciclo([_dado()], [])
    assert id2 > id1


# ── 3. ignora dados sem trecho ────────────────────────────────────────────────

def test_salvar_ciclo_ignora_sem_trecho(repo):
    dados = [{"status": "Normal"}, _dado("BR-381")]  # primeiro sem trecho
    repo.salvar_ciclo(dados, [])
    hist = repo.buscar_historico_trecho("BR-381", 24)
    assert len(hist) == 1


# ── 4. lista vazia retorna 0 ──────────────────────────────────────────────────

def test_salvar_ciclo_lista_vazia(repo):
    ciclo_id = repo.salvar_ciclo([], [])
    assert ciclo_id == 0
    assert repo.contar_ciclos() == 0


# ── 5. buscar_historico_trecho retorna pontos no período ──────────────────────

def test_historico_trecho_basico(repo):
    repo.salvar_ciclo([_dado("BR-101", "Intenso", 20)], [])
    hist = repo.buscar_historico_trecho("BR-101", 24)
    assert len(hist) == 1
    assert hist[0]["status"] == "Intenso"
    assert hist[0]["atraso_min"] == 20.0


# ── 6. respeita filtro de horas ────────────────────────────────────────────────

def test_historico_trecho_filtro_horas(repo):
    """Registros fora do período de horas não devem aparecer."""
    from sqlalchemy import update
    from storage.models import snapshots_rotas

    repo.salvar_ciclo([_dado("BR-381", "Parado", 60)], [])

    # Força ts_iso para 48 horas atrás
    passado = (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds")
    with repo._engine.begin() as conn:
        conn.execute(
            update(snapshots_rotas)
            .where(snapshots_rotas.c.trecho == "BR-381")
            .values(ts_iso=passado)
        )

    # Busca nas últimas 24h — não deve encontrar
    hist_24h = repo.buscar_historico_trecho("BR-381", 24)
    assert len(hist_24h) == 0

    # Busca nas últimas 72h — deve encontrar
    hist_72h = repo.buscar_historico_trecho("BR-381", 72)
    assert len(hist_72h) == 1


# ── 7. trecho inexistente retorna [] ─────────────────────────────────────────

def test_historico_trecho_inexistente(repo):
    hist = repo.buscar_historico_trecho("TRECHO_QUE_NAO_EXISTE", 24)
    assert hist == []


# ── 8. buscar_tendencias_hora agrega por hora ─────────────────────────────────

def test_tendencias_agrega_por_hora(repo):
    repo.salvar_ciclo([_dado("BR-116", "Normal",   0)], [])
    repo.salvar_ciclo([_dado("BR-116", "Moderado", 8)], [])
    repo.salvar_ciclo([_dado("BR-116", "Intenso", 25)], [])

    tend = repo.buscar_tendencias_hora("BR-116", 24)
    assert len(tend) >= 1  # tudo na mesma hora → 1 bucket

    bucket = tend[0]
    assert "hora" in bucket
    assert "status_mais_frequente" in bucket
    assert "atraso_medio" in bucket
    assert bucket["n_pontos"] == 3


# ── 9. status mais frequente é calculado corretamente ────────────────────────

def test_tendencias_status_mais_frequente(repo):
    # 2 × Normal, 1 × Intenso → Normal deve vencer
    repo.salvar_ciclo([_dado("BR-040", "Normal")],  [])
    repo.salvar_ciclo([_dado("BR-040", "Normal")],  [])
    repo.salvar_ciclo([_dado("BR-040", "Intenso")], [])

    tend = repo.buscar_tendencias_hora("BR-040", 24)
    assert tend[0]["status_mais_frequente"] == "Normal"


# ── 10. buscar_historico_csv cabeçalho correto ───────────────────────────────

def test_csv_cabecalho(repo):
    repo.salvar_ciclo([_dado()], [])
    csv_str = repo.buscar_historico_csv(24)
    reader  = csv.DictReader(io.StringIO(csv_str))
    cabecalho = reader.fieldnames
    assert "ts_iso"         in cabecalho
    assert "trecho"         in cabecalho
    assert "status"         in cabecalho
    assert "atraso_min"     in cabecalho
    assert "confianca_pct"  in cabecalho


# ── 11. buscar_historico_csv conteúdo correto ────────────────────────────────

def test_csv_conteudo(repo):
    repo.salvar_ciclo([_dado("SP-310 SP-RCL", "Parado", 45)], [])
    csv_str = repo.buscar_historico_csv(24)
    reader  = list(csv.DictReader(io.StringIO(csv_str)))
    assert len(reader) == 1
    assert reader[0]["trecho"] == "SP-310 SP-RCL"
    assert reader[0]["status"] == "Parado"
    assert float(reader[0]["atraso_min"]) == 45.0


# ── 12. purgar_antigos remove via CASCADE ────────────────────────────────────

def test_purgar_remove_antigos(repo):
    from sqlalchemy import update
    from storage.models import ciclos as ciclos_t, snapshots_rotas

    repo.salvar_ciclo([_dado("BR-153", "Normal")], [])
    assert repo.contar_ciclos() == 1

    # Força ts_iso do ciclo para 100 dias atrás
    passado = (datetime.now() - timedelta(days=100)).isoformat(timespec="seconds")
    with repo._engine.begin() as conn:
        conn.execute(update(ciclos_t).values(ts_iso=passado))
        conn.execute(update(snapshots_rotas).values(ts_iso=passado))

    removidos = repo.purgar_antigos(retencao_dias=90)
    assert removidos == 1
    assert repo.contar_ciclos() == 0
    # Snapshots devem ter sido removidos por CASCADE
    hist = repo.buscar_historico_trecho("BR-153", 9999)
    assert len(hist) == 0


# ── 13. purgar preserva ciclos recentes ──────────────────────────────────────

def test_purgar_preserva_recentes(repo):
    repo.salvar_ciclo([_dado()], [])  # ciclo recente
    removidos = repo.purgar_antigos(retencao_dias=90)
    assert removidos == 0
    assert repo.contar_ciclos() == 1


# ── 14. contar_ciclos ────────────────────────────────────────────────────────

def test_contar_ciclos(repo):
    assert repo.contar_ciclos() == 0
    repo.salvar_ciclo([_dado()], [])
    repo.salvar_ciclo([_dado()], [])
    assert repo.contar_ciclos() == 2


# ── 15. ultimo_ciclo_ts retorna mais recente ─────────────────────────────────

def test_ultimo_ciclo_ts(repo):
    assert repo.ultimo_ciclo_ts() is None
    repo.salvar_ciclo([_dado()], [])
    ts = repo.ultimo_ciclo_ts()
    assert ts is not None
    # Deve ser um ISO válido
    datetime.fromisoformat(ts)


# ── 16. listar_trechos retorna distintos ordenados ──────────────────────────

def test_listar_trechos(repo):
    repo.salvar_ciclo([
        _dado("SP-021 SP-MG"),
        _dado("BR-381 BH-SP"),
        _dado("SP-021 SP-MG"),  # duplicado → deve aparecer uma vez
    ], [])
    trechos = repo.listar_trechos()
    assert trechos == sorted(set(trechos))  # ordenados
    assert len(trechos) == 2
    assert "SP-021 SP-MG" in trechos


# ── 17. múltiplos ciclos são preservados no histórico ──────────────────────

def test_multiplos_ciclos_historico(repo):
    for i in range(5):
        repo.salvar_ciclo([_dado("BR-116", "Normal", float(i))], [])

    hist = repo.buscar_historico_trecho("BR-116", 24)
    assert len(hist) == 5
    atrasos = [h["atraso_min"] for h in hist]
    assert sorted(atrasos) == [0.0, 1.0, 2.0, 3.0, 4.0]


# ── 18. thread safety básico (arquivo temporário) ────────────────────────────

def test_thread_safety(tmp_path):
    """10 threads escrevem concorrentemente — banco não deve corromper.

    Usa arquivo temporário pois SQLite :memory: cria banco separado por conexão.
    """
    engine = get_engine(tmp_path / "test_threads.db", wal_mode=True)
    repo_file = RotaRepository(engine)

    errors = []

    def worker(i):
        try:
            repo_file.salvar_ciclo([_dado(f"BR-{100+i}", "Normal")], [])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Erros de concorrência: {errors[:2]}"
    assert repo_file.contar_ciclos() == 10
