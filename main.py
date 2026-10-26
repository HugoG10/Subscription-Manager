"""Run the Subscription Manager server.

Subscriptions are stored in MongoDB Atlas. Put the connection string in
.env as MONGODB_URI, then from the project folder:

    python main.py

Open http://127.0.0.1:8000
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from model import ValidationError, subscription_from_payload, summarize
from store import DEFAULT_DATABASE, DEFAULT_URI, SubscriptionStore

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
ID_MAX_LENGTH = 64


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def create_app(
    mongo_uri: str | None = None,
    database: str | None = None,
    *,
    connect: bool = True,
) -> Flask:
    load_env()
    app = Flask(__name__)
    store = SubscriptionStore(
        mongo_uri or os.environ.get("MONGODB_URI", DEFAULT_URI),
        database or os.environ.get("MONGODB_DB", DEFAULT_DATABASE),
    )
    app.store = store
    if connect:
        store.initialize()
    else:
        # Vercel imports this module at build time, before a request exists.
        # Connect on the first request so a missing database does not fail the build.
        ready = False

        @app.before_request
        def connect_database():
            nonlocal ready
            if ready:
                return None
            store.initialize()
            ready = True
            return None

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/css/<path:filename>")
    def styles(filename):
        return send_from_directory(FRONTEND / "css", filename)

    @app.get("/js/<path:filename>")
    def scripts(filename):
        return send_from_directory(FRONTEND / "js", filename)

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/subscriptions")
    def list_subscriptions():
        return jsonify([item.to_json() for item in store.list_subscriptions()])

    @app.get("/api/summary")
    def summary():
        return jsonify(summarize(store.list_subscriptions()))

    @app.post("/api/subscriptions")
    def create_subscription():
        try:
            subscription = subscription_from_payload(request.get_json(silent=True))
        except ValidationError as error:
            return jsonify({"errors": error.errors}), 400
        saved = store.add(subscription)
        return jsonify(saved.to_json()), 201

    @app.put("/api/subscriptions/<subscription_id>")
    def update_subscription(subscription_id):
        if not _valid_id(subscription_id):
            return jsonify({"errors": ["That subscription was not found."]}), 404
        if store.get(subscription_id) is None:
            return jsonify({"errors": ["That subscription was not found."]}), 404
        try:
            subscription = subscription_from_payload(
                request.get_json(silent=True),
                subscription_id,
            )
        except ValidationError as error:
            return jsonify({"errors": error.errors}), 400
        saved = store.update(subscription)
        return jsonify(saved.to_json())

    @app.delete("/api/subscriptions/<subscription_id>")
    def delete_subscription(subscription_id):
        if not _valid_id(subscription_id) or not store.delete(subscription_id):
            return jsonify({"errors": ["That subscription was not found."]}), 404
        return "", 204

    @app.delete("/api/subscriptions")
    def delete_all():
        store.delete_all()
        return jsonify([])

    @app.post("/api/subscriptions/sample")
    def load_sample():
        subscriptions = store.replace_with_sample()
        return jsonify([item.to_json() for item in subscriptions])

    return app


def _valid_id(subscription_id: str) -> bool:
    if not subscription_id or len(subscription_id) > ID_MAX_LENGTH:
        return False
    return all(character.isalnum() or character in "-_" for character in subscription_id)


# Vercel loads this Flask instance. Local `python main.py` builds its own.
app = create_app(connect=False)


def main() -> None:
    print("Subscription Manager running at http://127.0.0.1:8000")
    create_app().run(host="127.0.0.1", port=8000, debug=True)


if __name__ == "__main__":
    main()
