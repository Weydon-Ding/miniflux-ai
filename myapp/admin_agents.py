from hmac import compare_digest
from pathlib import Path
from secrets import token_urlsafe

from flask import make_response, render_template, request

from common.config_editor import AGENT_NAMES, ConfigEditor, ConfigEditorError, ConfigValidationError
from myapp.admin import _is_authorized, _unauthorized_response


AGENT_FIELDS = (
    ('title', '标题'),
    ('prompt', '提示词'),
    ('style_block', '引用块样式'),
    ('allow_list', '允许列表'),
    ('deny_list', '排除列表'),
)
EDITABLE_FIELDS = {
    f'agents.{name}.{field}' for name in AGENT_NAMES for field, _ in AGENT_FIELDS
}


def register_admin_agent_routes(app, config):
    config_path = Path('config.yml').resolve()
    csrf_token = token_urlsafe(32)

    @app.route('/admin/config/agents', methods=['GET', 'POST'])
    def admin_agents():
        if not _is_authorized(config):
            response = _unauthorized_response()
        elif request.method == 'POST' and not compare_digest(
            request.form.get('csrf_token', '').encode('utf-8'), csrf_token.encode('utf-8')
        ):
            response = make_response('表单验证失败，请刷新页面后重试。', 400)
        elif request.method == 'POST' and any(
            field not in EDITABLE_FIELDS | {'csrf_token'} or len(request.form.getlist(field)) != 1
            for field in request.form
        ):
            response = make_response('表单包含不支持或重复的字段。', 400)
        else:
            editor = ConfigEditor(config_path)
            saved = False
            errors = {}
            status = 200
            values = {field: '' for field in EDITABLE_FIELDS}
            try:
                values.update(editor.render_form())
            except ConfigEditorError:
                errors = {'config.yml': '无法读取配置，请检查 config.yml 的格式及读取权限。'}
                status = 500
            form_data = {field: request.form[field] for field in EDITABLE_FIELDS if field in request.form}
            values.update(form_data)
            if request.method == 'POST' and not errors:
                try:
                    editor.save(form_data)
                except ConfigValidationError as exc:
                    errors = {
                        error.field: '此项为必填项。' if error.code == 'required' else '请输入有效的配置值。'
                        for error in exc.errors
                    }
                    status = 400
                except ConfigEditorError:
                    errors = {'config.yml': '保存失败，请检查配置文件及备份目录的读写权限后重试。'}
                    status = 500
                else:
                    saved = True
                    values = editor.render_form()
            response = make_response(render_template(
                'admin/config_agents.html',
                agent_names=AGENT_NAMES,
                agent_fields=AGENT_FIELDS,
                values={field: values[field] for field in EDITABLE_FIELDS},
                csrf_token=csrf_token,
                saved=saved,
                errors=errors,
            ), status)
        response.headers['Cache-Control'] = 'no-store'
        return response
