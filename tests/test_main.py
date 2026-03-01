"""
Testes unitários do main (config e utilidades).
"""
import json
import pytest
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestCarregarConfig:
    """Testes para carregar_config."""

    def test_config_nao_encontrado(self):
        with pytest.raises(FileNotFoundError):
            main.carregar_config("arquivo_inexistente.json")

    def test_config_minimo(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as f:
            json.dump({"trechos": []}, f, ensure_ascii=False)
            path = f.name
        try:
            config = main.carregar_config(path)
            assert "trechos" in config
        finally:
            os.unlink(path)


class TestResolverCaminho:
    """Testes para _resolver_caminho_relativo."""

    def test_caminho_absoluto(self):
        path_config = os.path.join("a", "config.json")
        path_rotas = os.path.abspath(os.path.join("b", "rotas.json"))
        r = main._resolver_caminho_relativo(path_config, path_rotas)
        assert r == path_rotas

    def test_caminho_relativo_relativo(self):
        r = main._resolver_caminho_relativo("/a/b/config.json", "rotas.json")
        assert os.path.isabs(r) or "rotas" in r

    def test_caminho_relativo_sem_exigir_existencia(self):
        r = main._resolver_caminho_relativo(
            os.path.join("c:", "app", "config.json"),
            "./relatorios",
            exigir_existencia=False,
        )
        assert r.endswith(os.path.join("app", "relatorios"))


class TestObterApiKey:
    """Testes para _obter_api_key."""

    def test_key_ausente_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        config = {"google_maps": {}}
        key = main._obter_api_key(config, "google_maps")
        assert key == ""

    def test_key_placeholder_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "SUA_CHAVE")
        config = {"google_maps": {}}
        key = main._obter_api_key(config, "google_maps")
        assert key == ""

    def test_key_definida_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "abc123")
        config = {"google_maps": {}}
        key = main._obter_api_key(config, "google_maps")
        assert key == "abc123"


class TestApiDisponivel:
    """Testes para _api_disponivel."""

    def test_api_desabilitada(self, monkeypatch):
        monkeypatch.setenv("HERE_API_KEY", "abc")
        config = {"here": {"enabled": False}}
        assert main._api_disponivel(config, "here") is False

    def test_api_sem_key(self, monkeypatch):
        monkeypatch.delenv("HERE_API_KEY", raising=False)
        config = {"here": {"enabled": True}}
        assert main._api_disponivel(config, "here") is False

    def test_api_ok(self, monkeypatch):
        monkeypatch.setenv("HERE_API_KEY", "abc123")
        config = {"here": {"enabled": True}}
        assert main._api_disponivel(config, "here") is True


class TestCarregarEnvArquivo:
    """Testes para _carregar_env_arquivo."""

    def test_carrega_env_de_arquivo(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("GOOGLE_MAPS_API_KEY=abc123\n", encoding="utf-8")

        main._carregar_env_arquivo(env_file)

        assert os.getenv("GOOGLE_MAPS_API_KEY") == "abc123"

    def test_nao_sobrescreve_env_existente(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERE_API_KEY", "valor_atual")
        env_file = tmp_path / ".env"
        env_file.write_text("HERE_API_KEY=valor_novo\n", encoding="utf-8")

        main._carregar_env_arquivo(env_file)

        assert os.getenv("HERE_API_KEY") == "valor_atual"

    def test_carrega_primeira_linha_com_bom_utf8(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_bytes("\ufeffGOOGLE_MAPS_API_KEY=abc123\n".encode("utf-8"))

        main._carregar_env_arquivo(env_file)

        assert os.getenv("GOOGLE_MAPS_API_KEY") == "abc123"

    def test_ignora_chave_invalida_e_carrega_valida(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HERE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GOOGLE-MAPS_API_KEY=invalida\nHERE_API_KEY=ok123\n",
            encoding="utf-8",
        )

        main._carregar_env_arquivo(env_file)

        assert os.getenv("GOOGLE_MAPS_API_KEY") is None
        assert os.getenv("HERE_API_KEY") == "ok123"


def test_executar_coleta_aplica_data_advisor_tambem_no_modo_full(monkeypatch):
    called = {"advisor": 0}

    class FakeAdvisor:
        def enriquecer_dados(self, dados, here_dados, gmaps_resultados, intervalo_polling_min=30, **kwargs):
            called["advisor"] += 1
            dados[0]["confianca_pct"] = 90
            dados[0]["confianca"] = "Alta"
            return dados

    monkeypatch.setattr(main, "DataAdvisor", FakeAdvisor)
    monkeypatch.setattr(main, "_api_disponivel", lambda _cfg, nome: nome == "google_maps")
    monkeypatch.setattr(main, "_obter_api_key", lambda *_args, **_kwargs: "key")
    monkeypatch.setattr(main, "_obter_config_google_maps", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        main,
        "_coletar_fonte",
        lambda nome_fonte, *_args: [{"trecho": "Rota A", "status": "Normal"}]
        if nome_fonte == "Google Maps"
        else {"incidentes": {}, "fluxo": {}},
    )
    monkeypatch.setattr(
        main,
        "correlacionar_todos",
        lambda **_kwargs: [{"trecho": "Rota A", "status": "Normal", "ocorrencia": ""}],
    )
    monkeypatch.setattr(main, "gerar_relatorio", lambda **_kwargs: "fake.xlsx")

    config = {
        "trechos": [{"nome": "Rota A", "origem": "A", "destino": "B"}],
        "google_maps": {"enabled": True},
        "here": {"enabled": False},
        "relatorio": {"pasta_saida": "./tmp", "prefixo": "teste"},
    }

    caminho = main.executar_coleta(config, modo_mvp=False, intervalo_min=30)
    assert caminho == "fake.xlsx"
    assert called["advisor"] == 1


def test_executar_coleta_resolve_pasta_saida_relativa_ao_config(monkeypatch, tmp_path):
    called = {}

    class FakeAdvisor:
        def enriquecer_dados(self, dados, here_dados, gmaps_resultados, intervalo_polling_min=30, **kwargs):
            return dados

    monkeypatch.setattr(main, "DataAdvisor", FakeAdvisor)
    monkeypatch.setattr(main, "_api_disponivel", lambda _cfg, nome: nome == "google_maps")
    monkeypatch.setattr(main, "_obter_api_key", lambda *_args, **_kwargs: "key")
    monkeypatch.setattr(main, "_obter_config_google_maps", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        main,
        "_coletar_fonte",
        lambda nome_fonte, *_args: [{"trecho": "Rota A", "status": "Normal"}]
        if nome_fonte == "Google Maps"
        else {"incidentes": {}, "fluxo": {}},
    )
    monkeypatch.setattr(
        main,
        "correlacionar_todos",
        lambda **_kwargs: [{"trecho": "Rota A", "status": "Normal", "ocorrencia": ""}],
    )

    def fake_gerar_relatorio(**kwargs):
        called["pasta_saida"] = kwargs["pasta_saida"]
        return "fake.xlsx"

    monkeypatch.setattr(main, "gerar_relatorio", fake_gerar_relatorio)

    config_path = tmp_path / "config_mvp.json"
    config = {
        "__config_path": str(config_path),
        "trechos": [{"nome": "Rota A", "origem": "A", "destino": "B"}],
        "google_maps": {"enabled": True},
        "here": {"enabled": False},
        "relatorio": {"pasta_saida": "./relatorios", "prefixo": "teste"},
    }

    caminho = main.executar_coleta(config, modo_mvp=False, intervalo_min=30)
    assert caminho == "fake.xlsx"
    assert called["pasta_saida"] == str(tmp_path / "relatorios")
