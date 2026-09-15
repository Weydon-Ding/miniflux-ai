import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def load_config_class():
    config_path = Path(__file__).resolve().parents[1] / "common" / "config.py"
    spec = importlib.util.spec_from_file_location("config_mod", config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Config


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.Config = load_config_class()
        self.old_cwd = os.getcwd()
        self.old_admin_password = os.environ.pop("MINIFLUX_AI_ADMIN_PASSWORD", None)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()
        if self.old_admin_password is not None:
            os.environ["MINIFLUX_AI_ADMIN_PASSWORD"] = self.old_admin_password

    def write_config(self, content):
        Path("config.yml").write_text(content, encoding="utf8")

    def restore_env(self, name, value):
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def test_extra_params_defaults_to_empty_mapping(self):
        self.write_config("llm:\n  extra_params:\n")

        config = self.Config()

        self.assertEqual(config.llm_extra_params, {})

    def test_extra_params_accepts_nested_mapping(self):
        self.write_config(
            "llm:\n"
            "  extra_params:\n"
            "    thinking_config:\n"
            "      thinking_budget: 0\n"
        )

        config = self.Config()

        self.assertEqual(
            config.llm_extra_params,
            {"thinking_config": {"thinking_budget": 0}},
        )

    def test_extra_params_rejects_non_mapping(self):
        self.write_config("llm:\n  extra_params: nope\n")

        with self.assertRaises(ValueError):
            self.Config()

    def test_admin_config_defaults_to_disabled_with_default_username(self):
        self.write_config("miniflux: {}\nllm: {}\n")

        config = self.Config()

        self.assertFalse(config.admin_enabled)
        self.assertEqual(config.admin_username, "admin")
        self.assertIsNone(config.admin_password)

    def test_admin_config_reads_enabled_credentials_and_password_env(self):
        self.write_config(
            "admin:\n"
            "  enabled: true\n"
            "  username: operator\n"
            "  password_env: CUSTOM_ADMIN_PASSWORD\n"
            "  password: config-password\n"
        )
        old_password = os.environ.get("CUSTOM_ADMIN_PASSWORD")
        os.environ["CUSTOM_ADMIN_PASSWORD"] = "env-password"
        try:
            config = self.Config()
        finally:
            self.restore_env("CUSTOM_ADMIN_PASSWORD", old_password)

        self.assertTrue(config.admin_enabled)
        self.assertEqual(config.admin_username, "operator")
        self.assertEqual(config.admin_password_env, "CUSTOM_ADMIN_PASSWORD")
        self.assertEqual(config.admin_password, "env-password")

    def test_admin_config_falls_back_to_config_password_without_password_env(self):
        self.write_config(
            "admin:\n"
            "  enabled: true\n"
            "  username: operator\n"
            "  password_env: CUSTOM_ADMIN_PASSWORD\n"
            "  password: config-password\n"
        )
        old_password = os.environ.pop("CUSTOM_ADMIN_PASSWORD", None)
        try:
            config = self.Config()
        finally:
            self.restore_env("CUSTOM_ADMIN_PASSWORD", old_password)

        self.assertEqual(config.admin_password, "config-password")


if __name__ == "__main__":
    unittest.main()
