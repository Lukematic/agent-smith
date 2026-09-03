"""Privacy regression: session_log.append() used to persist whatever text it
was given verbatim, including secret-shaped strings. The UserPromptSubmit hook
feeds every prompt through append(), so a pasted credential landed on disk in
plaintext under .smith state. append() must now pass text through a redaction
denylist before persisting, while ordinary technical content is unchanged.

All secrets below are synthetic, constructed to match the shape only.
"""

from __future__ import annotations

from pathlib import Path

from smith import session_log

# Synthetic, non-functional examples of each denylisted secret shape.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.abc123DEF456ghi789"
GITHUB_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
PRIVATE_KEY_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA0synthetic0not0a0real0key0AAAA\n"
    "-----END RSA PRIVATE KEY-----"
)


def _persisted(state_root: Path, session_id: str) -> str:
    return session_log.log_path(state_root, session_id).read_text(encoding="utf-8")


class TestSecretsAreNotPersistedVerbatim:
    def test_aws_access_key_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", f"my key is {AWS_KEY} please use it")
        raw = _persisted(tmp_path, "s1")
        assert AWS_KEY not in raw
        assert "[REDACTED:aws-access-key]" in raw

    def test_bearer_token_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", f"use header Authorization: {BEARER}")
        raw = _persisted(tmp_path, "s1")
        assert BEARER.split()[1] not in raw
        assert "[REDACTED:bearer-token]" in raw

    def test_github_token_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", f"token: {GITHUB_TOKEN}")
        raw = _persisted(tmp_path, "s1")
        assert GITHUB_TOKEN not in raw
        assert "[REDACTED:github-token]" in raw

    def test_password_assignment_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", "connect with password=hunter2secret!")
        raw = _persisted(tmp_path, "s1")
        assert "hunter2secret!" not in raw
        assert "[REDACTED:password]" in raw

    def test_api_key_assignment_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", 'set api_key="sk-not-real-12345abcde"')
        raw = _persisted(tmp_path, "s1")
        assert "sk-not-real-12345abcde" not in raw
        assert "[REDACTED:api-key]" in raw

    def test_private_key_block_is_redacted_on_disk(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", f"here is the pem\n{PRIVATE_KEY_BLOCK}")
        raw = _persisted(tmp_path, "s1")
        assert "MIIEpAIBAAKCAQEA" not in raw
        assert "[REDACTED:private-key]" in raw

    def test_returned_ask_is_also_redacted_not_just_the_file(self, tmp_path: Path) -> None:
        ask = session_log.append(tmp_path, "s1", "user_turn", f"key {AWS_KEY}")
        assert AWS_KEY not in ask.text

    def test_normalized_text_does_not_leak_the_secret_either(self, tmp_path: Path) -> None:
        ask = session_log.append(tmp_path, "s1", "user_turn", f"key {AWS_KEY}")
        assert AWS_KEY.lower() not in ask.text_norm


class TestOrdinaryContentIsUnchanged:
    def test_plain_instruction_passes_through_verbatim(self, tmp_path: Path) -> None:
        text = "never rename the config keys in this project"
        ask = session_log.append(tmp_path, "s1", "user_turn", text)
        assert ask.text == text

    def test_technical_content_with_identifiers_is_untouched(self, tmp_path: Path) -> None:
        text = (
            "the function _next_turn(path) reads session/{session_id}.jsonl "
            "and AKIAB is a fine variable prefix but not a full key"
        )
        ask = session_log.append(tmp_path, "s1", "user_turn", text)
        assert ask.text == text

    def test_talking_about_passwords_without_a_value_is_untouched(self, tmp_path: Path) -> None:
        text = "should the password field be hashed with bcrypt or argon2?"
        ask = session_log.append(tmp_path, "s1", "user_turn", text)
        assert ask.text == text

    def test_duplicate_detection_still_works_on_redacted_entries(self, tmp_path: Path) -> None:
        session_log.append(tmp_path, "s1", "user_turn", f"deploy with key {AWS_KEY} to prod")
        found = session_log.find_duplicate_question(
            tmp_path, "s1", "deploy with key [REDACTED:aws-access-key] to prod"
        )
        assert found is not None
