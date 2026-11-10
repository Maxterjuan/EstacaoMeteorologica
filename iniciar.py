"""Assistente da estação: configura o firmware do ESP32, gera a chave e inicia o servidor.

Uso:  python iniciar.py            -> configura e inicia o servidor
      python iniciar.py --teste    -> envia uma leitura simulada (para ver o dashboard sem o ESP32)
"""
import json, os, re, secrets, socket, subprocess, sys, time, urllib.request, webbrowser

AQUI = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(AQUI, "config.json")
FW = os.path.join(AQUI, "firmware")


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # não envia nada; só descobre a interface de rede
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "192.168.0.10"


def perguntar(txt, padrao=""):
    r = input(txt + (f" [{padrao}]" if padrao else "") + ": ").strip()
    return r or padrao


def configurar():
    cfg = json.load(open(CFG, encoding="utf-8")) if os.path.exists(CFG) else {}
    print("\n== Configuração da estação (Enter mantém o valor entre colchetes) ==")
    cfg["ssid"] = perguntar("Nome do Wi-Fi (rede 2,4 GHz)", cfg.get("ssid", ""))
    cfg["senha"] = perguntar("Senha do Wi-Fi", cfg.get("senha", ""))
    cfg["ip"] = perguntar("IP deste computador", cfg.get("ip") or ip_local())
    cfg["lat"] = perguntar("Latitude da estação", cfg.get("lat", "-23.5505"))
    cfg["lon"] = perguntar("Longitude da estação", cfg.get("lon", "-46.6333"))
    cfg.setdefault("chave", secrets.token_urlsafe(16))
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), indent=2)

    troca = {
        "WIFI_SSID": json.dumps(cfg["ssid"]),
        "WIFI_PASS": json.dumps(cfg["senha"]),
        "API_URL": json.dumps(f"http://{cfg['ip']}:5000/api/leituras"),
        "API_KEY": json.dumps(cfg["chave"]),
    }
    s = open(os.path.join(FW, "main.py"), encoding="utf-8").read()
    for nome, valor in troca.items():
        s = re.sub(rf"^{nome} = .*$", lambda m: f"{nome} = {valor}", s, flags=re.M)
    s = re.sub(r"^LAT, LON = .*$", lambda m: f"LAT, LON = {float(cfg['lat'])}, {float(cfg['lon'])}", s, flags=re.M)
    saida = os.path.join(FW, "main_configurado.py")
    open(saida, "w", encoding="utf-8").write(s)

    print("\nFirmware pronto:", saida)
    print("No Thonny: abra esse arquivo e use Arquivo > Salvar como > dispositivo MicroPython, nome main.py.")
    print(f"Dashboard: http://{cfg['ip']}:5000  (se o Windows perguntar sobre o firewall, clique em Permitir)\n")
    return cfg


def servidor(cfg):
    try:
        import flask  # noqa: F401
    except ImportError:
        print("Instalando o Flask…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask"])
    p = subprocess.Popen([sys.executable, "app.py"], cwd=AQUI, env=dict(os.environ, API_KEY=cfg["chave"]))
    time.sleep(2)
    webbrowser.open("http://localhost:5000")
    print("Servidor rodando. Feche esta janela ou pressione Ctrl+C para parar.")
    try:
        p.wait()
    except KeyboardInterrupt:
        p.terminate()


def enviar_teste():
    cfg = json.load(open(CFG, encoding="utf-8"))
    dado = {"estacao_id": "EST01", "temperatura": 27.3, "umidade": 61, "pressao": 1011.8,
            "qualidade_ar": 1500, "chuva_nivel": 0, "luz_escuro": False,
            "latitude": float(cfg["lat"]), "longitude": float(cfg["lon"])}
    req = urllib.request.Request("http://localhost:5000/api/leituras", json.dumps(dado).encode(),
                                 {"Content-Type": "application/json", "X-API-KEY": cfg["chave"]})
    print("Resposta do servidor:", urllib.request.urlopen(req).status, "(201 = gravado)")


if __name__ == "__main__":
    if "--teste" in sys.argv:
        enviar_teste()
    else:
        c = configurar()
        if "--so-configurar" not in sys.argv:
            servidor(c)
