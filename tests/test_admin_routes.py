import base64
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class AdminRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.old_admin_password = os.environ.pop("MINIFLUX_AI_ADMIN_PASSWORD", None)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        self._clear_app_modules()

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()
        if self.old_admin_password is not None:
            os.environ["MINIFLUX_AI_ADMIN_PASSWORD"] = self.old_admin_password
        self._clear_app_modules()

    def _clear_app_modules(self):
        for module_name in list(sys.modules):
            if (
                module_name == "myapp"
                or module_name.startswith("myapp.")
                or module_name == "common"
                or module_name.startswith("common.")
                or module_name == "core"
                or module_name.startswith("core.")
            ):
                del sys.modules[module_name]

    def import_app(self, config_content):
        Path("config.yml").write_text(config_content, encoding="utf-8")
        myapp = importlib.import_module("myapp")
        return myapp.app

    def config_with(self, content):
        return (
            "miniflux:\n"
            "  base_url: https://miniflux.example.test\n"
            "  api_key: miniflux-test-key\n"
            "llm:\n"
            "  base_url: https://llm.example.test/v1\n"
            "  api_key: llm-test-key\n"
            "  model: test-model\n"
            f"{content}"
        )

    def route_paths(self, app):
        return {rule.rule for rule in app.url_map.iter_rules()}

    def basic_auth_header(self, username, password):
        token = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def import_admin_app(self):
        return self.import_app(
            self.config_with(
                "admin:\n"
                "  enabled: true\n"
                "  username: operator\n"
                "  password: test-admin-password\n"
            )
        )

    def test_admin_config_route_is_not_registered_by_default(self):
        app = self.import_app(self.config_with(""))

        self.assertNotIn("/admin/config", self.route_paths(app))
        response = app.test_client().get("/admin/config")
        self.assertEqual(response.status_code, 404)

    def test_admin_config_route_requires_basic_auth_when_admin_is_enabled(self):
        app = self.import_admin_app()

        self.assertIn("/admin/config", self.route_paths(app))
        response = app.test_client().get("/admin/config")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], 'Basic realm="miniflux-ai admin"')

    def test_admin_config_route_rejects_wrong_basic_auth_credentials(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "wrong-password"),
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_config_route_accepts_correct_basic_auth_credentials(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"miniflux-ai admin configuration", response.data)

    def test_admin_config_route_is_not_registered_for_string_false(self):
        app = self.import_app(self.config_with("admin:\n  enabled: 'false'\n"))

        self.assertNotIn("/admin/config", self.route_paths(app))


if __name__ == "__main__":
    unittest.main()
