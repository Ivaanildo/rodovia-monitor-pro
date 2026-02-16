"""
Testes unitários do main (config e utilidades).
"""
import pytest
import sys
import os
import tempfile
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestCarregarConfig:
    """Testes para carregar_config."""

    def test_config_nao_encontrado(self):
        with pytest.raises(FileNotFoundError):
            main.carregar_config("arquivo_inexistente.yaml")

    def test_config_minimo(self):
        with tempfile.NamedTemporaryFile(
            suffix=".yaml",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as f:
            yaml.dump({"trechos": []}, f, allow_unicode=True)
            path = f.name
        try:
            config = main.carregar_config(path)
            assert "trechos" in config
        finally:
            os.unlink(path)


class TestResolverCaminho:
    """Testes para _resolver_caminho_relativo."""

    def test_caminho_absoluto(self):
        path_config = os.path.join("a", "config.yaml")
        path_rotas = os.path.abspath(os.path.join("b", "rotas.yaml"))
        r = main._resolver_caminho_relativo(path_config, path_rotas)
        assert r == path_rotas

    def test_caminho_relativo_relativo(self):
        r = main._resolver_caminho_relativo("/a/b/config.yaml", "rotas.yaml")
        assert os.path.isabs(r) or "rotas" in r


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
