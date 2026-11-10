# Estação ESP32 (MicroPython / Thonny) - versão compacta + envio Wi-Fi para o dashboard
# Pinos: DHT22=13, Chuva AO=32, Luz D0=33, MQ-135 AO=34 (divisor /2)
#        OLED: SCL=21, SDA=22 (fios trocados na montagem) | BMP280: SDA=25, SCL=26 (barramentos I2C separados)
from machine import Pin, I2C, SoftI2C, ADC
import dht, struct, time, math, framebuf, network, ujson
try:
    import urequests as requests
except ImportError:
    import requests

# ---------- CONFIGURAÇÕES ----------
ROTACIONAR_OLED = True     # False se a imagem ficar de cabeça para baixo
USAR_PRESSAO_FIXA = False  # True mostra PRESSAO_FIXA (para foto)
PRESSAO_FIXA = 1013.2      # hPa
FREQ_I2C = 100000          # 400000 deixa a animação mais fluida, se estiver estável
ZONA_MORTA_CHUVA = 300     # abaixo disso = "seco"
LUZ_1_E_ESCURO = False     # True se o D0 em 1 significar escuro
LIMITE_GAS_MV = 3000       # acima disso aparecem bolhas de "gás" (leitura crua, não é ppm)
TENTAR_DE_NOVO_MS = 10000  # reinicia OLED/BMP280 que não responderam

# --- envio ao dashboard ---
WIFI_SSID = "SUA_REDE"
WIFI_PASS = "SUA_SENHA"
API_URL = "http://192.168.0.10:5000/api/leituras"   # IP do computador/servidor
API_KEY = "troque-esta-chave"                        # igual à do servidor
ESTACAO_ID = "EST01"
LAT, LON = -23.5505, -46.6333                        # coordenadas reais da estação
INTERVALO_ENVIO_MS = 60000


# ---------- Wi-Fi e envio ----------
def conectar_wifi():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if not w.isconnected():
        w.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if w.isconnected():
                break
            time.sleep(1)
    print("Wi-Fi:", "conectado" if w.isconnected() else "sem conexao")
    return w.isconnected()


def enviar(d):
    try:
        r = requests.post(API_URL, data=ujson.dumps(d),
                          headers={"Content-Type": "application/json", "X-API-KEY": API_KEY})
        ok = r.status_code == 201
        if not ok:
            print("Servidor recusou:", r.status_code, r.text)
        r.close()
        return ok
    except Exception as e:
        print("Falha no envio:", e)
        return False


# ---------- dois barramentos I2C separados (hardware; se o scan vier vazio, software) ----------
def criar_bus(id_, scl, sda):
    b = I2C(id_, scl=Pin(scl), sda=Pin(sda), freq=FREQ_I2C)
    if not b.scan():
        b = SoftI2C(scl=Pin(scl), sda=Pin(sda), freq=FREQ_I2C)
    return b


i2c_oled = criar_bus(0, 21, 22)   # OLED:   SCL=21, SDA=22 (como está ligado na prática)
i2c_bmp = criar_bus(1, 26, 25)    # BMP280: SDA=25, SCL=26
print("OLED   (SCL 21 / SDA 22):", [hex(a) for a in i2c_oled.scan()], "| esperado 0x3c")
print("BMP280 (SDA 25 / SCL 26):", [hex(a) for a in i2c_bmp.scan()], "| esperado 0x76")


# ---------- BMP280 ----------
class BMP280:
    def __init__(self, i2c, addr=0x76):
        self.i2c, self.a = i2c, addr
        if i2c.readfrom_mem(addr, 0xD0, 1)[0] not in (0x56, 0x57, 0x58, 0x60):
            raise OSError("ID invalido")
        c = i2c.readfrom_mem(addr, 0x88, 24)
        self.T = struct.unpack('<Hhh', c[0:6])
        self.P = struct.unpack('<Hhhhhhhhh', c[6:24])
        i2c.writeto_mem(addr, 0xF4, b'\x27')
        i2c.writeto_mem(addr, 0xF5, b'\xA0')
        time.sleep_ms(100)

    def read(self):
        d = self.i2c.readfrom_mem(self.a, 0xF7, 6)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        t1, t2, t3 = self.T
        tf = (adc_t / 16384.0 - t1 / 1024.0) * t2 + ((adc_t / 131072.0 - t1 / 8192.0) ** 2) * t3
        p1, p2, p3, p4, p5, p6, p7, p8, p9 = self.P
        v1 = tf / 2.0 - 64000.0
        v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
        v2 = v2 / 4.0 + p4 * 65536.0
        v1 = (1.0 + (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0 / 32768.0) * p1
        if v1 == 0:
            return tf / 5120.0, 0.0
        p = (1048576.0 - adc_p - v2 / 4096.0) * 6250.0 / v1
        p += (p9 * p * p / 2147483648.0 + p * p8 / 32768.0 + p7) / 16.0
        return tf / 5120.0, p / 100.0   # °C, hPa


# ---------- OLED SSD1306 ----------
class SSD1306(framebuf.FrameBuffer):
    def __init__(self, i2c, addr=0x3C, w=128, h=64, girar=True):
        self.i2c, self.addr, self.w, self.pages = i2c, addr, w, h // 8
        self.buf = bytearray(self.pages * w)
        super().__init__(self.buf, w, h, framebuf.MONO_VLSB)
        for c in (0xAE, 0x20, 0x00, 0x40, 0xA0 if girar else 0xA1, 0xA8, h - 1,
                  0xC0 if girar else 0xC8, 0xD3, 0x00, 0xDA, 0x12, 0xD5, 0x80,
                  0xD9, 0xF1, 0xDB, 0x30, 0x81, 0xFF, 0xA4, 0xA6, 0x8D, 0x14, 0xAF):
            self.cmd(c)

    def cmd(self, c):
        self.i2c.writeto(self.addr, bytes([0x80, c]))

    def show(self):
        for c in (0x21, 0, self.w - 1, 0x22, 0, self.pages - 1):
            self.cmd(c)
        self.i2c.writeto(self.addr, b'\x40' + self.buf)


def iniciar_oled():
    try:
        return SSD1306(i2c_oled, 0x3C, 128, 64, ROTACIONAR_OLED)
    except Exception as e:
        print("OLED indisponivel:", e)


def iniciar_bmp():
    for a in (0x76, 0x77):
        try:
            b = BMP280(i2c_bmp, a)
            print("BMP280 ok em 0x%02X" % a)
            return b
        except Exception as e:
            print("BMP280 sem resposta em 0x%02X: %s" % (a, e))


oled = iniciar_oled()
bmp = iniciar_bmp()

sensor_dht = dht.DHT22(Pin(13))
adc_chuva = ADC(Pin(32))
adc_chuva.atten(ADC.ATTN_11DB)
adc_mq = ADC(Pin(34))
adc_mq.atten(ADC.ATTN_11DB)
luz = Pin(33, Pin.IN)


def mq_mV():
    """Média de 32 leituras; x2 por causa do divisor."""
    soma = 0
    for _ in range(32):
        try:
            soma += adc_mq.read_uv() / 1000
        except AttributeError:
            soma += adc_mq.read() / 4095 * 3100
        time.sleep_ms(2)
    return int(soma / 32 * 2)


# ---------- animação ----------
def disco(o, x, y, r, c=1):
    for dy in range(-r, r + 1):
        w = int(math.sqrt(r * r - dy * dy))
        o.hline(x - w, y + dy, 2 * w + 1, c)


ESCALAS = (0.75, 1.0, 1.25)
BOLHAS_NUVEM = ((-9, 4, 6), (-1, 0, 8), (8, 4, 6))
OFFS_CHUVA = (-12, -6, 0, 6, 12, -9, 3, 9)
ESTRELAS = ((88, 3), (97, 9), (108, 2), (126, 16))
RAIOS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))


def nuvem(o, cx, cy, e):
    for dx, dy, r in BOLHAS_NUVEM:
        disco(o, cx + int(dx * e), cy + int(dy * e), int(r * e))
    o.fill_rect(cx + int(-9 * e), cy + int(4 * e), int(17 * e), int(6 * e), 1)


def desenhar(f, t, umid, pres, chuva, chuva_txt, escuro, mq):
    o = oled
    o.fill(0)
    o.text("T erro" if t is None else "T %.1fC" % t, 0, 0)
    o.text("U erro" if umid is None else "U %d%%" % umid, 0, 8)
    o.text("P --" if pres is None else "P %dhPa" % pres, 0, 16)
    o.text("Chuva", 0, 24)
    o.text(chuva_txt, 0, 32)
    o.text("Luz Escuro" if escuro else "Luz Claro", 0, 40)
    o.text("MQ %dmV" % mq, 0, 48)

    # nuvem muda de tamanho com a umidade
    e = ESCALAS[1 if umid is None or 50 <= umid < 80 else 0 if umid < 50 else 2]
    cx, cy = 105 + abs((f // 2) % 8 - 4) - 2, 22

    if escuro:                              # lua e estrelas
        disco(o, 120, 7, 4)
        disco(o, 122, 6, 3, 0)
        for i, (x, y) in enumerate(ESTRELAS):
            if (f // 4 + i) % 3:
                o.pixel(x, y, 1)
    elif chuva == 0:                        # sol
        disco(o, 120, 7, 3)
        r2 = 7 if (f // 3) % 2 else 6
        for a, b in RAIOS:
            o.line(120 + a * 5, 7 + b * 5, 120 + a * r2, 7 + b * r2, 1)

    nuvem(o, cx, cy, e)

    if chuva > 0:                           # mais gotas quanto mais molhado
        base = cy + int(10 * e) + 2
        span = 62 - base
        for i in range(1 + min(7, chuva * 7 // 4095)):
            o.vline(cx + OFFS_CHUVA[i], base + (f * 3 + i * 5) % span, 3, 1)

    if mq > LIMITE_GAS_MV:                  # bolhas de gás
        for i in range(4):
            o.fill_rect(88 + i * 10, 62 - (f * 2 + i * 8) % 24, 2, 2, 1)

    o.show()


# ---------- laço principal ----------
conectar_wifi()
t_dht = umid = t_bmp = pres = None
chuva, chuva_txt, estado_luz, mq, frame = 0, "0 (seco)", 0, 0, 0
escuro_atual = False
ultima_leitura = time.ticks_add(time.ticks_ms(), -2000)
ultima_tentativa = time.ticks_ms()
ultimo_envio = time.ticks_ms()

while True:
    agora = time.ticks_ms()

    # se OLED ou BMP280 falhar, tenta reiniciar sem derrubar o outro
    if (oled is None or bmp is None) and time.ticks_diff(agora, ultima_tentativa) >= TENTAR_DE_NOVO_MS:
        ultima_tentativa = agora
        oled = oled or iniciar_oled()
        bmp = bmp or iniciar_bmp()

    # sensores a cada 2 s (limite do DHT22)
    if time.ticks_diff(agora, ultima_leitura) >= 2000:
        ultima_leitura = agora
        try:
            sensor_dht.measure()
            t_dht, umid = sensor_dht.temperature(), round(sensor_dht.humidity())   # umidade inteira
        except Exception:
            t_dht = umid = None

        t_bmp = pres = None
        if bmp:
            try:
                t_bmp, pres = bmp.read()
            except Exception as e:
                print("Erro lendo o BMP280:", e)
                bmp = None
        if USAR_PRESSAO_FIXA:
            pres = PRESSAO_FIXA

        chuva = 4095 - adc_chuva.read()     # 0 = seco, sobe ao molhar
        if chuva < ZONA_MORTA_CHUVA:
            chuva, chuva_txt = 0, "0 (seco)"
        else:
            chuva_txt = str(chuva)

        estado_luz = luz.value()
        escuro_atual = (estado_luz == 1) if LUZ_1_E_ESCURO else (estado_luz == 0)
        mq = mq_mV()                        # leitura crua, não é ppm

        print("DHT:", t_dht, "C", umid, "% | BMP:",
              None if t_bmp is None else round(t_bmp, 1), "C",
              None if pres is None else round(pres, 1), "hPa",
              "| Chuva:", chuva_txt, "| Luz:", estado_luz, "| MQ-135:", mq, "mV")

    # envio ao dashboard a cada INTERVALO_ENVIO_MS (só com leituras completas)
    if time.ticks_diff(agora, ultimo_envio) >= INTERVALO_ENVIO_MS:
        temp = t_dht if t_dht is not None else t_bmp
        if temp is not None and umid is not None and pres is not None:
            ultimo_envio = agora
            if not network.WLAN(network.STA_IF).isconnected():
                conectar_wifi()
            enviar({
                "estacao_id": ESTACAO_ID,
                "temperatura": round(temp, 1),
                "umidade": umid,
                "pressao": round(pres, 1),
                "qualidade_ar": mq,                          # mV crus
                "chuva_nivel": round(chuva / 4095 * 100),    # 0 a 100 %
                "luz_escuro": escuro_atual,
                "latitude": LAT, "longitude": LON,
            })

    if oled:
        try:
            desenhar(frame, t_dht if t_dht is not None else t_bmp, umid, pres,
                     chuva, chuva_txt, escuro_atual, mq)
        except Exception as e:
            print("Erro no OLED:", e)
            oled = None

    frame += 1
    time.sleep_ms(20)
