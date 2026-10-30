"""Regression canaries for accidental credential or user-file distribution."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.check_distribution import (
    ENTRYPOINT,
    PUBLIC_DOCS,
    SPEC_PATH,
    main,
    scan_distribution,
    scan_repository,
)

SAFE_SPEC = """from pathlib import Path
root = Path(SPECPATH).parent
public_docs = ["README.md", "docs/SECURITY.md", "docs/TWITCH_SETUP.md", "docs/PROFILES.md", "docs/VALIDATION.md"]
analysis = Analysis(
    [str(root / "packaging" / "run_app.py")], pathex=[str(root / "src")],
    binaries=[], hookspath=[], runtime_hooks=[],
    datas=[(str(root / name), "docs") for name in public_docs],
)
"""


class DistributionChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.write("src/twitch_chat_control/__init__.py", '__version__ = "0.1.0"\n')
        self.write(
            "src/twitch_chat_control/twitch.py",
            'def connect(client_id):\n    access_token = ""\n    return client_id\n',
        )
        self.write(ENTRYPOINT, "from twitch_chat_control.app import main\nmain()\n")
        self.write(SPEC_PATH, SAFE_SPEC)
        for name in PUBLIC_DOCS:
            self.write(name, "# Public documentation\nSupply your own authorization in Twitch.\n")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def rules(self, tracked=()):
        return {finding.rule for finding in scan_repository(self.root, tracked_paths=tracked)}

    def test_explicit_application_and_document_allowlist_passes(self):
        self.assertEqual(self.rules(), set())

    def test_untracked_env_and_synthetic_tests_are_outside_build_inputs(self):
        self.write(".env", "TWITCH_ACCESS_TOKEN=synthetic-never-ship\n")
        self.write("tests/test_fake.py", 'access_token = "synthetic-credential-fixture"\n')
        self.assertEqual(self.rules(), set())

    def test_tracked_env_and_private_key_canaries_fail(self):
        self.write(".env", "TWITCH_ACCESS_TOKEN=synthetic-never-ship\n")
        self.write("private.key", "synthetic private key canary")
        self.assertIn("forbidden_tracked_credential_or_user_file", self.rules([".env", "private.key"]))

    def test_private_key_material_in_tracked_pem_fails(self):
        self.write(
            "signing.pem", "-----BEGIN PRIVATE KEY-----\nsynthetic-not-a-key\n-----END PRIVATE KEY-----"
        )
        self.assertIn("private_key_material", self.rules(["signing.pem"]))

    def test_runtime_literal_credential_assignments_and_defaults_fail(self):
        canaries = (
            'access_token = "synthetic-access-value"',
            'DEFAULT_CLIENT_SECRET = "synthetic-client-secret"',
            'settings = {"refresh_token": "synthetic-refresh-value"}',
            'def connect(client_id="syntheticClientId123"): pass',
            'def connect(*, api_key="synthetic-api-value"): pass',
            'obj.client_id_var = StringVar(value="syntheticClientId123")',
            'TwitchClient("syntheticClientId123", "channel")',
            'obj._access, obj._refresh = "synthetic-access-value", ""',
            'obj["password"] = "synthetic-password-value"',
            'client_id = "synthetic" + "ClientId123"',
            'access_token = supplied or "synthetic-access-value"',
            'value = os.getenv("TWITCH_ACCESS_TOKEN", "synthetic-access-value")',
            'value = settings.get("client_id", "syntheticClientId123")',
        )
        for canary in canaries:
            with self.subTest(canary=canary.split("=")[0]):
                self.write("src/twitch_chat_control/twitch.py", canary + "\n")
                self.assertTrue(
                    self.rules() & {"nonempty_credential_or_client_id_literal", "prefilled_credential_field"}
                )

    def test_runtime_received_credentials_and_blank_defaults_pass(self):
        self.write(
            "src/twitch_chat_control/twitch.py",
            """
def connect(client_id):
    access = response.get("access_token")
    token_url = "https://id.twitch.tv/oauth2/token"
    refresh = ""
    headers = {"Authorization": "Bearer " + access, "Client-Id": client_id}
    return headers, token_url, refresh
""",
        )
        self.assertEqual(self.rules(), set())

    def test_recognizable_oauth_literal_is_detected_without_variable_name(self):
        self.write("src/twitch_chat_control/twitch.py", 'value = "oauth:abcdefghijklmnopqrstuvwxyz012345"\n')
        self.assertIn("recognizable_token_literal", self.rules())

    def test_broad_dynamic_and_directory_spec_inputs_fail(self):
        originals = 'datas=[(str(root / name), "docs") for name in public_docs]'
        for replacement in (
            'datas=[(str(root), ".")]',
            'datas=[(".", ".")]',
            'datas=Tree(".")',
            'datas=collect_data_files(".")',
            'datas=[(str(root / "docs"), "docs")]',
            'datas=[(str(root / ".env"), ".")]',
            'datas=[(str(root / ".." / "private.key"), "docs")]',
        ):
            with self.subTest(replacement=replacement):
                self.write(SPEC_PATH, SAFE_SPEC.replace(originals, replacement))
                self.assertIn("packaging_data_must_be_explicit_public_documents", self.rules())

    def test_extra_runtime_hooks_or_entrypoints_fail(self):
        for spec in (
            SAFE_SPEC.replace("runtime_hooks=[]", 'runtime_hooks=["local.py"]'),
            SAFE_SPEC.replace('"run_app.py"', '"secrets.py"'),
            SAFE_SPEC.replace("binaries=[]", 'binaries=[("local.dll", ".")]'),
        ):
            with self.subTest(spec=spec[:20]):
                self.write(SPEC_PATH, spec)
                self.assertIn("packaging_entrypoint_or_extra_code_outside_allowlist", self.rules())

    def test_spec_assignments_after_analysis_cannot_hide_unsafe_inputs(self):
        spec = SAFE_SPEC.replace('"README.md", ', '".env", ')
        spec += '\npublic_docs = ["README.md"]\n'
        self.write(SPEC_PATH, spec)
        self.assertIn("packaging_data_must_be_explicit_public_documents", self.rules())

    def test_spec_data_cannot_be_extended_after_checked_analysis(self):
        self.write(SPEC_PATH, SAFE_SPEC + '\nanalysis.datas += [(".env", ".")]\n')
        self.assertIn("packaging_post_analysis_mutation_not_allowed", self.rules())

    def test_safe_distribution_runtime_dependencies_and_public_certificates_pass(self):
        for name, content in {
            "TwitchChatControl.exe": "synthetic-executable",
            "_internal/python312.dll": "synthetic-runtime",
            "_internal/crypto-tokens.dll": "synthetic-runtime",
            "_internal/tokens.dll": "synthetic-runtime",
            "_internal/token.py": "# Standard library token definitions",
            "_internal/secrets.pyc": "synthetic-bytecode",
            "_internal/websockets-17.4.dist-info/LICENSE": "BSD license text",
            "_internal/certifi/cacert.pem": "-----BEGIN CERTIFICATE-----\nsynthetic-public-certificate\n-----END CERTIFICATE-----",
            "_internal/docs/PROFILES.md": "# Public profile instructions",
        }.items():
            self.write("dist/" + name, content)
        self.assertEqual(scan_distribution(self.root / "dist"), [])

    def test_distribution_excludes_user_profiles_logs_env_tests_and_keys(self):
        for name in (
            ".env",
            "_internal/tokens.json",
            "_internal/profiles/game.json",
            "settings.json",
            "_internal/debug.log",
            "_internal/tests/test_fixture.py",
            "private.pfx",
        ):
            with self.subTest(name=name):
                path = self.write("dist/" + name, "synthetic-local-data")
                rules = {finding.rule for finding in scan_distribution(self.root / "dist")}
                self.assertIn("forbidden_distribution_credential_or_user_file", rules)
                path.unlink()

    def test_cli_diagnostics_do_not_print_canary_values(self):
        secret = "synthetic-value-must-not-appear-in-output"
        self.write("src/twitch_chat_control/twitch.py", f'access_token = "{secret}"\n')
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("src/twitch_chat_control/twitch.py", output.getvalue())
        self.assertIn("nonempty_credential", output.getvalue())


if __name__ == "__main__":
    unittest.main()
