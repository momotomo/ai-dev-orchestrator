"""Tests for github_copilot_provider_real.py."""
from __future__ import annotations

import io
import json
import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import github_copilot_provider_real  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------


class ResolveModelTests(unittest.TestCase):
    def test_known_model_sonnet46_maps_to_gpt4o_mini(self) -> None:
        self.assertEqual(github_copilot_provider_real.resolve_model("sonnet-4.6"), "gpt-4o-mini")

    def test_known_model_gpt4o_stays_gpt4o(self) -> None:
        self.assertEqual(github_copilot_provider_real.resolve_model("gpt-4o"), "gpt-4o")

    def test_known_model_gpt4o_mini(self) -> None:
        self.assertEqual(github_copilot_provider_real.resolve_model("gpt-4o-mini"), "gpt-4o-mini")

    def test_empty_model_returns_default(self) -> None:
        self.assertEqual(
            github_copilot_provider_real.resolve_model(""),
            github_copilot_provider_real.DEFAULT_MODEL,
        )

    def test_unknown_model_returned_as_is(self) -> None:
        """Unknown model names are passed through — the API will return a clear error."""
        self.assertEqual(github_copilot_provider_real.resolve_model("my-custom-model"), "my-custom-model")

    def test_whitespace_stripped(self) -> None:
        self.assertEqual(github_copilot_provider_real.resolve_model("  gpt-4o  "), "gpt-4o")


# ---------------------------------------------------------------------------
# get_gh_token
# ---------------------------------------------------------------------------


class GetGhTokenTests(unittest.TestCase):
    def test_returns_token_on_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "gho_test_token_abc\n"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            token = github_copilot_provider_real.get_gh_token()
        self.assertEqual(token, "gho_test_token_abc")

    def test_raises_when_gh_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.get_gh_token()
        self.assertIn("gh CLI", str(ctx.exception))

    def test_raises_when_returncode_nonzero(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "not logged in"
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.get_gh_token()
        self.assertIn("gh auth token が失敗", str(ctx.exception))

    def test_raises_when_token_empty(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.get_gh_token()
        self.assertIn("空のトークン", str(ctx.exception))


# ---------------------------------------------------------------------------
# call_github_models_api
# ---------------------------------------------------------------------------


def _make_mock_response(content: str) -> MagicMock:
    """Build a mock urllib response that returns a minimal completions JSON."""
    body = json.dumps(
        {
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        }
    ).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class CallGithubModelsApiTests(unittest.TestCase):
    def test_returns_response_content(self) -> None:
        mock_resp = _make_mock_response("Hello from AI")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = github_copilot_provider_real.call_github_models_api(
                "hi", "gpt-4o-mini", "token-abc"
            )
        self.assertEqual(result, "Hello from AI")

    def test_raises_on_http_error(self) -> None:
        import urllib.error
        exc = urllib.error.HTTPError(
            url="https://example.com",
            code=401,
            msg="Unauthorized",
            hdrs={},  # type: ignore
            fp=io.BytesIO(b'{"error":"unauthorized"}'),
        )
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.call_github_models_api(
                    "hi", "gpt-4o-mini", "bad-token"
                )
        self.assertIn("401", str(ctx.exception))

    def test_raises_on_url_error(self) -> None:
        import urllib.error
        exc = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.call_github_models_api(
                    "hi", "gpt-4o-mini", "token"
                )
        self.assertIn("接続エラー", str(ctx.exception))

    def test_raises_when_no_choices(self) -> None:
        body = json.dumps({"choices": []}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.call_github_models_api(
                    "hi", "gpt-4o-mini", "token"
                )
        self.assertIn("choices", str(ctx.exception))

    def test_raises_when_content_empty(self) -> None:
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": ""}}]}
        ).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                github_copilot_provider_real.call_github_models_api(
                    "hi", "gpt-4o-mini", "token"
                )
        self.assertIn("空の content", str(ctx.exception))


# ---------------------------------------------------------------------------
# run (integration of the above)
# ---------------------------------------------------------------------------


class RunTests(unittest.TestCase):
    def _make_gh_mock(self, token: str = "gho_fake") -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        m.stdout = f"{token}\n"
        m.stderr = ""
        return m

    def test_empty_prompt_exits_nonzero(self) -> None:
        with patch("sys.stdin", io.StringIO("   \n")):
            with patch("sys.stderr", io.StringIO()):
                rc = github_copilot_provider_real.run([])
        self.assertNotEqual(rc, 0)

    def test_gh_not_found_exits_nonzero(self) -> None:
        with patch("sys.stdin", io.StringIO("some prompt")):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                with patch("sys.stderr", io.StringIO()):
                    rc = github_copilot_provider_real.run([])
        self.assertNotEqual(rc, 0)

    def test_api_error_exits_nonzero(self) -> None:
        import urllib.error
        exc = urllib.error.URLError("network down")
        with patch("sys.stdin", io.StringIO("some prompt")):
            with patch("subprocess.run", return_value=self._make_gh_mock()):
                with patch("urllib.request.urlopen", side_effect=exc):
                    with patch("sys.stderr", io.StringIO()):
                        rc = github_copilot_provider_real.run([])
        self.assertNotEqual(rc, 0)

    def test_success_prints_response_and_exits_zero(self) -> None:
        mock_resp = _make_mock_response("The answer is 42.")
        captured_stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("What is 6 times 7?")):
            with patch("subprocess.run", return_value=self._make_gh_mock()):
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    with patch("sys.stdout", captured_stdout):
                        rc = github_copilot_provider_real.run(["--model", "gpt-4o-mini"])
        self.assertEqual(rc, 0)
        self.assertIn("The answer is 42.", captured_stdout.getvalue())

    def test_model_forwarded_correctly(self) -> None:
        """--model sonnet-4.6 is mapped to gpt-4o-mini and forwarded to the API."""
        captured_request: list[urllib.request.Request] = []

        def fake_urlopen(req: urllib.request.Request, timeout: int) -> MagicMock:
            captured_request.append(req)
            return _make_mock_response("ok")

        with patch("sys.stdin", io.StringIO("test prompt")):
            with patch("subprocess.run", return_value=self._make_gh_mock()):
                with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    with patch("sys.stdout", io.StringIO()):
                        github_copilot_provider_real.run(["--model", "sonnet-4.6"])

        self.assertEqual(len(captured_request), 1)
        body = json.loads(captured_request[0].data.decode("utf-8"))
        self.assertEqual(body["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
