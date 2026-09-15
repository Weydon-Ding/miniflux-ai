from hmac import compare_digest

from flask import Response, request


_ADMIN_REALM = 'miniflux-ai admin'


def _unauthorized_response():
    return Response(
        'Authentication required',
        401,
        {'WWW-Authenticate': f'Basic realm="{_ADMIN_REALM}"'},
        mimetype='text/plain',
    )


def _is_authorized(config):
    auth = request.authorization
    if not auth or not config.admin_password:
        return False

    username = auth.username or ''
    password = auth.password or ''
    expected_username = config.admin_username or ''

    return compare_digest(username, expected_username) and compare_digest(password, config.admin_password)


def register_admin_routes(app, config):
    @app.route('/admin/config', methods=['GET'])
    def admin_config():
        if not _is_authorized(config):
            return _unauthorized_response()

        return Response('miniflux-ai admin configuration', mimetype='text/plain')
