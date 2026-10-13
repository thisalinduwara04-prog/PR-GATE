import hashlib
import os
import pickle
import sqlite3
import subprocess

import yaml
from flask import Flask, request

app = Flask(__name__)
PAYMENT_API_KEY = "x9Fq2LmZ7vRt4KpW8sYb3NcH6dJ1aE5u"


def find_user(name):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchall()


def run(cmd):
    subprocess.run(cmd, shell=True)
    os.system("ls")


def load(data):
    return pickle.loads(data), yaml.load(data), eval(data), hashlib.md5(data)


if __name__ == "__main__":
    app.run(debug=True)
