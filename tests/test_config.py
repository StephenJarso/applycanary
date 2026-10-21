"""Settings-level tests for LLM enablement, email backend, and warnings."""

from app.config import Settings, in_container

# The provider chain in app.llm.client._build_provider_order accepts xAI,
# Gemini, OpenRouter, Groq, Ollama, Anthropic and Bedrock. `llm_enabled` must
# agree with that chain (minus Ollama, whose default host points at nothing
# until the operator actually runs a local server) or the dashboard keeps
# warning "LLM not configured" even though scoring and tailoring work.

LLM_KEY_NAMES = (
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
)


def test_llm_enabled_false_without_keys() -> None:
    assert not Settings().llm_enabled


def test_llm_enabled_openrouter_key() -> None:
    assert Settings(openrouter_api_key="sk-or-v1-test").llm_enabled


def test_llm_enabled_groq_key() -> None:
    assert Settings(groq_api_key="gsk_test").llm_enabled


def test_llm_enabled_other_providers() -> None:
    assert Settings(xai_api_key="xai-test").llm_enabled
    assert Settings(gemini_api_key="gem-test").llm_enabled
    assert Settings(anthropic_api_key="sk-ant-test").llm_enabled
    assert Settings(aws_access_key_id="AKIATEST").llm_enabled


def test_no_llm_warning_when_openrouter_configured() -> None:
    s = Settings(openrouter_api_key="sk-or-v1-test")
    assert not any(w.startswith("No LLM API key") for w in s.startup_warnings())


def test_no_llm_warning_when_groq_configured() -> None:
    s = Settings(groq_api_key="gsk_test")
    assert not any(w.startswith("No LLM API key") for w in s.startup_warnings())


def test_llm_warning_lists_all_providers() -> None:
    s = Settings()
    llm_warnings = [w for w in s.startup_warnings() if w.startswith("No LLM API key")]
    assert llm_warnings
    for key in LLM_KEY_NAMES:
        assert key in llm_warnings[0]


# ---------------------------------------------------------------------------
# Email backend: Brevo preferred, then Resend, then SMTP legacy fallback.
# ---------------------------------------------------------------------------


def test_email_disabled_without_backend() -> None:
    assert not Settings().email_enabled


def test_email_enabled_with_brevo_key() -> None:
    assert Settings(brevo_api_key="xkeysib-test").email_enabled


def test_email_enabled_with_resend_key() -> None:
    assert Settings(resend_api_key="re_test").email_enabled


def test_email_enabled_with_smtp() -> None:
    assert Settings(smtp_host="smtp.gmail.com", smtp_user="me@gmail.com").email_enabled


def test_email_disabled_smtp_without_user() -> None:
    assert not Settings(smtp_host="smtp.gmail.com").email_enabled


def test_email_warning_when_disabled() -> None:
    s = Settings()
    assert any("No email backend configured" in w for w in s.startup_warnings())


def test_no_email_warning_when_resend_configured() -> None:
    s = Settings(resend_api_key="re_test")
    assert not any("No email backend" in w for w in s.startup_warnings())


def test_no_email_warning_when_brevo_configured() -> None:
    s = Settings(brevo_api_key="xkeysib-test")
    assert not any("No email backend" in w for w in s.startup_warnings())


def test_verification_without_absolute_origin_warns_about_skipped_mail() -> None:
    # _link() skips the send rather than mail a relative URL, so the gate would
    # strand every signup without anyone noticing.
    s = Settings(
        brevo_api_key="xkeysib-test",
        require_email_verification=True,
        public_base_url="",
        frontend_base_url="/",
    )
    assert any("verification emails are skipped" in w for w in s.startup_warnings())


def test_no_link_warning_when_absolute_origin_is_set() -> None:
    s = Settings(
        brevo_api_key="xkeysib-test",
        require_email_verification=True,
        public_base_url="https://apply.example.com",
    )
    assert not any("verification emails are skipped" in w for w in s.startup_warnings())


# ---------------------------------------------------------------------------
# Bind-address and GitHub warnings: noise control for managed platforms.
# ---------------------------------------------------------------------------


def test_render_env_is_detected_as_container(monkeypatch) -> None:
    in_container.cache_clear()
    monkeypatch.setenv("RENDER", "true")
    try:
        assert in_container()
    finally:
        in_container.cache_clear()


def test_host_warning_on_non_container(monkeypatch) -> None:
    monkeypatch.setattr("app.config.in_container", lambda: False)
    s = Settings(host="0.0.0.0", secret_key="x" * 40)
    assert any("HOST is 0.0.0.0" in w for w in s.startup_warnings())


def test_no_host_warning_in_container_with_real_secret(monkeypatch) -> None:
    monkeypatch.setattr("app.config.in_container", lambda: True)
    s = Settings(host="0.0.0.0", secret_key="x" * 40)
    assert not any(w.startswith("HOST is") for w in s.startup_warnings())


def test_github_warning_says_profile_values_still_work() -> None:
    s = Settings(github_username="")
    assert any(
        "GITHUB_USERNAME is unset" in w and "Profiles with their own" in w
        for w in s.startup_warnings()
    )
