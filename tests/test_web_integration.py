"""
Testes de integração — Fase 3.2: DataStore + loop de coleta + API web.

Cobre:
  - DataStore: estado, thread-safety, contagem de ciclos
  - DataStore.get_resumo(): categorização de trechos críticos
  - Bridge sync→async: notificação SSE via call_soon_threadsafe
  - executar_coleta() → _web_store.update() quando --web ativo
  - executar_coleta() sem _web_store (modo sem dashboard)
  - Endpoints FastAPI: /api/status, /api/resumo após update
"""
import asyncio
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from web.state import DataStore


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------

DADOS_FAKE = [
    {"trecho": "BR-101 Curitiba/SC", "rodovia": "BR-101", "status": "Normal",
     "ocorrencia": "", "atraso_min": 2, "confianca_pct": 85},
    {"trecho": "BR-116 SP (Regis Bittencourt)", "rodovia": "BR-116", "status": "Intenso",
     "ocorrencia": "Colisão", "atraso_min": 32, "confianca_pct": 78},
    {"trecho": "BR-381 SP (São Paulo → BH)", "rodovia": "BR-381", "status": "Parado",
     "ocorrencia": "Interdição", "atraso_min": 60, "confianca_pct": 91},
    {"trecho": "PE-060 Santo Agostinho", "rodovia": "PE-060", "status": "Moderado",
     "ocorrencia": "", "atraso_min": 12, "confianca_pct": 55},
    {"trecho": "BR-040 Duque de Caxias", "rodovia": "BR-040", "status": "Sem dados",
     "ocorrencia": "", "atraso_min": None, "confianca_pct": 0},
]


def _config_fake(monkeypatch):
    """Configura monkeypatches mínimos para executar_coleta() sem chamadas reais."""
    class FakeAdvisor:
        def enriquecer_dados(self, dados, here_dados, gmaps_resultados, intervalo_polling_min=30, **kwargs):
            for d in dados:
                d.setdefault("confianca_pct", 80)
                d.setdefault("confianca", "Alta")
            return dados

    monkeypatch.setattr(main, "DataAdvisor", FakeAdvisor)
    monkeypatch.setattr(main, "_api_disponivel", lambda _cfg, nome: nome == "google_maps")
    monkeypatch.setattr(main, "_obter_api_key", lambda *_a, **_kw: "key_fake")
    monkeypatch.setattr(main, "_obter_config_google_maps", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        main, "_coletar_fonte",
        lambda nome, *_a: (
            [{"trecho": "BR-101", "status": "Normal"}]
            if nome == "Google Maps"
            else {"incidentes": {}, "fluxo": {}}
        ),
    )
    monkeypatch.setattr(
        main, "correlacionar_todos",
        lambda **_kw: [{"trecho": "BR-101", "status": "Normal", "ocorrencia": ""}],
    )
    monkeypatch.setattr(main, "gerar_relatorio", lambda **_kw: "fake.xlsx")

    return {
        "trechos": [{"nome": "BR-101", "origem": "A", "destino": "B"}],
        "google_maps": {"enabled": True},
        "here": {"enabled": False},
        "relatorio": {"pasta_saida": "./tmp", "prefixo": "teste"},
    }


# ---------------------------------------------------------------------------
# DataStore — estado básico
# ---------------------------------------------------------------------------

class TestDataStoreEstado:

    def test_estado_inicial_vazio(self):
        ds = DataStore()
        snap = ds.get()
        assert snap["total_trechos"] == 0
        assert snap["dados"] == []
        assert snap["ultimo_ciclo"] is None
        assert snap["fontes_ativas"] == []
        assert snap["total_ciclos"] == 0

    def test_update_persiste_dados(self):
        ds = DataStore()
        ds.update(DADOS_FAKE, fontes_ativas=["HERE", "Google Maps"])
        snap = ds.get()
        assert snap["total_trechos"] == len(DADOS_FAKE)
        assert snap["fontes_ativas"] == ["HERE", "Google Maps"]
        assert snap["ultimo_ciclo"] is not None
        assert snap["dados"] == DADOS_FAKE

    def test_update_incrementa_ciclos(self):
        ds = DataStore()
        ds.update(DADOS_FAKE)
        ds.update(DADOS_FAKE)
        assert ds.get()["total_ciclos"] == 2

    def test_update_sem_fontes_ativas_preserva_anterior(self):
        ds = DataStore()
        ds.update(DADOS_FAKE, fontes_ativas=["HERE"])
        # Segunda chamada sem fontes_ativas=None não sobrescreve
        ds.update([], fontes_ativas=None)
        assert ds.get()["fontes_ativas"] == ["HERE"]

    def test_update_com_lista_vazia_de_fontes(self):
        ds = DataStore()
        ds.update(DADOS_FAKE, fontes_ativas=[])
        assert ds.get()["fontes_ativas"] == []

    def test_dados_sao_copia_independente(self):
        """Mutações externas na lista não devem afetar o estado interno."""
        ds = DataStore()
        lista = [{"trecho": "X", "status": "Normal"}]
        ds.update(lista)
        lista.append({"trecho": "Y", "status": "Intenso"})
        assert ds.get()["total_trechos"] == 1


# ---------------------------------------------------------------------------
# DataStore — get_resumo
# ---------------------------------------------------------------------------

class TestDataStoreResumo:

    def setup_method(self):
        self.ds = DataStore()
        self.ds.update(DADOS_FAKE, fontes_ativas=["HERE", "Google"])

    def test_contagem_por_status(self):
        resumo = self.ds.get_resumo()
        por_status = resumo["por_status"]
        assert por_status.get("Normal") == 1
        assert por_status.get("Intenso") == 1
        assert por_status.get("Parado") == 1
        assert por_status.get("Moderado") == 1
        assert por_status.get("Sem dados") == 1

    def test_criticos_incluem_intenso_e_parado(self):
        criticos = self.ds.get_resumo()["criticos"]
        status_criticos = {c["status"] for c in criticos}
        assert status_criticos == {"Intenso", "Parado"}

    def test_criticos_nao_incluem_normal(self):
        criticos = self.ds.get_resumo()["criticos"]
        assert all(c["status"] in ("Intenso", "Parado") for c in criticos)

    def test_critico_contem_campos_necessarios(self):
        criticos = self.ds.get_resumo()["criticos"]
        for c in criticos:
            assert "trecho" in c
            assert "status" in c
            assert "atraso_min" in c
            assert "confianca_pct" in c


# ---------------------------------------------------------------------------
# DataStore — thread-safety
# ---------------------------------------------------------------------------

class TestDataStoreThreadSafety:

    def test_update_concorrente_nao_corrompe_estado(self):
        """10 threads atualizando simultaneamente — estado final consistente."""
        ds = DataStore()
        erros = []

        def _worker(i):
            try:
                ds.update(
                    [{"trecho": f"Rota-{i}", "status": "Normal"}],
                    fontes_ativas=[f"fonte-{i}"],
                )
            except Exception as e:
                erros.append(e)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not erros, f"Erros em threads: {erros}"
        snap = ds.get()
        assert snap["total_ciclos"] == 10
        assert snap["total_trechos"] == 1  # cada update substitui o anterior

    def test_leitura_concorrente_durante_escrita(self):
        """Leituras e escritas simultâneas não geram exceções."""
        ds = DataStore()
        erros = []
        stop = threading.Event()

        def _writer():
            while not stop.is_set():
                ds.update(DADOS_FAKE, fontes_ativas=["HERE"])

        def _reader():
            while not stop.is_set():
                try:
                    snap = ds.get()
                    assert isinstance(snap["dados"], list)
                except Exception as e:
                    erros.append(e)

        writers = [threading.Thread(target=_writer, daemon=True) for _ in range(3)]
        readers = [threading.Thread(target=_reader, daemon=True) for _ in range(3)]
        for t in writers + readers:
            t.start()

        threading.Event().wait(0.3)  # deixa rodar 300ms
        stop.set()

        for t in writers + readers:
            t.join(timeout=1.0)

        assert not erros, f"Erros durante leitura concorrente: {erros}"


# ---------------------------------------------------------------------------
# DataStore — bridge sync→async (SSE)
# ---------------------------------------------------------------------------

class TestDataStoreSseBridge:

    def test_registrar_e_remover_cliente(self):
        ds = DataStore()
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        ds.registrar_cliente_sse(loop, queue)
        assert ds.clientes_conectados == 1

        ds.remover_cliente_sse(loop, queue)
        assert ds.clientes_conectados == 0
        loop.close()

    def test_update_notifica_cliente_via_queue(self):
        """Verifica que update() coloca evento na queue asyncio do cliente SSE."""
        ds = DataStore()
        loop = asyncio.new_event_loop()

        resultado = []
        done = threading.Event()

        def _run_loop():
            asyncio.set_event_loop(loop)

            async def _consume():
                queue: asyncio.Queue = asyncio.Queue()
                ds.registrar_cliente_sse(loop, queue)
                # Aguarda até 2s pelo evento
                try:
                    evento = await asyncio.wait_for(queue.get(), timeout=2.0)
                    resultado.append(evento)
                except asyncio.TimeoutError:
                    resultado.append(None)
                finally:
                    done.set()

            loop.run_until_complete(_consume())

        t = threading.Thread(target=_run_loop, daemon=True)
        t.start()

        # Aguarda o loop asyncio estar rodando (setup da queue)
        import time
        time.sleep(0.05)

        # Update chamado da thread principal (sync)
        ds.update([{"trecho": "BR-101", "status": "Normal"}], fontes_ativas=["HERE"])

        done.wait(timeout=3.0)
        t.join(timeout=1.0)
        loop.close()

        assert resultado, "Nenhum evento recebido"
        assert resultado[0] is not None, "Timeout aguardando evento SSE"
        assert resultado[0]["event"] == "data-update"
        assert "ts" in resultado[0]

    def test_cliente_desconectado_e_removido_automaticamente(self):
        """update() deve limpar clientes cujo loop já foi fechado."""
        ds = DataStore()
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        ds.registrar_cliente_sse(loop, queue)
        assert ds.clientes_conectados == 1

        loop.close()  # simula desconexão

        # update() tenta call_soon_threadsafe em loop fechado → captura exceção → remove
        ds.update(DADOS_FAKE)

        assert ds.clientes_conectados == 0


# ---------------------------------------------------------------------------
# Integração: executar_coleta → _web_store
# ---------------------------------------------------------------------------

class TestExecutarColetaWebIntegracao:

    def test_update_chamado_quando_web_store_configurado(self, monkeypatch):
        """_web_store.update() deve ser chamado após correlação quando ativo."""
        config = _config_fake(monkeypatch)

        chamadas = []

        class FakeStore:
            clientes_conectados = 0

            def update(self, dados, fontes_ativas=None):
                chamadas.append({"dados": dados, "fontes": fontes_ativas})

        monkeypatch.setattr(main, "_web_store", FakeStore())

        caminho = main.executar_coleta(config, modo_mvp=True)

        assert caminho == "fake.xlsx"
        assert len(chamadas) == 1, "update() deve ser chamado exatamente uma vez"
        assert isinstance(chamadas[0]["dados"], list)
        assert chamadas[0]["fontes"] == ["Google Maps"]

    def test_update_nao_chamado_quando_web_store_none(self, monkeypatch):
        """Sem --web, _web_store é None e não deve haver chamadas."""
        config = _config_fake(monkeypatch)

        monkeypatch.setattr(main, "_web_store", None)

        # Não deve lançar exceção nem chamar update
        caminho = main.executar_coleta(config, modo_mvp=True)
        assert caminho == "fake.xlsx"

    def test_update_recebe_fontes_ativas_corretas(self, monkeypatch):
        """fontes_ativas passado ao update deve refletir as APIs habilitadas."""
        config = _config_fake(monkeypatch)

        # Habilita BOTH Google Maps e HERE
        def _api_ambas(_cfg, nome):
            return nome in ("google_maps", "here")

        monkeypatch.setattr(main, "_api_disponivel", _api_ambas)

        chamadas = []

        class FakeStore:
            clientes_conectados = 0

            def update(self, dados, fontes_ativas=None):
                chamadas.append(fontes_ativas)

        monkeypatch.setattr(main, "_web_store", FakeStore())

        main.executar_coleta(config, modo_mvp=True)

        fontes = chamadas[0]
        assert "Google Maps" in fontes
        assert "HERE Traffic" in fontes

    def test_update_nao_chamado_quando_sem_fontes(self, monkeypatch):
        """Se não há fontes ativas, executar_coleta retorna None antes do update."""
        monkeypatch.setattr(main, "_api_disponivel", lambda *_: False)

        chamadas = []

        class FakeStore:
            clientes_conectados = 0

            def update(self, dados, fontes_ativas=None):
                chamadas.append(True)

        monkeypatch.setattr(main, "_web_store", FakeStore())

        config = {
            "trechos": [{"nome": "X", "origem": "A", "destino": "B"}],
            "google_maps": {"enabled": False},
            "here": {"enabled": False},
            "relatorio": {},
        }

        resultado = main.executar_coleta(config)

        assert resultado is None
        assert not chamadas, "update() NÃO deve ser chamado se não há fontes"


# ---------------------------------------------------------------------------
# Endpoints FastAPI após update
# ---------------------------------------------------------------------------

class TestApiEndpointsAposUpdate:

    @pytest.fixture(autouse=True)
    def _reset_store(self):
        """Reseta o store global entre testes."""
        from web.state import store
        store._dados = []
        store._ultimo_ciclo = None
        store._fontes_ativas = []
        store._total_ciclos = 0
        yield

    def test_api_status_reflete_update(self):
        from fastapi.testclient import TestClient
        from web.app import app
        from web.state import store

        store.update(DADOS_FAKE, fontes_ativas=["HERE", "Google"])

        client = TestClient(app)
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["total_trechos"] == len(DADOS_FAKE)
        assert body["fontes_ativas"] == ["HERE", "Google"]
        assert body["ultimo_ciclo"] is not None

    def test_api_resumo_criticos_apos_update(self):
        from fastapi.testclient import TestClient
        from web.app import app
        from web.state import store

        store.update(DADOS_FAKE, fontes_ativas=["HERE"])

        client = TestClient(app)
        r = client.get("/api/resumo")
        assert r.status_code == 200
        body = r.json()
        # Deve ter Intenso (BR-116) e Parado (BR-381) como críticos
        status_criticos = {c["status"] for c in body["criticos"]}
        assert "Intenso" in status_criticos
        assert "Parado" in status_criticos

    def test_health_reflete_ciclos(self):
        from fastapi.testclient import TestClient
        from web.app import app
        from web.state import store

        store.update(DADOS_FAKE)
        store.update(DADOS_FAKE)

        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["total_ciclos"] == 2
        assert body["status"] == "ok"

    def test_api_status_store_inicial_vazio(self):
        """Store sem dados deve retornar snapshot vazio sem erro."""
        from fastapi.testclient import TestClient
        from web.app import app

        client = TestClient(app)
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["total_trechos"] == 0
        assert body["dados"] == []

    def test_api_bloqueia_rotas_privadas_sem_auth_em_producao(self, monkeypatch):
        from fastapi.testclient import TestClient
        from web.app import app

        monkeypatch.delenv("AUTH_SECRET", raising=False)
        monkeypatch.delenv("AUTH_USERS", raising=False)
        monkeypatch.delenv("AUTH_ALLOW_UNCONFIGURED", raising=False)
        monkeypatch.setenv("VERCEL_ENV", "production")

        client = TestClient(app)
        r = client.get("/api/status")
        assert r.status_code == 503
        assert "Autenticacao nao configurada" in r.json()["erro"]

    def test_login_ignora_x_forwarded_for_sem_confianca_explicita(self, monkeypatch):
        from fastapi.testclient import TestClient
        from web.app import app
        from web.auth import hash_password, login_limiter

        login_limiter._attempts.clear()
        monkeypatch.setenv("AUTH_SECRET", "segredo-de-teste")
        monkeypatch.setenv(
            "AUTH_USERS",
            json.dumps(
                [
                    {
                        "username": "admin",
                        "password_hash": hash_password("correta"),
                        "role": "admin",
                    }
                ]
            ),
        )
        monkeypatch.delenv("AUTH_TRUST_X_FORWARDED_FOR", raising=False)
        monkeypatch.delenv("VERCEL_ENV", raising=False)

        client = TestClient(app)
        r = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "errada"},
            headers={"x-forwarded-for": "203.0.113.10"},
        )

        assert r.status_code == 401
        assert "203.0.113.10" not in login_limiter._attempts
        assert len(login_limiter._attempts) == 1

    def test_login_usa_x_forwarded_for_quando_habilitado(self, monkeypatch):
        from fastapi.testclient import TestClient
        from web.app import app
        from web.auth import hash_password, login_limiter

        login_limiter._attempts.clear()
        monkeypatch.setenv("AUTH_SECRET", "segredo-de-teste")
        monkeypatch.setenv(
            "AUTH_USERS",
            json.dumps(
                [
                    {
                        "username": "admin",
                        "password_hash": hash_password("correta"),
                        "role": "admin",
                    }
                ]
            ),
        )
        monkeypatch.setenv("AUTH_TRUST_X_FORWARDED_FOR", "true")
        monkeypatch.delenv("VERCEL_ENV", raising=False)

        client = TestClient(app)
        r = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "errada"},
            headers={"x-forwarded-for": "203.0.113.10"},
        )

        assert r.status_code == 401
        assert "203.0.113.10" in login_limiter._attempts
