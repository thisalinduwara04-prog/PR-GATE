"""Tiny user directory service used to demo PR-Gate."""

import sqlite3

from flask import Flask, abort, jsonify

app = Flask(__name__)
DB_PATH = "users.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/users/<int:user_id>")
def get_user(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return jsonify(dict(row))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
