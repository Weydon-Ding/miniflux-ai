from flask import Response


def register_admin_routes(app):
    @app.route('/admin/config', methods=['GET'])
    def admin_config():
        return Response('miniflux-ai admin configuration', mimetype='text/plain')
