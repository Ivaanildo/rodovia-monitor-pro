from web import observability


def test_env_float_accepts_valid_sample_rate(monkeypatch):
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    assert observability.env_float("SENTRY_TRACES_SAMPLE_RATE") == 0.25


def test_env_float_rejects_invalid_sample_rate(monkeypatch):
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1.5")

    assert observability.env_float("SENTRY_TRACES_SAMPLE_RATE") is None


def test_validate_sentry_env_requires_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "sentry_sdk", object())

    valid, issues, details = observability.validate_sentry_env()

    assert valid is False
    assert "SENTRY_DSN is not set" in issues
    assert details["dsn_present"] is False


def test_validate_sentry_env_rejects_invalid_sample_rate(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "abc")
    monkeypatch.setattr(observability, "sentry_sdk", object())

    valid, issues, _details = observability.validate_sentry_env()

    assert valid is False
    assert "SENTRY_TRACES_SAMPLE_RATE must be a number between 0 and 1" in issues


def test_init_sentry_uses_env_configuration(monkeypatch):
    captured = {}

    class FakeSentry:
        def init(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.4")
    monkeypatch.setenv("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "0.2")
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "true")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "preview")
    monkeypatch.setenv("SENTRY_RELEASE", "abc123")
    monkeypatch.setenv("VERCEL_URL", "example-app.vercel.app")
    monkeypatch.setattr(observability, "sentry_sdk", FakeSentry())

    assert observability.init_sentry_from_env() is True

    assert captured["dsn"] == "https://public@example.ingest.sentry.io/1"
    assert captured["traces_sample_rate"] == 0.4
    assert captured["profile_session_sample_rate"] == 0.2
    assert captured["send_default_pii"] is True
    assert captured["environment"] == "preview"
    assert captured["release"] == "abc123"
    assert captured["server_name"] == "example-app.vercel.app"


def test_init_sentry_returns_false_for_invalid_sample_rate(monkeypatch):
    class FakeSentry:
        def init(self, **_kwargs):
            raise AssertionError("init should not be called with invalid config")

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "abc")
    monkeypatch.setattr(observability, "sentry_sdk", FakeSentry())

    assert observability.init_sentry_from_env() is False
