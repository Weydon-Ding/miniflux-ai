import os
import shutil
import tempfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from yaml import safe_load as pyyaml_safe_load


SECRET_PLACEHOLDER = "********"
AGENT_NAMES = ("summary", "translate")
SECRET_FIELDS = {
    "miniflux.api_key",
    "miniflux.webhook_secret",
    "llm.api_key",
}
REQUIRED_SECRET_FIELDS = {
    "miniflux.api_key",
    "llm.api_key",
}
STRING_FIELDS = {
    "log_level",
    "miniflux.base_url",
    "llm.provider",
    "llm.base_url",
    "llm.model",
    "ai_news.url",
    "ai_news.prompts.greeting",
    "ai_news.prompts.summary",
    "ai_news.prompts.summary_block",
    "feeds_status.url",
    *[f"agents.{name}.title" for name in AGENT_NAMES],
    *[f"agents.{name}.prompt" for name in AGENT_NAMES],
}
REQUIRED_STRING_FIELDS = {
    "miniflux.base_url",
    "llm.base_url",
    "llm.model",
    *[f"agents.{name}.title" for name in AGENT_NAMES],
    *[f"agents.{name}.prompt" for name in AGENT_NAMES],
}
INT_FIELDS = {
    "miniflux.schedule_interval": 1,
    "llm.max_length": 0,
    "llm.timeout": 1,
    "llm.max_workers": 1,
    "llm.RPM": 1,
}
BOOL_FIELDS = {
    "feeds_status.enabled",
    *[f"agents.{name}.style_block" for name in AGENT_NAMES],
}
LIST_FIELDS = {
    "ai_news.schedule",
    *[f"agents.{name}.allow_list" for name in AGENT_NAMES],
    *[f"agents.{name}.deny_list" for name in AGENT_NAMES],
}
TIME_LIST_FIELDS = {"ai_news.schedule"}
TIME_FIELDS = {"feeds_status.schedule"}
MAPPING_FIELDS = {"llm.extra_params"}
FIELD_ORDER = (
    "log_level",
    "miniflux.base_url",
    "miniflux.api_key",
    "miniflux.webhook_secret",
    "miniflux.schedule_interval",
    "llm.provider",
    "llm.base_url",
    "llm.api_key",
    "llm.model",
    "llm.max_length",
    "llm.timeout",
    "llm.max_workers",
    "llm.RPM",
    "llm.extra_params",
    "ai_news.url",
    "ai_news.schedule",
    "ai_news.prompts.greeting",
    "ai_news.prompts.summary",
    "ai_news.prompts.summary_block",
    "agents.summary.title",
    "agents.summary.prompt",
    "agents.summary.style_block",
    "agents.summary.deny_list",
    "agents.summary.allow_list",
    "agents.translate.title",
    "agents.translate.prompt",
    "agents.translate.style_block",
    "agents.translate.deny_list",
    "agents.translate.allow_list",
    "feeds_status.enabled",
    "feeds_status.url",
    "feeds_status.schedule",
)
KNOWN_FIELDS = set(FIELD_ORDER)
TRUE_VALUES = {"true", "on", "1", "yes"}
FALSE_VALUES = {"false", "off", "0", "no"}
DEFAULTS = {
    "log_level": "INFO",
    "llm.provider": "openai",
    "llm.timeout": 60,
    "llm.max_workers": 4,
    "llm.RPM": 1000,
    "feeds_status.enabled": False,
    "feeds_status.schedule": "09:00",
}


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    values: dict
    errors: tuple[FieldError, ...]

    @property
    def valid(self):
        return not self.errors


@dataclass(frozen=True)
class SaveResult:
    config_path: Path
    backup_path: Path


class ConfigEditorError(Exception):
    pass


class ConfigLoadError(ConfigEditorError):
    pass


class ConfigValidationError(ConfigEditorError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("Config validation failed")


class ConfigBackupError(ConfigEditorError):
    pass


class ConfigSaveError(ConfigEditorError):
    pass


class ConfigEditor:
    def __init__(self, config_path="config.yml", backup_path=None):
        self.config_path = Path(config_path)
        if backup_path is None:
            self.backup_path = self.config_path.with_name(self.config_path.name + ".bak")
        else:
            self.backup_path = Path(backup_path)
        self.yaml = YAML(typ="rt")
        self.yaml.preserve_quotes = True
        self.yaml.width = 4096
        self.snippet_yaml = YAML(typ="safe")
        self.snippet_yaml.default_flow_style = False

    def load(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as config_file:
                document = self.yaml.load(config_file)
        except Exception as exc:
            raise ConfigLoadError(f"Failed to load config: {exc}") from exc

        if not isinstance(document, dict):
            raise ConfigLoadError("Config must be a mapping")
        return document

    def render_form(self, document=None):
        document = document if document is not None else self.load()
        form = {}

        for field in FIELD_ORDER:
            if field == "feeds_status.url":
                value = self._feeds_status_url_default(document)
            else:
                value = self._get_path(document, field, DEFAULTS.get(field))
            if field in SECRET_FIELDS:
                form[field] = SECRET_PLACEHOLDER if value else ""
            elif field in BOOL_FIELDS:
                form[field] = self._format_bool(value)
            elif field in LIST_FIELDS:
                form[field] = self._format_list(value)
            elif field in MAPPING_FIELDS:
                form[field] = self._dump_mapping(value)
            elif value is None:
                form[field] = ""
            else:
                form[field] = str(value)

        return form

    def validate(self, form_data, document=None):
        document = document if document is not None else self.load()
        values = {}
        errors = []

        for field in form_data:
            if field not in KNOWN_FIELDS:
                errors.append(FieldError(field, "unknown_field", "Unknown config field"))

        for field in FIELD_ORDER:
            if field in form_data:
                value, field_errors = self._parse_field(field, form_data[field], document)
            else:
                value, field_errors = self._normalize_existing_field(field, document)

            if field_errors:
                errors.extend(field_errors)
            elif field in form_data or self._should_apply_existing_field(field, document, value):
                values[field] = value

            self._validate_required(field, value, errors)

        if self._has_schedule(values, document):
            for field in (
                "ai_news.prompts.greeting",
                "ai_news.prompts.summary",
                "ai_news.prompts.summary_block",
            ):
                value = values.get(field, self._get_path(document, field))
                if self._is_blank(value):
                    errors.append(FieldError(field, "required", "This field is required when ai_news.schedule is configured"))

        if self._is_feeds_status_enabled(values, document):
            value = values.get("feeds_status.url")
            if self._is_blank(value):
                value = self._feeds_status_url_default(document)
            if self._is_blank(value):
                errors.append(FieldError("feeds_status.url", "required", "This field is required when feeds_status.enabled is true"))
            schedule = values.get(
                "feeds_status.schedule",
                self._get_path(document, "feeds_status.schedule", DEFAULTS["feeds_status.schedule"]),
            )
            schedule_text = self._coerce_text(schedule).strip()
            if not schedule_text:
                errors.append(FieldError("feeds_status.schedule", "required", "This field is required when feeds_status.enabled is true"))
            elif not self._valid_time(schedule_text):
                errors.append(FieldError("feeds_status.schedule", "invalid_time", "Use HH:MM in 24-hour format"))

        if errors:
            values = {}

        return ValidationResult(values=values, errors=tuple(errors))

    def apply(self, document, validation):
        if not validation.valid:
            raise ConfigValidationError(validation.errors)
        for field, value in validation.values.items():
            old_value = self._get_path(document, field)
            if self._value_unchanged(old_value, value):
                continue
            self._set_path(document, field, value)
        return document

    def backup(self):
        try:
            shutil.copy2(self.config_path, self.backup_path)
        except Exception as exc:
            raise ConfigBackupError(f"Failed to back up config: {exc}") from exc
        return self.backup_path

    def save(self, form_data):
        document = self.load()
        validation = self.validate(form_data, document)
        if not validation.valid:
            raise ConfigValidationError(validation.errors)

        updated = self.apply(document, validation)
        tmp_path = None
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.config_path.parent,
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                tmp_path = Path(temp_file.name)
                self.yaml.dump(updated, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            self.backup()
            os.replace(tmp_path, self.config_path)
            tmp_path = None
        except ConfigBackupError:
            raise
        except Exception as exc:
            raise ConfigSaveError(f"Failed to save config: {exc}") from exc
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return SaveResult(config_path=self.config_path, backup_path=self.backup_path)

    def _parse_field(self, field, raw_value, document):
        if field in SECRET_FIELDS:
            return self._parse_secret(field, raw_value, document)
        if field in STRING_FIELDS or field in TIME_FIELDS:
            return self._parse_string(field, raw_value)
        if field in INT_FIELDS:
            return self._parse_int(field, raw_value)
        if field in BOOL_FIELDS:
            return self._parse_bool(field, raw_value)
        if field in LIST_FIELDS:
            return self._parse_list(field, raw_value, document)
        if field in MAPPING_FIELDS:
            return self._parse_mapping(field, raw_value)
        return None, (FieldError(field, "unknown_field", "Unknown config field"),)

    def _normalize_existing_field(self, field, document):
        old_value = self._get_path(document, field, DEFAULTS.get(field))
        if old_value is None and field in DEFAULTS:
            old_value = DEFAULTS[field]
        if isinstance(old_value, str):
            if field in SECRET_FIELDS or field in STRING_FIELDS or field in TIME_FIELDS:
                return self._quote_string(old_value), ()
            if field in BOOL_FIELDS:
                return self._parse_bool(field, old_value)
        return old_value, ()

    def _should_apply_existing_field(self, field, document, value):
        old_value = self._get_path(document, field)
        if old_value is None and field in DEFAULTS:
            return True
        if isinstance(old_value, str):
            if field in SECRET_FIELDS or field in STRING_FIELDS or field in TIME_FIELDS:
                return not self._pyyaml_same_scalar_type(old_value)
            if field in BOOL_FIELDS and isinstance(value, bool):
                return True
        return False

    def _value_unchanged(self, old_value, new_value):
        if isinstance(old_value, str) and isinstance(new_value, str):
            if not self._pyyaml_same_scalar_type(old_value):
                return False
        return old_value == new_value

    def _parse_secret(self, field, raw_value, document):
        value = self._coerce_text(raw_value).strip()
        old_value = self._get_path(document, field)
        if value in ("", SECRET_PLACEHOLDER):
            if isinstance(old_value, str):
                return self._quote_string(old_value), ()
            return old_value, ()
        return self._quote_string(value), ()

    def _parse_string(self, field, raw_value):
        value = self._coerce_text(raw_value)
        if field in TIME_FIELDS:
            value = value.strip()
            if value and not self._valid_time(value):
                return None, (FieldError(field, "invalid_time", "Use HH:MM in 24-hour format"),)
            if value:
                return self._quote_string(value), ()
        if field in {"ai_news.url", "feeds_status.url"} and self._is_blank(value):
            return None, ()
        if field in DEFAULTS and self._is_blank(value):
            return self._quote_string(DEFAULTS[field]), ()
        return self._quote_string(value), ()

    def _parse_int(self, field, raw_value):
        value = self._coerce_text(raw_value).strip()
        if value == "":
            if field in DEFAULTS:
                return DEFAULTS[field], ()
            return None, ()
        try:
            parsed = int(value)
        except ValueError:
            return None, (FieldError(field, "type", "Enter an integer"),)
        minimum = INT_FIELDS[field]
        if parsed < minimum:
            return None, (FieldError(field, "type", f"Enter an integer greater than or equal to {minimum}"),)
        return parsed, ()

    def _parse_bool(self, field, raw_value):
        value = self._coerce_text(raw_value).strip().lower()
        if value in TRUE_VALUES:
            return True, ()
        if value in FALSE_VALUES:
            return False, ()
        return None, (FieldError(field, "type", "Enter true or false"),)

    def _parse_list(self, field, raw_value, document):
        text = self._coerce_text(raw_value)
        items = [line.strip() for line in text.splitlines() if line.strip()]
        if not items:
            old_value = self._get_path(document, field)
            if old_value is None:
                return None, ()
            return [], ()
        if field in TIME_LIST_FIELDS:
            invalid = [item for item in items if not self._valid_time(item)]
            if invalid:
                return None, (FieldError(field, "invalid_time", "Use one HH:MM time per line"),)
        return [self._quote_string(item) for item in items], ()

    def _parse_mapping(self, field, raw_value):
        text = self._coerce_text(raw_value)
        if not text.strip():
            return {}, ()
        try:
            parsed = self.snippet_yaml.load(text)
        except Exception:
            return None, (FieldError(field, "invalid_yaml", "Enter a valid YAML mapping"),)
        if parsed is None:
            return {}, ()
        if not isinstance(parsed, dict):
            return None, (FieldError(field, "invalid_mapping", "Enter a YAML mapping"),)
        return self._quote_mapping_strings(parsed), ()

    def _validate_required(self, field, value, errors):
        if field in REQUIRED_SECRET_FIELDS and self._is_blank(value):
            errors.append(FieldError(field, "required", "This field is required"))
        elif field in REQUIRED_STRING_FIELDS and self._is_blank(value):
            errors.append(FieldError(field, "required", "This field is required"))

    def _has_schedule(self, values, document):
        schedule = values.get("ai_news.schedule", self._get_path(document, "ai_news.schedule"))
        return bool(schedule)

    def _is_feeds_status_enabled(self, values, document):
        enabled = values.get(
            "feeds_status.enabled",
            self._get_path(document, "feeds_status.enabled", DEFAULTS["feeds_status.enabled"]),
        )
        if isinstance(enabled, str):
            parsed = self._parse_yaml_11_scalar(enabled)
            if isinstance(parsed, bool):
                return parsed
        return bool(enabled)

    def _feeds_status_url_default(self, document):
        return self._get_path(
            document,
            "feeds_status.url",
            self._get_path(document, "ai_news.url"),
        )

    def _get_path(self, document, field, default=None):
        current = document
        for part in field.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def _set_path(self, document, field, value):
        current = document
        parts = field.split(".")
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = CommentedMap()
            current = current[part]
        current[parts[-1]] = value

    def _format_bool(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            parsed = self._parse_yaml_11_scalar(value)
            if isinstance(parsed, bool):
                return "true" if parsed else "false"
        return "true" if bool(value) else "false"

    def _format_list(self, value):
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return "\n".join(str(item) for item in value)
        return str(value)

    def _dump_mapping(self, value):
        if not value:
            return ""
        stream = StringIO()
        self.snippet_yaml.dump(self._to_plain_data(value), stream)
        return stream.getvalue().strip()

    def _to_plain_data(self, value):
        if isinstance(value, dict):
            return {key: self._to_plain_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._to_plain_data(item) for item in value]
        return value

    def _quote_string(self, value):
        return DoubleQuotedScalarString(value)

    def _quote_mapping_strings(self, value):
        if isinstance(value, dict):
            return CommentedMap(
                (
                    self._quote_string(key) if isinstance(key, str) else key,
                    self._quote_mapping_strings(item),
                )
                for key, item in value.items()
            )
        if isinstance(value, list):
            return [self._quote_mapping_strings(item) for item in value]
        if isinstance(value, str):
            return self._quote_string(value)
        return value

    def _parse_yaml_11_scalar(self, value):
        return pyyaml_safe_load(value)

    def _pyyaml_same_scalar_type(self, value):
        try:
            parsed = self._parse_yaml_11_scalar(value)
        except Exception:
            return True
        return isinstance(parsed, str)

    def _coerce_text(self, value):
        if value is None:
            return ""
        return str(value)

    def _is_blank(self, value):
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _valid_time(self, value):
        if len(value) != 5 or value[2] != ":":
            return False
        hour = value[:2]
        minute = value[3:]
        if not hour.isdigit() or not minute.isdigit():
            return False
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
