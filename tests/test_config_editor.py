import importlib.util
import tempfile
import unittest
from pathlib import Path

from yaml import safe_load


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_config_editor_module():
    module_path = REPO_ROOT / "common" / "config_editor.py"
    spec = importlib.util.spec_from_file_location("config_editor_mod", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigEditorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.yml"
        self.config_path.write_text(
            "# keep this comment\n"
            "log_level: INFO\n"
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: old-miniflux-key\n"
            "  webhook_secret: old-webhook-secret\n"
            "  schedule_interval: 15\n"
            "llm:\n"
            "  provider: openai\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "  max_length: 10000\n"
            "  timeout: 60\n"
            "  max_workers: 4\n"
            "  RPM: 1000\n"
            "  extra_params:\n"
            "    extra_body:\n"
            "      enable_thinking: false\n"
            "ai_news:\n"
            "  url: http://miniflux-ai\n"
            "  schedule:\n"
            "    - '07:30'\n"
            "    - '18:00'\n"
            "  prompts:\n"
            "    greeting: hello\n"
            "    summary: summarize\n"
            "    summary_block: blocks\n"
            "agents:\n"
            "  summary:\n"
            "    title: '֎ AI summary:'\n"
            "    prompt: summarize ${content}\n"
            "    style_block: true\n"
            "    deny_list:\n"
            "      - https://ai-news.miniflux\n"
            "    allow_list:\n"
            "  translate:\n"
            "    title: '🌐AI translate:'\n"
            "    prompt: translate ${content}\n"
            "    style_block: false\n"
            "    deny_list:\n"
            "    allow_list:\n"
            "      - https://example.com/\n"
            "  custom_agent:\n"
            "    title: keep me\n"
            "    prompt: custom prompt\n"
            "feeds_status:\n"
            "  enabled: true\n"
            "  url: http://miniflux-ai\n"
            "  schedule: '09:00'\n"
            "custom_section:\n"
            "  keep: yes\n",
            encoding="utf-8",
        )
        self.config_editor = load_config_editor_module()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_provider_accepts_supported_modes_and_rejects_unknown_mode_without_writing(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        original = self.config_path.read_bytes()
        for provider in ('openai', 'gemini', ''):
            with self.subTest(provider=provider):
                result = editor.validate({'llm.provider': provider})
                self.assertTrue(result.valid, result.errors)
                self.assertEqual(result.values['llm.provider'], provider or 'openai')
        with self.assertRaises(self.config_editor.ConfigValidationError) as raised:
            editor.save({'llm.provider': 'unsupported'})
        self.assertEqual([(error.field, error.code) for error in raised.exception.errors],
                         [('llm.provider', 'choice')])
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse(editor.backup_path.exists())

    def test_render_form_masks_existing_secrets(self):
        editor = self.config_editor.ConfigEditor(self.config_path)

        form = editor.render_form()

        self.assertEqual(form["miniflux.api_key"], self.config_editor.SECRET_PLACEHOLDER)
        self.assertEqual(form["miniflux.webhook_secret"], self.config_editor.SECRET_PLACEHOLDER)
        self.assertEqual(form["llm.api_key"], self.config_editor.SECRET_PLACEHOLDER)
        self.assertNotIn("old-miniflux-key", form.values())
        self.assertNotIn("old-webhook-secret", form.values())
        self.assertNotIn("old-llm-key", form.values())

    def test_render_form_formats_lists_booleans_numbers_and_mapping_snippet(self):
        editor = self.config_editor.ConfigEditor(self.config_path)

        form = editor.render_form()

        self.assertEqual(form["miniflux.schedule_interval"], "15")
        self.assertEqual(form["feeds_status.enabled"], "true")
        self.assertEqual(form["ai_news.schedule"], "07:30\n18:00")
        self.assertEqual(form["agents.summary.deny_list"], "https://ai-news.miniflux")
        self.assertEqual(form["agents.translate.allow_list"], "https://example.com/")
        self.assertIn("extra_body:", form["llm.extra_params"])
        self.assertIn("enable_thinking: false", form["llm.extra_params"])

    def test_render_form_handles_round_trip_scalars_in_extra_params(self):
        self.config_path.write_text(
            'llm:\n'
            '  extra_params:\n'
            '    "temperature": 0.0\n'
            '    thinking_budget: 0\n'
            '    labels: ["on", 1_000, false]\n',
            encoding='utf-8',
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        form = editor.render_form()

        from yaml import safe_load
        self.assertEqual(safe_load(form['llm.extra_params']), {
            'temperature': 0.0, 'thinking_budget': 0, 'labels': ['on', 1000, False],
        })

    def test_apply_updates_fields_and_preserves_masked_secrets(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        document = editor.load()

        result = editor.validate(
            {
                "miniflux.base_url": "https://new-miniflux.example.com",
                "miniflux.api_key": self.config_editor.SECRET_PLACEHOLDER,
                "miniflux.webhook_secret": "",
                "llm.base_url": "https://new-llm.example.com/v1",
                "llm.api_key": "new-llm-key",
                "llm.model": "new-model",
            },
            document,
        )
        updated = editor.apply(document, result)

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(updated["miniflux"]["base_url"], "https://new-miniflux.example.com")
        self.assertEqual(updated["miniflux"]["api_key"], "old-miniflux-key")
        self.assertEqual(updated["miniflux"]["webhook_secret"], "old-webhook-secret")
        self.assertEqual(updated["llm"]["base_url"], "https://new-llm.example.com/v1")
        self.assertEqual(updated["llm"]["api_key"], "new-llm-key")
        self.assertEqual(updated["llm"]["model"], "new-model")

    def test_validate_reports_field_errors_without_secret_values(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        document = editor.load()

        result = editor.validate(
            {
                "miniflux.base_url": "",
                "miniflux.schedule_interval": "fifteen",
                "llm.api_key": "submitted-secret",
                "llm.max_workers": "0",
                "llm.extra_params": "- nope",
                "ai_news.schedule": "07:30\n24:00",
                "agents.summary.style_block": "maybe",
                "feeds_status.schedule": "9:00",
                "unknown.field": "value",
            },
            document,
        )

        self.assertFalse(result.valid)
        errors = {(error.field, error.code) for error in result.errors}
        self.assertIn(("miniflux.base_url", "required"), errors)
        self.assertIn(("miniflux.schedule_interval", "type"), errors)
        self.assertIn(("llm.max_workers", "type"), errors)
        self.assertIn(("llm.extra_params", "invalid_mapping"), errors)
        self.assertIn(("ai_news.schedule", "invalid_time"), errors)
        self.assertIn(("agents.summary.style_block", "type"), errors)
        self.assertIn(("feeds_status.schedule", "invalid_time"), errors)
        self.assertIn(("unknown.field", "unknown_field"), errors)
        rendered_errors = "\n".join(error.message for error in result.errors)
        self.assertNotIn("submitted-secret", rendered_errors)

    def test_validate_requires_secret_when_no_old_secret_exists(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  model: test-model\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        result = editor.validate(
            {
                "miniflux.api_key": self.config_editor.SECRET_PLACEHOLDER,
                "llm.api_key": "",
            },
            editor.load(),
        )

        errors = {(error.field, error.code) for error in result.errors}
        self.assertIn(("miniflux.api_key", "required"), errors)
        self.assertIn(("llm.api_key", "required"), errors)

    def test_save_backs_up_old_config_and_preserves_round_trip_content(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        old_content = self.config_path.read_text(encoding="utf-8")
        form = editor.render_form()
        form.update(
            {
                "miniflux.base_url": "https://saved-miniflux.example.com",
                "llm.timeout": "90",
                "llm.extra_params": "extra_body:\n  temperature: 0\n  enabled: false\n",
                "ai_news.schedule": "08:00\n19:30",
                "agents.summary.allow_list": "https://allowed.example/\n\n https://second.example/ ",
                "feeds_status.enabled": "false",
            }
        )

        result = editor.save(form)

        self.assertEqual(result.config_path, self.config_path)
        self.assertEqual(result.backup_path, self.config_path.with_name("config.yml.bak"))
        self.assertEqual(result.backup_path.read_text(encoding="utf-8"), old_content)
        saved_content = self.config_path.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", saved_content)
        self.assertIn("custom_section:", saved_content)
        self.assertLess(saved_content.index("miniflux:"), saved_content.index("llm:"))
        saved = safe_load(saved_content)
        self.assertEqual(saved["miniflux"]["base_url"], "https://saved-miniflux.example.com")
        self.assertEqual(saved["miniflux"]["api_key"], "old-miniflux-key")
        self.assertEqual(saved["llm"]["timeout"], 90)
        self.assertEqual(saved["llm"]["extra_params"], {"extra_body": {"temperature": 0, "enabled": False}})
        self.assertEqual(saved["ai_news"]["schedule"], ["08:00", "19:30"])
        self.assertEqual(saved["agents"]["summary"]["allow_list"], ["https://allowed.example/", "https://second.example/"])
        self.assertEqual(saved["agents"]["custom_agent"], {"title": "keep me", "prompt": "custom prompt"})
        self.assertFalse(saved["feeds_status"]["enabled"])

    def test_save_empty_extra_params_as_empty_mapping(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        form = editor.render_form()
        form["llm.extra_params"] = ""

        editor.save(form)

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["extra_params"], {})

    def test_save_quotes_string_list_mapping_and_secret_values_that_pyyaml_would_coerce(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        form = editor.render_form()
        form.update(
            {
                "llm.model": "yes",
                "llm.api_key": "12:34",
                "agents.summary.allow_list": "yes\nno",
                "llm.extra_params": "extra_body:\n  flag: yes\n  names:\n    - no\n",
            }
        )

        editor.save(form)

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["model"], "yes")
        self.assertEqual(saved["llm"]["api_key"], "12:34")
        self.assertEqual(saved["agents"]["summary"]["allow_list"], ["yes", "no"])
        self.assertEqual(
            saved["llm"]["extra_params"],
            {"extra_body": {"flag": "yes", "names": ["no"]}},
        )

    def test_empty_list_fields_keep_none_or_write_empty_list_by_existing_shape(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        form = editor.render_form()
        form.update(
            {
                "agents.summary.deny_list": "",
                "agents.summary.allow_list": "",
            }
        )

        editor.save(form)

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["agents"]["summary"]["deny_list"], [])
        self.assertIsNone(saved["agents"]["summary"]["allow_list"])

    def test_save_preserves_comments_on_unchanged_mapping_and_list_fields(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: old-miniflux-key\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "  extra_params:\n"
            "    # provider option\n"
            "    foo: bar\n"
            "ai_news:\n"
            "  schedule:\n"
            "    - '07:30' # morning\n"
            "  prompts:\n"
            "    greeting: hello\n"
            "    summary: summarize\n"
            "    summary_block: blocks\n"
            "agents:\n"
            "  summary:\n"
            "    title: summary\n"
            "    prompt: summarize ${content}\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)
        form = editor.render_form()
        form["miniflux.base_url"] = "https://changed-miniflux.example.com"

        editor.save(form)

        saved_content = self.config_path.read_text(encoding="utf-8")
        self.assertIn("# provider option", saved_content)
        self.assertIn("# morning", saved_content)

    def test_empty_defaulted_fields_keep_runtime_safe_defaults(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        form = editor.render_form()
        form.update(
            {
                "log_level": "",
                "llm.timeout": "",
                "llm.max_workers": "",
                "llm.RPM": "",
                "feeds_status.schedule": "",
            }
        )

        editor.save(form)

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["log_level"], "INFO")
        self.assertEqual(saved["llm"]["timeout"], 60)
        self.assertEqual(saved["llm"]["max_workers"], 4)
        self.assertEqual(saved["llm"]["RPM"], 1000)
        self.assertEqual(saved["feeds_status"]["schedule"], "09:00")

    def test_partial_save_normalizes_null_runtime_defaults(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: old-miniflux-key\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "  timeout:\n"
            "  max_workers:\n"
            "  RPM:\n"
            "feeds_status:\n"
            "  enabled: false\n"
            "  schedule:\n"
            "agents:\n"
            "  summary:\n"
            "    title: summary\n"
            "    prompt: summarize ${content}\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        editor.save({"miniflux.base_url": "https://changed-miniflux.example.com"})

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["llm"]["timeout"], 60)
        self.assertEqual(saved["llm"]["max_workers"], 4)
        self.assertEqual(saved["llm"]["RPM"], 1000)
        self.assertEqual(saved["feeds_status"]["schedule"], "09:00")

    def test_partial_save_quotes_existing_strings_that_pyyaml_would_coerce(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: yes\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: 12:34\n"
            "  model: no\n"
            "agents:\n"
            "  summary:\n"
            "    title: on\n"
            "    prompt: summarize ${content}\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        editor.save({"miniflux.base_url": "https://changed-miniflux.example.com"})

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["miniflux"]["api_key"], "yes")
        self.assertEqual(saved["llm"]["api_key"], "12:34")
        self.assertEqual(saved["llm"]["model"], "no")
        self.assertEqual(saved["agents"]["summary"]["title"], "on")

    def test_partial_save_keeps_special_secret_values_that_are_not_yaml_scalars(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: '['\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "agents:\n"
            "  summary:\n"
            "    title: summary\n"
            "    prompt: summarize ${content}\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        editor.save({"miniflux.base_url": "https://changed-miniflux.example.com"})

        saved = safe_load(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["miniflux"]["api_key"], "[")

    def test_render_form_reads_yaml_1_1_boolean_strings_as_booleans(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: old-miniflux-key\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "feeds_status:\n"
            "  enabled: no\n"
            "agents:\n"
            "  summary:\n"
            "    title: summary\n"
            "    prompt: summarize ${content}\n"
            "    style_block: off\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n"
            "    style_block: on\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        form = editor.render_form()

        self.assertEqual(form["feeds_status.enabled"], "false")
        self.assertEqual(form["agents.summary.style_block"], "false")
        self.assertEqual(form["agents.translate.style_block"], "true")

    def test_feeds_status_url_can_fall_back_to_ai_news_url(self):
        self.config_path.write_text(
            "miniflux:\n"
            "  base_url: https://miniflux.example.com\n"
            "  api_key: old-miniflux-key\n"
            "llm:\n"
            "  base_url: https://llm.example.com/v1\n"
            "  api_key: old-llm-key\n"
            "  model: test-model\n"
            "ai_news:\n"
            "  url: http://miniflux-ai\n"
            "feeds_status:\n"
            "  enabled: true\n"
            "  schedule: '09:00'\n"
            "agents:\n"
            "  summary:\n"
            "    title: summary\n"
            "    prompt: summarize ${content}\n"
            "  translate:\n"
            "    title: translate\n"
            "    prompt: translate ${content}\n",
            encoding="utf-8",
        )
        editor = self.config_editor.ConfigEditor(self.config_path)

        form = editor.render_form()
        result = editor.validate(
            {"miniflux.base_url": "https://changed-miniflux.example.com"},
            editor.load(),
        )

        self.assertEqual(form["feeds_status.url"], "http://miniflux-ai")
        self.assertTrue(result.valid, result.errors)

    def test_save_rejects_invalid_form_without_touching_file_or_backup(self):
        editor = self.config_editor.ConfigEditor(self.config_path)
        old_content = self.config_path.read_text(encoding="utf-8")

        with self.assertRaises(self.config_editor.ConfigValidationError):
            editor.save({"miniflux.base_url": "", "llm.extra_params": "not-a-mapping"})

        self.assertEqual(self.config_path.read_text(encoding="utf-8"), old_content)
        self.assertFalse(self.config_path.with_name("config.yml.bak").exists())

    def test_save_backup_failure_keeps_original_config_and_cleans_temp_file(self):
        backup_path = Path(self.tmpdir.name) / "missing" / "config.yml.bak"
        editor = self.config_editor.ConfigEditor(self.config_path, backup_path=backup_path)
        old_content = self.config_path.read_text(encoding="utf-8")
        form = editor.render_form()
        form["miniflux.base_url"] = "https://should-not-save.example.com"

        with self.assertRaises(self.config_editor.ConfigBackupError):
            editor.save(form)

        self.assertEqual(self.config_path.read_text(encoding="utf-8"), old_content)
        self.assertFalse(backup_path.exists())
        self.assertEqual(list(self.config_path.parent.glob(".config.yml.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
