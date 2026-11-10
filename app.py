"""API REST + banco SQLite da estação meteorológica.

Rotas:
  POST /api/leituras   recebe leitura do ESP32 (header X-API-KEY)
  GET  /api/leituras   médias por hora das últimas 24 h
  GET  /api/ultima     leitura mais recente
  GET  /api/resumo     média, máxima e mínima das últimas 24 h
"""
import os
import sqlite3
from flask import Flask, g, jsonify, request

DB = os.environ.get("DB_PATH", "estacao.db")
API_KEY = os.environ.get("API_KEY", "troque-esta-chave")

# Faixas válidas por campo (validação básica contra leituras absurdas)
FAIXAS = {
    "temperatura": (-40, 80),
    "umidade": (0, 100),
    "pressao": (300, 1100),
    "qualidade_ar": (0, 7000),  # mV crus do MQ-135 (não é ppm)
    "chuva_nivel": (0, 100),    # % do sensor de chuva
}
CAMPOS = list(FAIXAS)

app = Flask(__name__, static_folder="static", static_url_path="")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def fechar(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    with sqlite3.connect(DB) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS leituras (
                id_leitura   INTEGER PRIMARY KEY AUTOINCREMENT,
                estacao_id   VARCHAR(20) NOT NULL DEFAULT 'EST01',
                temperatura  REAL, umidade REAL, pressao REAL,
                qualidade_ar REAL, chuva_nivel REAL, luz_escuro INTEGER,
                latitude REAL, longitude REAL,
                data_hora    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        # Índice temporal: mantém o dashboard rápido (requisito < 2 s)
        c.execute("CREATE INDEX IF NOT EXISTS idx_data ON leituras(data_hora)")


def numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/leituras")
def gravar():
    if request.headers.get("X-API-KEY") != API_KEY:
        return jsonify(erro="chave inválida"), 401
    d = request.get_json(silent=True) or {}
    for campo, (lo, hi) in FAIXAS.items():
        v = d.get(campo)
        if not numero(v):
            return jsonify(erro=f"campo ausente ou inválido: {campo}"), 400
        if not lo <= v <= hi:
            return jsonify(erro=f"{campo} fora da faixa ({lo} a {hi})"), 400
    luz = d.get("luz_escuro")
    if luz not in (0, 1):  # aceita true/false ou 0/1
        return jsonify(erro="campo ausente ou inválido: luz_escuro"), 400
    lat, lon = d.get("latitude"), d.get("longitude")
    if (lat is not None and not numero(lat)) or (lon is not None and not numero(lon)):
        return jsonify(erro="latitude/longitude inválidas"), 400
    est = str(d.get("estacao_id", "EST01"))[:20]
    db().execute(
        """INSERT INTO leituras (estacao_id, temperatura, umidade, pressao,
           qualidade_ar, chuva_nivel, luz_escuro, latitude, longitude)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (est, *[d[c] for c in CAMPOS], int(luz), lat, lon),
    )
    db().commit()
    return jsonify(ok=True), 201


@app.get("/api/leituras")
def listar():
    # Temperatura/umidade/pressão/ar: média da hora. Chuva: nível máximo da hora.
    rows = db().execute(
        """SELECT strftime('%Y-%m-%dT%H:00:00Z', data_hora) AS hora,
                  AVG(temperatura) AS temperatura, AVG(umidade) AS umidade,
                  AVG(pressao) AS pressao, AVG(qualidade_ar) AS qualidade_ar,
                  MAX(chuva_nivel) AS chuva_nivel, AVG(luz_escuro) AS luz_escuro
           FROM leituras WHERE data_hora >= datetime('now','-24 hours')
           GROUP BY hora ORDER BY hora"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/ultima")
def ultima():
    r = db().execute(
        "SELECT *, strftime('%Y-%m-%dT%H:%M:%SZ', data_hora) AS data_iso "
        "FROM leituras ORDER BY id_leitura DESC LIMIT 1"
    ).fetchone()
    return jsonify(dict(r) if r else None)


@app.get("/api/resumo")
def resumo():
    sel = ", ".join(
        f"AVG({c}) AS {c}_media, MAX({c}) AS {c}_max, MIN({c}) AS {c}_min"
        for c in CAMPOS if c != "chuva_nivel"
    )
    r = db().execute(
        f"SELECT COUNT(*) AS n, {sel}, COALESCE(MAX(chuva_nivel),0) AS chuva_max "
        "FROM leituras WHERE data_hora >= datetime('now','-24 hours')"
    ).fetchone()
    return jsonify(dict(r))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
