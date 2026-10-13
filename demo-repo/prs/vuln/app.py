"""Tiny user directory service used to demo PR-Gate."""

import hashlib
import sqlite3
import subprocess

import yaml
from flask import Flask, abort, jsonify, request

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


@app.get("/users/search")
def search_users():
    name = request.args.get("name", "")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, name, email FROM users WHERE name LIKE '%{name}%'")
        rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/admin/import")
def import_users():
    config = yaml.load(request.data, Loader=yaml.Loader)
    return jsonify({"imported": len(config.get("users", []))})


@app.get("/version")
def version():
    # Fixed command, no user input.
    out = subprocess.run("git --version", shell=True, capture_output=True, text=True)
    etag = hashlib.md5(out.stdout.encode()).hexdigest()
    return {"git": out.stdout.strip(), "etag": etag}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
