import logging
import traceback
from flask import Flask, jsonify, render_template_string, request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DBAPIError
from config import Config
from extensions import db, login_manager, bcrypt, migrate
from routes.auth_routes import auth_bp
from routes.dashboard_routes import dashboard_bp
from models.user import User
import models.settings

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    logging.basicConfig(level=logging.INFO)

    # Initialize Extensions
    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    migrate.init_app(app, db)
    from extensions import socketio
    socketio.init_app(app, cors_allowed_origins="*", async_mode=app.config.get('SOCKETIO_ASYNC_MODE', 'threading'))

    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (OperationalError, DBAPIError):
            db.session.rollback()
            try:
                return db.session.get(User, int(user_id))
            except Exception:
                return None

    # Register Blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    from routes.customer_routes import customer_bp
    app.register_blueprint(customer_bp)
    from routes.prescription_routes import prescription_bp
    app.register_blueprint(prescription_bp)
    from routes.order_routes import order_bp
    app.register_blueprint(order_bp)
    from routes.inventory_routes import inventory_bp
    app.register_blueprint(inventory_bp)
    from routes.audit_routes import audit_bp
    app.register_blueprint(audit_bp)
    from routes.deletion_request_routes import deletion_request_bp
    app.register_blueprint(deletion_request_bp)
    from routes.ai_routes import ai_bp
    app.register_blueprint(ai_bp)
    import models.chat_history

    @app.context_processor
    def inject_pending_deletions():
        from flask_login import current_user
        if current_user.is_authenticated and current_user.is_admin:
            try:
                from models.deletion_request import DeletionRequest
                count = DeletionRequest.query.filter_by(status='Pending').count()
                return dict(pending_deletion_count=count)
            except Exception:
                return dict(pending_deletion_count=0)
        return dict(pending_deletion_count=0)

    # Register Audit Listeners
    from services.audit_service import register_audit_listeners
    with app.app_context():
        register_audit_listeners()

    # ── Keep-alive / health endpoints (no DB, fast) ───────────────────────────
    @app.route('/ping')
    def ping():
        return 'pong', 200

    @app.route('/health')
    def health():
        db_ok = True
        try:
            db.session.execute(text('SELECT 1'))
        except Exception:
            db.session.rollback()
            db_ok = False
        return jsonify(status='ok', db=db_ok), 200 if db_ok else 503

    # ── Global error handlers ─────────────────────────────────────────────────
    _RETRY_PAGE = """
    <!doctype html><html><head><title>Service Warming Up</title>
    <meta http-equiv="refresh" content="5">
    <style>body{font-family:Arial,sans-serif;text-align:center;padding:60px;background:#f7f9fc}
    .card{max-width:480px;margin:auto;background:#fff;padding:40px;border-radius:12px;
    box-shadow:0 4px 16px rgba(0,0,0,.08)}h2{color:#0d6efd}p{color:#555}
    .spinner{display:inline-block;width:36px;height:36px;border:4px solid #e3e3e3;
    border-top:4px solid #0d6efd;border-radius:50%;animation:spin 1s linear infinite;margin:16px}
    @keyframes spin{to{transform:rotate(360deg)}}</style></head>
    <body><div class="card"><div class="spinner"></div>
    <h2>Service Warming Up</h2>
    <p>The server is reconnecting. This page will refresh automatically in a few seconds.</p>
    <p><a href="/">Click here</a> if it does not refresh.</p></div></body></html>
    """

    @app.errorhandler(OperationalError)
    @app.errorhandler(DBAPIError)
    def handle_db_error(e):
        app.logger.warning(f"DB connection error on {request.path}: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return render_template_string(_RETRY_PAGE), 503

    @app.errorhandler(500)
    def handle_500(e):
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.error(f"500 on {request.path}: {e}\n{traceback.format_exc()}")
        return render_template_string(_RETRY_PAGE), 500

    @app.errorhandler(Exception)
    def handle_unhandled(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.error(f"Unhandled on {request.path}: {e}\n{traceback.format_exc()}")
        return render_template_string(_RETRY_PAGE), 500

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        if exception:
            try:
                db.session.rollback()
            except Exception:
                pass
        db.session.remove()

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
