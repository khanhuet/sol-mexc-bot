#!/usr/bin/env python3
"""
BOT V11.0 - CANH BAO CHENH LECH GIA CEX <-> DEX (PAPER / CHI BAO, KHONG TU DONG GIAO DICH)

CEX : MEXC, GATE, KUCOIN, BITGET, BYBIT, BINANCE, OKX, BINGX, HTX, LBANK
DEX : BNB, SOL, BASE, ETH, POLYGON, ARBITRUM, AVAX, HYPEREVM, ROBINHOOD, ARC, NEAR

Quy trinh moi token:
 1. Lay danh sach token + mang + contract + trang thai nap/rut tu tung CEX
 2. Tim pool DEX dung contract (DexScreener) -> loc thanh khoan / volume / tuoi pool
 3. Kiem tra contract: CEX cong bo == DEX, symbol khop, gia khong lech bat thuong
 4. Kiem tra tax (buy / sell / transfer) + honeypot (honeypot.is, GoPlus, RPC Solana)
 5. Size dong 200-2000$: thu nhieu muc size, quote THAT tren DEX aggregator
    (KyberSwap / Jupiter / Ref) va an THAT vao so lenh CEX -> chon size lai $ cao nhat
 6. Lai rong (sau phi CEX, phi rut, gas, tax, truot gia) >= MIN_NET_PCT (mac dinh 2%)
 7. Xac nhan lai sau vai giay -> bao Telegram day du
Bao cao: chi 1 lan/ngay luc 21:00 (UTC+7). Canh bao loi he thong: toi da 1 lan / 6h moi loai.
"""
import gc, base64, hashlib, hmac, html, json, logging, math, os, re, sqlite3, sys, threading, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, quote as urlquote
from zoneinfo import ZoneInfo
import requests

VERSION = "V11.7"
try: VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception: VN_TZ = timezone(timedelta(hours=7))

def env_str(name, default=""):
    v = os.getenv(name, default).strip().strip('"').strip("'").strip()
    if v.upper().startswith(name.upper() + "="): v = v[len(name) + 1:].strip().strip('"').strip("'")   # loi dan "NAME=..." vao o gia tri
    return v
def env_float(name, default): return float(env_str(name, str(default)) or default)
def env_int(name, default): return int(float(env_str(name, str(default)) or default))
def env_bool(name, default="1"): return env_str(name, default).lower() in ("1", "true", "yes", "on")
def _missing_env_server(missing):
    """Thieu env bat buoc: KHONG thoat (tranh Render bao 'No open ports' + vong khoi dong lai),
    mo cong PORT tra ve thong bao loi va ghi log moi 60s cho den khi duoc cau hinh."""
    import http.server, socketserver
    msg = f"BOT NOT RUNNING - missing environment variables: {', '.join(missing)}. Add them in Render > Environment and Save."
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(msg.encode())
        def log_message(self, *a): pass
    def nag():
        while True: print("[CONFIG] " + msg, flush=True); time.sleep(60)
    threading.Thread(target=nag, daemon=True).start()
    socketserver.TCPServer.allow_reuse_address = True
    socketserver.TCPServer(("0.0.0.0", int(os.getenv("PORT", "10000"))), H).serve_forever()
def require_env(name):
    v = os.environ.get(name, "").strip().strip('"').strip("'")
    if not v:
        missing = [n for n in ("TELEGRAM_TOKEN", "CHAT_ID") if not os.environ.get(n, "").strip()]
        _missing_env_server(missing or [name])
    return v

TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
CHAT_ID = str(require_env("CHAT_ID"))

ALLOWED_CEXS = ("MEXC","GATE","KUCOIN","BITGET","BYBIT","BINANCE","OKX","BINGX","HTX","LBANK")
ENABLED_CEXS = [c for c in (x.strip().strip('"').upper() for x in env_str("ENABLED_CEXS", ",".join(ALLOWED_CEXS)).split(",")) if c in ALLOWED_CEXS]
if not ENABLED_CEXS: sys.exit(f"ENABLED_CEXS phai co it nhat 1 trong {ALLOWED_CEXS}")

KEYS = {
    "MEXC":   (env_str("MEXC_API_KEY"), env_str("MEXC_SECRET"), ""),
    "GATE":   (env_str("GATE_API_KEY"), env_str("GATE_SECRET"), ""),
    "BYBIT":  (env_str("BYBIT_API_KEY"), env_str("BYBIT_SECRET"), ""),
    "BINANCE":(env_str("BINANCE_API_KEY"), env_str("BINANCE_SECRET"), ""),
    "OKX":    (env_str("OKX_API_KEY"), env_str("OKX_SECRET"), env_str("OKX_PASSPHRASE")),
    "BINGX":  (env_str("BINGX_API_KEY"), env_str("BINGX_SECRET"), ""),
}
def has_key(cex):
    k = KEYS.get(cex)
    if not k: return False
    return bool(k[0] and k[1] and (k[2] or cex != "OKX"))

# ---------------- Nguong & bo loc ----------------
PORT = env_int("PORT", 10000); DB_PATH = env_str("DB_PATH", "arb_v11.db"); ADMIN_TOKEN = env_str("ADMIN_TOKEN")
MIN_NET_PCT = env_float("MIN_NET_PCT", 2.0)          # lai rong toi thieu (%), sau MOI chi phi
MIN_NET_USD = env_float("MIN_NET_USD", 3.0)          # lai rong toi thieu ($) - $200 x 2% = $4 nen de 3$
ORDER_SIZE_MIN = env_float("ORDER_SIZE_MIN", 200); ORDER_SIZE_MAX = env_float("ORDER_SIZE_MAX", 2000)
SIZE_STEPS = sorted({float(x) for x in env_str("SIZE_STEPS", "200,300,500,750,1000,1500,2000").split(",") if x.strip()})
SIZE_LIQ_MAX_FRAC = env_float("SIZE_LIQ_MAX_FRAC", 0.02)   # size <= 2% tong thanh khoan DEX (cong tat ca DEX)
SIZE_MODE = env_str("SIZE_MODE", "pct").lower()            # pct = uu tien % lai cao nhat | usd = uu tien $ lai cao nhat
MIN_POOL_LIQ_EACH = env_float("MIN_POOL_LIQ_EACH", 5000)   # pool tung DEX phai >= $5k moi duoc tinh
MULTI_AGG = env_bool("MULTI_AGG", "1")                     # EVM: quote ca KyberSwap + ParaSwap, lay route tot nhat
MAX_SIZE_QUOTES = env_int("MAX_SIZE_QUOTES", 5)            # so lan quote toi da khi do size
MAX_IMPACT_PCT = env_float("MAX_IMPACT_PCT", 3.0)          # impact DEX toi da
MAX_CEX_SLIP_PCT = env_float("MAX_CEX_SLIP_PCT", 3.0)      # truot gia toi da khi an so lenh CEX
MAX_CEX_SPREAD = env_float("MAX_CEX_SPREAD", 3.0)          # spread bid/ask CEX toi da
MAX_PRICE_DEV = env_float("MAX_PRICE_DEV", 0.40)           # gia CEX lech DEX > 40% -> coi la token khac / loi
MAX_PRICE_DEV_INFERRED = env_float("MAX_PRICE_DEV_INFERRED", 0.10)
WARN_GROSS = env_float("WARN_GROSS", 10.0)                 # chenh > 10% -> them canh bao
MIN_LIQ_USD = env_float("MIN_LIQ_USD", 30000); MIN_VOL_24H = env_float("MIN_VOL_24H", 20000)
MIN_TXNS_H1 = env_int("MIN_TXNS_H1", 5); MIN_POOL_AGE_H = env_float("MIN_POOL_AGE_H", 6)
TAX_MAX = env_float("TAX_MAX", 0.10)                        # tax > 10% -> bo
UNKNOWN_TAX_MODE = env_str("UNKNOWN_TAX_MODE", "assume").lower()   # assume | skip (chi ap dung cho mang CO nguon tax nhung tra cuu that bai)
NO_SOURCE_TAX = env_float("NO_SOURCE_TAX", 0.0)            # mang CHUA co nguon kiem tra tax (Robinhood, HyperEVM, Arc...) -> mac dinh 0%
UNKNOWN_TAX_ASSUME = env_float("UNKNOWN_TAX_ASSUME", 0.03)  # gia dinh 3% mua + 3% ban neu khong xac minh duoc
EXTRA_SLIPPAGE = env_float("EXTRA_SLIPPAGE", 0.003)         # du phong truot gia khi thuc hien that
ALLOW_INFERRED_CONTRACT = env_bool("ALLOW_INFERRED_CONTRACT", "1")  # ghep san khong cong bo contract theo symbol (chi khi duy nhat)
ALLOW_UNVERIFIED_STATUS = env_bool("ALLOW_UNVERIFIED_STATUS", "0")  # cho phep trang thai nap/rut chua xac minh
CONFIRM_DELAY = env_float("CONFIRM_DELAY", 2.0); COOLDOWN_MIN = env_float("COOLDOWN_MIN", 30)
SCAN_PAUSE_SEC = env_float("SCAN_PAUSE_SEC", 20); STATIC_TTL = env_float("STATIC_TTL", 300); STALE_MAX = env_float("STALE_MAX", 1500)
REFRESH_WORKERS = env_int("REFRESH_WORKERS", 2)   # tai du lieu 2 san cung luc (thay vi 10) -> giam dinh RAM
STATUS_CONFIRM = env_int("STATUS_CONFIRM", 2)               # trang thai nap/rut phai giu 2 lan lam moi lien tiep moi bao
STATUS_NOTIFY = env_bool("STATUS_NOTIFY", "1"); STATUS_MAX_LINES = env_int("STATUS_MAX_LINES", 40)
STUCK_SEC = env_float("STUCK_SEC", 900); BOOK_TTL = 10; DEX_BATCH = 30
DAILY_REPORT_HOUR = env_int("DAILY_REPORT_HOUR", 21)
ERROR_ALERTS = env_bool("ERROR_ALERTS", "0"); ERROR_ALERT_EVERY_H = env_float("ERROR_ALERT_EVERY_H", 6)   # mac dinh TAT: loi chi hien trong /status va Daily Report
EXCLUDE_SYMBOLS = {s.strip().upper() for s in env_str("EXCLUDE_SYMBOLS", "USDT,USDC,USDE,DAI,FDUSD,TUSD,BUSD,PYUSD,USD1,USDD,USDP,EURC,USDS,USDT0").split(",") if s.strip()}
BAD_STREAK_BLACKLIST = env_int("BAD_STREAK_BLACKLIST", 3); BLACKLIST_HOURS = env_float("BLACKLIST_HOURS", 24)
FOLLOWUP_MIN = (5, 15)
KEEPALIVE = env_bool("KEEPALIVE", "1")       # tu ping URL Render moi 10 phut de goi Free khong ngu
OBSOLETE_ENV = [k for k in ("THRESHOLD_NET","PREFILTER_GROSS","MAX_GROSS","AI_SCORE_MIN","ADAPTIVE","MAKER_DEPTH_MULTIPLIER","MEXC_MAKER_FEE","ORDER_SIZE_USD",
                            "REAL_PRICE_CHECK","REQUIRE_REAL_PRICE","RISK_K","VOLATILE_CHG_THRESHOLD","DEX_BATCH","HEARTBEAT_MIN") if os.getenv(k) is not None]

TAKER_FEE = {c: env_float(f"{c}_TAKER_FEE", d) for c, d in {"MEXC":0.0005,"GATE":0.002,"KUCOIN":0.001,"BITGET":0.001,"BYBIT":0.001,"BINANCE":0.001,"OKX":0.001,"BINGX":0.001,"HTX":0.002,"LBANK":0.001}.items()}
CEX_EXTRA_MIN = {"MEXC":3,"GATE":5,"KUCOIN":5,"BITGET":5,"BYBIT":5,"BINANCE":3,"OKX":5,"BINGX":5,"HTX":8,"LBANK":8}

KYBER_URL = env_str("KYBER_URL", "https://aggregator-api.kyberswap.com"); KYBER_CLIENT_ID = env_str("KYBER_CLIENT_ID", "cexdex-alert-bot")
JUPITER_API_KEY = env_str("JUPITER_API_KEY"); JUPITER_LITE_URL = env_str("JUPITER_LITE_URL", "https://lite-api.jup.ag/swap/v1/quote")
JUPITER_PRO_URL = env_str("JUPITER_PRO_URL", "https://api.jup.ag/swap/v1/quote")
PARASWAP_URL = env_str("PARASWAP_URL", "https://api.paraswap.io/prices")
REF_ROUTER_URL = env_str("REF_ROUTER_URL", "https://smartrouter.ref.finance/findPath")
HONEYPOT_URL = env_str("HONEYPOT_URL", "https://api.honeypot.is/v2/IsHoneypot")
GOPLUS_URL = env_str("GOPLUS_URL", "https://api.gopluslabs.io/api/v1/token_security")
NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
WSOL = "So11111111111111111111111111111111111111112"; SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
NEAR_USDC = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"

# ---------------- Mang ----------------
# stable: (dia chi, decimals) de quote theo USD. None -> quote bang native (ETH) va quy doi USD qua KyberSwap.
def _stable(env_name, addr, dec):
    v = env_str(env_name)
    if v:
        a, _, d = v.partition(":")
        return (a.strip(), int(d or 6))
    return (addr, dec) if addr else None

CHAINS = {
    "SOL":      dict(kind="sol",  emoji="🟣", name="Solana",   dex="solana",   explorer="https://solscan.io/token/", gas_usd=0.01, transfer_min=2,
                     aliases={"SOL","SOLANA","SPL","SOLSPL","SOLANASPL","SOLMAINNET","SOLANAMAINNET","SOLSOL"}),
    "BNB":      dict(kind="evm",  emoji="🟡", name="BNB Chain", dex="bsc",      chain_id=56,    kyber="bsc",       stable=_stable("STABLE_BNB","0x55d398326f99059fF775485246999027B3197955",18),
                     rpc=env_str("RPC_BNB","https://bsc-dataseed.bnbchain.org"), explorer="https://bscscan.com/token/", gas_usd=0.10, transfer_min=3,
                     aliases={"BSC","BEP20","BSCBEP20","BEP20BSC","BNBSMARTCHAIN","BNBSMARTCHAINBEP20","BNBBEP20","BEP20BNB","BSCBNB","BNBCHAIN","BNBSC"}),
    "BASE":     dict(kind="evm",  emoji="🔵", name="Base",      dex="base",     chain_id=8453,  kyber="base",      stable=_stable("STABLE_BASE","0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",6),
                     rpc=env_str("RPC_BASE","https://mainnet.base.org"), explorer="https://basescan.org/token/", gas_usd=0.03, transfer_min=5,
                     aliases={"BASE","BASECHAIN","BASEMAINNET","BASEETH","BASEEVM","BASENETWORK"}),
    "ETH":      dict(kind="evm",  emoji="⚫", name="Ethereum",  dex="ethereum", chain_id=1,     kyber="ethereum",  stable=_stable("STABLE_ETH","0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",6),
                     rpc=env_str("RPC_ETH","https://ethereum-rpc.publicnode.com"), explorer="https://etherscan.io/token/", gas_usd=2.0, transfer_min=8,
                     aliases={"ERC20","ETH","ETHEREUM","ETHEREUMERC20","ETHERC20","ERC20ETH","ETHEREUMETH","ETHMAINNET","ERC20ETHEREUM"}),
    "POLYGON":  dict(kind="evm",  emoji="💜", name="Polygon",   dex="polygon",  chain_id=137,   kyber="polygon",   stable=_stable("STABLE_POLYGON","0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",6),
                     rpc=env_str("RPC_POLYGON","https://polygon-rpc.com"), explorer="https://polygonscan.com/token/", gas_usd=0.02, transfer_min=6,
                     aliases={"MATIC","POLYGON","POLYGONMATIC","MATICPOLYGON","POLYGONPOS","POSPOLYGON","POLYGONMAINNET","POL","POLYGONPOL","PLASMA_NO"}),
    "ARBITRUM": dict(kind="evm",  emoji="🔷", name="Arbitrum",  dex="arbitrum", chain_id=42161, kyber="arbitrum",  stable=_stable("STABLE_ARBITRUM","0xaf88d065e77c8cC2239327C5EDb3A432268e5831",6),
                     rpc=env_str("RPC_ARBITRUM","https://arb1.arbitrum.io/rpc"), explorer="https://arbiscan.io/token/", gas_usd=0.05, transfer_min=4,
                     aliases={"ARB","ARBITRUM","ARBITRUMONE","ARBONE","ARBITRUMONEARB","ARBITRUMONEETH","ARBITRUMMAINNET","ARBEVM"}),
    "AVAX":     dict(kind="evm",  emoji="🔺", name="Avalanche C", dex="avalanche", chain_id=43114, kyber="avalanche", stable=_stable("STABLE_AVAX","0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",6),
                     rpc=env_str("RPC_AVAX","https://api.avax.network/ext/bc/C/rpc"), explorer="https://snowtrace.io/token/", gas_usd=0.05, transfer_min=3,
                     aliases={"AVAXC","AVAXCCHAIN","CCHAIN","AVALANCHECCHAIN","AVALANCHEC","CAVAX","AVAXC20","AVAXCHAIN","AVALANCHE","AVAX"}),
    "HYPEREVM": dict(kind="evm",  emoji="🟢", name="HyperEVM",  dex="hyperevm", chain_id=999,   kyber="hyperevm",  stable=_stable("STABLE_HYPEREVM","0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb",6),
                     rpc=env_str("RPC_HYPEREVM","https://rpc.hyperliquid.xyz/evm"), explorer="https://hyperevmscan.io/token/", gas_usd=0.05, transfer_min=3,
                     aliases={"HYPEREVM","HYPERLIQUIDEVM","HYPEEVM","HYPEREVMHYPE","HYPERVM"}),
    "ROBINHOOD":dict(kind="evm",  emoji="🪶", name="Robinhood Chain", dex="robinhood", chain_id=4663, kyber="robinhood", stable=_stable("STABLE_ROBINHOOD","",6),
                     rpc=env_str("RPC_ROBINHOOD"), blockscout=env_str("BLOCKSCOUT_ROBINHOOD","https://robinhoodchain.blockscout.com"),
                     explorer="https://robinhoodchain.blockscout.com/token/", gas_usd=0.03, transfer_min=5,
                     aliases={"ROBINHOOD","ROBINHOODCHAIN","ROBINHOODL2","HOODCHAIN","RHCHAIN"}),
    "ARC":      dict(kind="evm",  emoji="⭕", name="Arc",       dex="arc",      chain_id=5042,  kyber="arc",       stable=_stable("STABLE_ARC","0x3600000000000000000000000000000000000000",6),
                     rpc=env_str("RPC_ARC"), blockscout=env_str("BLOCKSCOUT_ARC"), explorer=env_str("EXPLORER_ARC","https://arcscan.app/token/"), gas_usd=0.02, transfer_min=3,
                     aliases={"ARC","ARCMAINNET","ARCNETWORK","CIRCLEARC","ARCCHAIN","ARCL1"}),
    "NEAR":     dict(kind="near", emoji="🌐", name="NEAR",      dex="near", rpc=env_str("RPC_NEAR","https://rpc.mainnet.fastnear.com"),
                     explorer="https://nearblocks.io/token/", gas_usd=0.01, transfer_min=2,
                     aliases={"NEAR","NEARPROTOCOL","NEP141","NEARNEP141","NEARMAINNET"}),
}
SOL_RPC = env_str("SOL_RPC", "https://api.mainnet-beta.solana.com")
PARASWAP_CHAINS = {1, 56, 137, 42161, 8453, 43114}
GOPLUS_CHAINS = {int(x) for x in env_str("GOPLUS_CHAINS", "1,56,137,42161,8453,43114").split(",") if x.strip()}
HONEYPOT_CHAINS = {int(x) for x in env_str("HONEYPOT_CHAINS", "1,56,8453").split(",") if x.strip()}

for _k, _c in CHAINS.items():
    _c["dex"] = env_str(f"DEX_CHAIN_{_k}", _c["dex"])
    _c["gas_usd"] = env_float(f"GAS_{_k}", _c["gas_usd"]); _c["transfer_min"] = env_float(f"TRANSFER_MIN_{_k}", _c["transfer_min"])
    _c["aliases"].discard("PLASMA_NO")
# Them alias tu env: EXTRA_ALIASES="AVAX:AVAXC1,AVAX2;HYPEREVM:HYPE"
for _part in env_str("EXTRA_ALIASES").split(";"):
    if ":" in _part:
        _ch, _al = _part.split(":", 1); _ch = _ch.strip().upper()
        if _ch in CHAINS: CHAINS[_ch]["aliases"] |= {re.sub(r"[^A-Z0-9]", "", a.upper()) for a in _al.split(",") if a.strip()}
_enabled = [c.strip().upper() for c in env_str("ENABLED_CHAINS", ",".join(CHAINS)).split(",") if c.strip()]
CHAINS = {k: v for k, v in CHAINS.items() if k in _enabled}
if not CHAINS: sys.exit("ENABLED_CHAINS rong")
ALIAS_INDEX = {}
for _k, _c in CHAINS.items():
    for _a in _c["aliases"]: ALIAS_INDEX.setdefault(_a, _k)

logging.basicConfig(level=env_str("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
log = logging.getLogger("bot")

# ============================ HTTP & TIEN ICH ============================
GLOBAL_SEM = threading.Semaphore(20)
_local = threading.local()
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "application/json"}
def session():
    s = getattr(_local, "s", None)
    if s is None: s = _local.s = requests.Session()
    return s
class RateLimiter:
    def __init__(self, mi): self.mi = mi; self.next = 0.0; self.lock = threading.Lock()
    def wait(self):
        with self.lock:
            now = time.monotonic(); slot = max(now, self.next); self.next = slot + self.mi
        d = slot - now
        if d > 0: time.sleep(d)
LIMITERS = {n: RateLimiter(0.12) for n in ALLOWED_CEXS}
DEX_LIMITER = RateLimiter(env_float("DEX_LIMIT_SEC", 0.25)); KYBER_LIMITER = RateLimiter(env_float("KYBER_LIMIT_SEC", 0.25))
JUP_LIMITER = RateLimiter(env_float("JUP_LIMIT_SEC", 1.05)); PARA_LIMITER = RateLimiter(0.5); REF_LIMITER = RateLimiter(0.3)
SEC_LIMITER = RateLimiter(0.5); RPC_LIMITER = RateLimiter(0.1); TG_LIMITER = RateLimiter(1.1)
BACKOFF = {}; BACKOFF_LOCK = threading.Lock()   # host -> thoi diem het backoff (sau 429)

def _host(url): return url.split("/")[2] if "://" in url else url
def http_req(method, url, params=None, headers=None, json_body=None, limiter=None, timeout=15, retries=2):
    host = _host(url)
    with BACKOFF_LOCK: until = BACKOFF.get(host, 0)
    if time.time() < until: return None
    h = dict(UA)
    if headers: h.update(headers)
    for attempt in range(retries + 1):
        try:
            if limiter: limiter.wait()
            with GLOBAL_SEM:
                r = session().request(method, url, params=params, headers=h, json=json_body, timeout=timeout)
            if r.status_code == 418:     # Binance: IP bi cam tam thoi
                try: wait = float(r.headers.get("Retry-After", 1800))
                except Exception: wait = 1800
                with BACKOFF_LOCK: BACKOFF[host] = time.time() + min(max(wait, 300), 7200)
                log.warning("418 tu %s -> IP bi cam tam thoi, tam dung %.0f phut", host, min(max(wait, 300), 7200) / 60); return r
            if r.status_code == 429:
                try: wait = float(r.headers.get("Retry-After", 10))
                except Exception: wait = 10
                wait = min(max(wait, 3), 60)
                with BACKOFF_LOCK: BACKOFF[host] = time.time() + wait
                log.warning("429 tu %s -> tam dung %.0fs", host, wait); return None
            if r.status_code >= 500 and attempt < retries: time.sleep(1 + attempt); continue
            return r
        except requests.RequestException as e:
            log.debug("HTTP loi %s: %s", url.split("?")[0], e)
            if attempt < retries: time.sleep(1 + attempt)
    return None
def http_get(url, params=None, headers=None, limiter=None, timeout=15, retries=2):
    return http_req("GET", url, params=params, headers=headers, limiter=limiter, timeout=timeout, retries=retries)
def rpc_post(url, body, timeout=8):
    if not url: return None
    r = http_req("POST", url, json_body=body, limiter=RPC_LIMITER, timeout=timeout, retries=1)
    if r is not None and r.status_code == 200:
        try: return r.json()
        except Exception: return None
    return None
def jget(r, *path, default=None):
    """r: Response -> json -> lay theo path an toan."""
    if r is None or r.status_code != 200: return default
    try: d = r.json()
    except Exception: return default
    for p in path:
        if isinstance(d, dict): d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d): d = d[p]
        else: return default
        if d is None: return default
    return d
def http_err(r, what):
    if r is None: return RuntimeError(f"{what}: no response / timeout")
    hint = ""
    if r.status_code in (403, 451): hint = " (server IP may be geo-blocked by the exchange)"
    if r.status_code == 418: hint = " (server IP temporarily banned by the exchange for too many requests - auto retry later)"
    return RuntimeError(f"{what}: HTTP {r.status_code}{hint}")

def esc(s): return html.escape(str(s), quote=False)
def esc_url(u): return html.escape(str(u), quote=True)
def fnum(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception: return default
def fnum_or_none(x):
    if x is None or x == "": return None
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception: return None
def as_bool(v, default=False):
    if v is None: return default
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return v != 0
    return str(v).strip().lower() in ("1", "true", "yes", "on", "allowed", "enable", "enabled", "normal")
def status_ok(v):
    if v is None: return ALLOW_UNVERIFIED_STATUS
    return bool(v)
def status_icon(v): return "❓" if v is None else ("✅" if v else "❌")
def base_of(pair): return pair[:-4] if pair.endswith("USDT") else pair
def norm_sym(s): return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$"); SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
NEAR_RE = re.compile(r"^(?=.{2,64}$)(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+$")
def chain_kind(ch): return CHAINS[ch]["kind"]
def norm_addr(ch, a):
    if not a: return ""
    a = a.strip()
    return a if chain_kind(ch) == "sol" else a.lower()
def valid_contract(ch, c):
    if not c or ch not in CHAINS: return False
    k = chain_kind(ch)
    if k == "evm": return bool(EVM_RE.match(c)) and c.lower() != "0x" + "0" * 40
    if k == "sol": return bool(SOL_RE.match(c))
    return bool(NEAR_RE.match(c.lower()))

UNKNOWN_NETS = Counter(); UNKNOWN_LOCK = threading.Lock()
def find_chain(*names):
    raw = None
    for nm in names:
        n = norm_sym(nm)
        if not n: continue
        raw = raw or n
        if n in ALIAS_INDEX: return ALIAS_INDEX[n]
    if raw:
        with UNKNOWN_LOCK: UNKNOWN_NETS[raw] += 1
    return None

MULT_RE = re.compile(r"^(1000000|1M|100000|10000|1000|100)([A-Z0-9]+)$")
def unit_mult(cex_sym, dex_sym):
    """CEX co the niem yet dang 1000PEPE (1 don vi CEX = 1000 token on-chain)."""
    a, b = norm_sym(cex_sym), norm_sym(dex_sym)
    if a == b: return 1.0
    m = MULT_RE.match(a)
    if m and m.group(2) == b: return 1e6 if m.group(1) in ("1M", "1000000") else float(m.group(1))
    return 1.0
def symbol_ok(cex_sym, dex_sym):
    a, b = norm_sym(cex_sym), norm_sym(dex_sym)
    if not a or not b: return False
    if a == b or unit_mult(a, b) != 1.0: return True
    return len(a) >= 3 and len(b) >= 3 and (a in b or b in a)

# ============================ THONG KE ============================
class Stat:
    KEYS = ("scanned","pools","candidates","evaluated","signals","unconfirmed","suspect","tax_skip","contract_skip","thin","quote_fail","errors")
    def __init__(self): self.lock = threading.Lock(); self.c = Counter(); self.day = Counter(); self.last_beat = time.time(); self.last_scan = 0.0
    def inc(self, k, n=1):
        with self.lock: self.c[k] += n; self.day[k] += n
    def beat(self):
        with self.lock: self.last_beat = time.time()
    def snapshot(self, daily=False):
        with self.lock: return dict(self.day if daily else self.c)
    def reset_day(self):
        with self.lock: self.day = Counter()
STATS = {k: Stat() for k in CHAINS}

ERR_LAST = {}; ERR_LOCK = threading.Lock()
def error_alert(key, text):
    """Canh bao loi he thong, toi da 1 lan / ERROR_ALERT_EVERY_H moi loai."""
    if not ERROR_ALERTS: return
    with ERR_LOCK:
        if time.time() - ERR_LAST.get(key, 0) < ERROR_ALERT_EVERY_H * 3600: return
        ERR_LAST[key] = time.time()
    send_telegram("🚨 " + text)
class ApiHealth:
    def __init__(self): self.lock = threading.Lock(); self.d = {}
    def report(self, name, ok):
        with self.lock:
            s = self.d.setdefault(name, {"ok": 0, "fail": 0, "consec": 0})
            if ok: s["ok"] += 1; s["consec"] = 0
            else: s["fail"] += 1; s["consec"] += 1
            consec = s["consec"]
        if consec == 30: error_alert(f"api:{name}", f"API <b>{esc(name)}</b> failed {consec} times in a row - scan results may be incomplete.")
    def line(self):
        with self.lock: return " | ".join(f"{k}: {v['ok']}✓/{v['fail']}✗" for k, v in sorted(self.d.items())) or "none yet"
    def reset(self):
        with self.lock:
            for v in self.d.values(): v["ok"] = v["fail"] = 0
API = ApiHealth()

# ============================ SO LENH ============================
def clean_levels(raw, mult=1.0):
    out = []
    for lv in raw or []:
        try:
            if isinstance(lv, dict): p, q = float(lv.get("price") or lv.get("p")), float(lv.get("amount") or lv.get("quantity") or lv.get("q"))
            else: p, q = float(lv[0]), float(lv[1])
        except Exception: continue
        if p > 0 and q > 0: out.append((p / mult, q * mult))
    return out
def make_depth(bids, asks):
    bids = sorted(bids, key=lambda x: -x[0]); asks = sorted(asks, key=lambda x: x[0])
    if len(bids) < 3 or len(asks) < 3 or asks[0][0] <= bids[0][0]: return None
    return {"bids": bids, "asks": asks}
def scale_depth(d, mult):
    if not d or mult == 1.0: return d
    return {"bids": [(p / mult, q * mult) for p, q in d["bids"]], "asks": [(p / mult, q * mult) for p, q in d["asks"]]}
def walk_usd(levels, usd):
    """Mua bang usd tren asks -> (gia TB, so token) hoac None neu so lenh khong du."""
    left, tok = usd, 0.0
    for p, q in levels:
        take = min(left, p * q); tok += take / p; left -= take
        if left <= 1e-9: break
    return (usd / tok, tok) if left <= usd * 1e-6 and tok > 0 else None
def walk_tokens(levels, tokens):
    """Ban tokens vao bids -> so USD nhan hoac None neu so lenh khong hap thu het."""
    left, usd = tokens, 0.0
    for p, q in levels:
        take = min(left, q); usd += take * p; left -= take
        if left <= tokens * 1e-9: break
    return usd if left <= tokens * 1e-6 else None
def depth_usd(levels, within_pct):
    if not levels: return 0.0
    ref, tot = levels[0][0], 0.0
    for p, q in levels:
        if abs(p - ref) / ref * 100 > within_pct: break
        tot += p * q
    return tot

# ============================ CEX ADAPTERS ============================
def make_listing(cex, sym, chain, contract, pair, raw_net, wfee, wmin, wd, dep):
    if chain == "SOL" and sym == "SOL" and not contract: contract = WSOL
    contract = contract.strip() if contract else ""
    if contract and not valid_contract(chain, contract): contract = ""
    return {"cex": cex, "symbol": sym, "chain": chain, "contract": contract, "key": norm_addr(chain, contract) if contract else "",
            "cex_pair": pair, "raw_network": raw_net or "", "withdraw_fee": wfee, "withdraw_min": wmin or 0.0,
            "withdraw_enable": wd, "deposit_enable": dep, "inferred": False}
def add_listing(out, seen, t):
    if norm_sym(t["symbol"]) in EXCLUDE_SYMBOLS: return
    k = (t["chain"], t["key"] or t["symbol"], t["symbol"])
    if k in seen: return
    seen.add(k); out.append(t)

class Exchange:
    name = ""; status_source = "none"   # signed | public | none
    def __init__(self):
        self._book, self._book_ts = {}, 0.0; self._lock = threading.Lock(); self.fees = {}; self.native = {}; self.futures = set()
    @property
    def lim(self): return LIMITERS[self.name]
    def load_pairs(self): return {}
    def load_networks(self, pairs): return None
    def load_book(self): return {}
    def raw_depth(self, pair): return None
    def load_futures(self): return set()
    def to_native(self, pair): return pair
    def nat(self, pair): return self.native.get(pair) or self.to_native(pair)
    def taker_fee(self, pair): return self.fees.get(pair, TAKER_FEE[self.name])
    def trade_url(self, pair): return ""
    def depth(self, pair):
        try: return self.raw_depth(pair)
        except Exception as e: log.debug("%s depth %s loi %s", self.name, pair, e); return None
    def get_book(self):
        with self._lock:
            if self._book and time.time() - self._book_ts < BOOK_TTL: return self._book
            try: b = self.load_book()
            except Exception as e: log.warning("%s book loi %s", self.name, e); b = {}
            if b: self._book, self._book_ts = b, time.time()
            return self._book
def _sym2pair(pairs):
    d = {}
    for p, s in pairs.items(): d.setdefault(s, p)
    return d

def signed_query(secret, params, algo=hashlib.sha256):
    qs = urlencode(params); return qs, hmac.new(secret.encode(), qs.encode(), algo).hexdigest()

# ---- MEXC ----
class MexcEx(Exchange):
    name = "MEXC"; BASE = "https://api.mexc.com"
    status_source = "signed" if has_key("MEXC") else "none"
    def load_pairs(self):
        r = http_get(f"{self.BASE}/api/v3/exchangeInfo", limiter=self.lim, timeout=25)
        syms = jget(r, "symbols")
        if syms is None: raise http_err(r, "MEXC exchangeInfo")
        return {s["symbol"]: s["baseAsset"] for s in syms if s.get("quoteAsset") == "USDT" and str(s.get("status")) in ("1", "ENABLED", "TRADING") and s.get("isSpotTradingAllowed", True)}
    def load_networks(self, pairs):
        if not has_key("MEXC"): return None
        k, sec, _ = KEYS["MEXC"]
        qs, sig = signed_query(sec, {"recvWindow": 10000, "timestamp": int(time.time() * 1000)})
        r = http_get(f"{self.BASE}/api/v3/capital/config/getall?{qs}&signature={sig}", headers={"X-MEXC-APIKEY": k}, limiter=self.lim, timeout=25)
        data = jget(r)
        if not isinstance(data, list): raise http_err(r, "MEXC capital/config (check API key)")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in data:
            sym = coin.get("coin")
            if sym not in bases: continue
            for net in coin.get("networkList") or []:
                ch = find_chain(net.get("netWork"), net.get("network"), net.get("name"))
                if not ch: continue
                add_listing(out, seen, make_listing("MEXC", sym, ch, net.get("contract"), s2p[sym], net.get("network") or net.get("netWork"),
                                                    fnum_or_none(net.get("withdrawFee")), fnum(net.get("withdrawMin")), as_bool(net.get("withdrawEnable")), as_bool(net.get("depositEnable"))))
        return out
    def load_book(self):
        r = http_get(f"{self.BASE}/api/v3/ticker/bookTicker", limiter=self.lim, timeout=25); book = {}
        for t in jget(r, default=[]) or []:
            bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
            if 0 < bid < ask and t.get("symbol", "").endswith("USDT"): book[t["symbol"]] = (bid, ask, None)
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/api/v3/depth", params={"symbol": pair, "limit": 200}, limiter=self.lim, timeout=12))
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get("https://contract.mexc.com/api/v1/contract/detail", limiter=self.lim, timeout=25), "data", default=[])
        return {x.get("baseCoin") for x in d if x.get("quoteCoin") == "USDT" and x.get("state", 0) == 0}
    def trade_url(self, pair): return f"https://www.mexc.com/exchange/{base_of(pair)}_USDT"

# ---- GATE ----
class GateEx(Exchange):
    name = "GATE"; BASE = "https://api.gateio.ws/api/v4"; status_source = "public"
    def to_native(self, pair): return f"{base_of(pair)}_USDT"
    def load_pairs(self):
        r = http_get(f"{self.BASE}/spot/currency_pairs", limiter=self.lim, timeout=25); d = jget(r)
        if not isinstance(d, list): raise http_err(r, "Gate currency_pairs")
        out = {}
        for p in d:
            if p.get("quote") == "USDT" and p.get("trade_status") == "tradable":
                pair = f"{p['base']}USDT"; out[pair] = p["base"]; self.native[pair] = p["id"]
                f = fnum(p.get("fee")) / 100.0
                if f > 0: self.fees[pair] = f
        return out
    def _withdraw_fees(self):
        if not has_key("GATE"): return {}
        k, sec, _ = KEYS["GATE"]; path = "/wallet/withdraw_status"; ts = str(int(time.time()))
        s = f"GET\n/api/v4{path}\n\n{hashlib.sha512(b'').hexdigest()}\n{ts}"
        sign = hmac.new(sec.encode(), s.encode(), hashlib.sha512).hexdigest()
        d = jget(http_get(f"{self.BASE}{path}", headers={"KEY": k, "Timestamp": ts, "SIGN": sign}, limiter=self.lim, timeout=25), default=[])
        out = {}
        for row in d or []:
            per = row.get("withdraw_fix_on_chains") or {}; mini = fnum(row.get("withdraw_amount_mini"))
            dd = {c: (fnum(v), mini) for c, v in per.items()}
            if not dd and row.get("withdraw_fix") is not None: dd["*"] = (fnum(row.get("withdraw_fix")), mini)
            out[row.get("currency")] = dd
        return out
    def load_networks(self, pairs):
        r = http_get(f"{self.BASE}/spot/currencies", limiter=self.lim, timeout=30); d = jget(r)
        if not isinstance(d, list): raise http_err(r, "Gate currencies")
        wf = self._withdraw_fees(); bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for c in d:
            sym = c.get("currency")
            if sym not in bases or c.get("delisted"): continue
            top_wd_off, top_dep_off = as_bool(c.get("withdraw_disabled")), as_bool(c.get("deposit_disabled"))
            for chn in c.get("chains") or []:
                ch = find_chain(chn.get("name"))
                if not ch: continue
                per = wf.get(sym, {}); fm = per.get(chn.get("name")) or per.get("*")
                add_listing(out, seen, make_listing("GATE", sym, ch, chn.get("addr"), s2p[sym], chn.get("name"), fm[0] if fm else None, fm[1] if fm else 0.0,
                                                    (not top_wd_off) and not as_bool(chn.get("withdraw_disabled")), (not top_dep_off) and not as_bool(chn.get("deposit_disabled"))))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/spot/tickers", limiter=self.lim, timeout=25), default=[]) or []:
            cp = t.get("currency_pair", "")
            if not cp.endswith("_USDT"): continue
            bid, ask = fnum(t.get("highest_bid")), fnum(t.get("lowest_ask"))
            if 0 < bid < ask: book[cp.replace("_", "")] = (bid, ask, fnum(t.get("quote_volume")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/spot/order_book", params={"currency_pair": self.nat(pair), "limit": 200}, limiter=self.lim, timeout=12))
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get(f"{self.BASE}/futures/usdt/contracts", limiter=self.lim, timeout=25), default=[])
        return {x["name"][:-5] for x in d or [] if x.get("name", "").endswith("_USDT") and not x.get("in_delisting")}
    def trade_url(self, pair): return f"https://www.gate.com/trade/{self.nat(pair)}"

# ---- BYBIT ----
class BybitEx(Exchange):
    name = "BYBIT"; BASES = ["https://api.bybit.com", "https://api.bytick.com"]
    status_source = "signed" if has_key("BYBIT") else "none"
    def _get(self, path, params=None, headers=None, timeout=20):
        r = None
        for b in self.BASES:
            r = http_get(b + path, params=params, headers=headers, limiter=self.lim, timeout=timeout)
            if r is not None and r.status_code == 200: return r
        return r
    def load_pairs(self):
        r = self._get("/v5/market/instruments-info", {"category": "spot", "limit": 1000}); lst = jget(r, "result", "list")
        if lst is None: raise http_err(r, "Bybit instruments")
        return {f"{s['baseCoin']}USDT": s["baseCoin"] for s in lst if s.get("quoteCoin") == "USDT" and s.get("status") == "Trading"}
    def load_networks(self, pairs):
        if not has_key("BYBIT"): return None
        k, sec, _ = KEYS["BYBIT"]; ts = str(int(time.time() * 1000)); recv = "10000"
        sign = hmac.new(sec.encode(), (ts + k + recv).encode(), hashlib.sha256).hexdigest()
        r = self._get("/v5/asset/coin/query-info", headers={"X-BAPI-API-KEY": k, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign}, timeout=30)
        if jget(r, "retCode") not in (0, "0"): raise http_err(r, f"Bybit coin/query-info retCode={jget(r, 'retCode')} {jget(r, 'retMsg')}")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in jget(r, "result", "rows", default=[]) or []:
            sym = coin.get("coin")
            if sym not in bases: continue
            for n in coin.get("chains") or []:
                ch = find_chain(n.get("chain"), n.get("chainType"))
                if not ch: continue
                wd = as_bool(n.get("chainWithdraw")) if "chainWithdraw" in n else None
                dep = as_bool(n.get("chainDeposit")) if "chainDeposit" in n else None
                add_listing(out, seen, make_listing("BYBIT", sym, ch, n.get("contractAddress"), s2p[sym], n.get("chainType") or n.get("chain"),
                                                    fnum_or_none(n.get("withdrawFee")), fnum(n.get("withdrawMin")), wd, dep))
        return out
    def load_book(self):
        book = {}
        for t in jget(self._get("/v5/market/tickers", {"category": "spot"}), "result", "list", default=[]) or []:
            s = t.get("symbol", "")
            bid, ask = fnum(t.get("bid1Price")), fnum(t.get("ask1Price"))
            if s.endswith("USDT") and 0 < bid < ask: book[s] = (bid, ask, fnum(t.get("turnover24h")))
        return book
    def raw_depth(self, pair):
        j = jget(self._get("/v5/market/orderbook", {"category": "spot", "symbol": pair, "limit": 200}, timeout=12), "result")
        return make_depth(clean_levels(j.get("b")), clean_levels(j.get("a"))) if j else None
    def load_futures(self):
        lst = jget(self._get("/v5/market/instruments-info", {"category": "linear", "limit": 1000}), "result", "list", default=[])
        return {x.get("baseCoin") for x in lst or [] if x.get("quoteCoin") == "USDT" and x.get("status") == "Trading" and x.get("contractType") == "LinearPerpetual"}
    def trade_url(self, pair): return f"https://www.bybit.com/en/trade/spot/{base_of(pair)}/USDT"

# ---- BINANCE ----
class BinanceEx(Exchange):
    name = "BINANCE"; BASE = "https://api.binance.com"; PUBLIC = ["https://api.binance.com", "https://data-api.binance.vision"]
    status_source = "signed" if has_key("BINANCE") else "none"
    def _pub(self, path, params=None, timeout=20):
        r = None
        for b in self.PUBLIC:
            r = http_get(b + path, params=params, limiter=self.lim, timeout=timeout)
            if r is not None and r.status_code == 200: return r
        return r
    def load_pairs(self):
        r = self._pub("/api/v3/exchangeInfo", {"permissions": "SPOT"}, timeout=30); syms = jget(r, "symbols")
        if syms is None: raise http_err(r, "Binance exchangeInfo")
        return {s["symbol"]: s["baseAsset"] for s in syms if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}
    def load_networks(self, pairs):
        if not has_key("BINANCE"): return None
        k, sec, _ = KEYS["BINANCE"]
        qs, sig = signed_query(sec, {"timestamp": int(time.time() * 1000), "recvWindow": 10000})
        r = http_get(f"{self.BASE}/sapi/v1/capital/config/getall?{qs}&signature={sig}", headers={"X-MBX-APIKEY": k}, limiter=self.lim, timeout=30); d = jget(r)
        if not isinstance(d, list): raise http_err(r, "Binance capital/config (check API key / IP)")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in d:
            sym = coin.get("coin")
            if sym not in bases: continue
            for n in coin.get("networkList") or []:
                ch = find_chain(n.get("network"), n.get("name"))
                if not ch: continue
                add_listing(out, seen, make_listing("BINANCE", sym, ch, n.get("contractAddress"), s2p[sym], n.get("network"),
                                                    fnum_or_none(n.get("withdrawFee")), fnum(n.get("withdrawMin")), as_bool(n.get("withdrawEnable")), as_bool(n.get("depositEnable"))))
        return out
    def load_book(self):
        book = {}
        for t in jget(self._pub("/api/v3/ticker/bookTicker", timeout=25), default=[]) or []:
            s = t.get("symbol", ""); bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
            if s.endswith("USDT") and 0 < bid < ask: book[s] = (bid, ask, None)
        return book
    def raw_depth(self, pair):
        j = jget(self._pub("/api/v3/depth", {"symbol": pair, "limit": 200}, timeout=12))
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        syms = jget(http_get("https://fapi.binance.com/fapi/v1/exchangeInfo", limiter=self.lim, timeout=25), "symbols", default=[])
        return {x.get("baseAsset") for x in syms or [] if x.get("quoteAsset") == "USDT" and x.get("contractType") == "PERPETUAL" and x.get("status") == "TRADING"}
    def trade_url(self, pair): return f"https://www.binance.com/en/trade/{base_of(pair)}_USDT?type=spot"

# ---- OKX ----
class OkxEx(Exchange):
    name = "OKX"; BASE = "https://www.okx.com"
    status_source = "signed" if has_key("OKX") else "none"
    def to_native(self, pair): return f"{base_of(pair)}-USDT"
    def _tickers(self): return jget(http_get(f"{self.BASE}/api/v5/market/tickers", params={"instType": "SPOT"}, limiter=self.lim, timeout=25), "data", default=None)
    def load_pairs(self):
        r = http_get(f"{self.BASE}/api/v5/public/instruments", params={"instType": "SPOT"}, limiter=self.lim, timeout=25); d = jget(r, "data")
        if d is None: raise http_err(r, "OKX instruments")
        out = {}
        for x in d:
            if x.get("quoteCcy") == "USDT" and x.get("state") == "live":
                pair = f"{x['baseCcy']}USDT"; out[pair] = x["baseCcy"]; self.native[pair] = x["instId"]
        return out
    def load_networks(self, pairs):
        if not has_key("OKX"): return None
        k, sec, pp = KEYS["OKX"]; path = "/api/v5/asset/currencies"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = base64.b64encode(hmac.new(sec.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()).decode()
        r = http_get(self.BASE + path, headers={"OK-ACCESS-KEY": k, "OK-ACCESS-SIGN": sign, "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": pp}, limiter=self.lim, timeout=30)
        d = jget(r, "data")
        if d is None: raise http_err(r, f"OKX asset/currencies {jget(r, 'msg', default='')}")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for row in d:
            sym = row.get("ccy")
            if sym not in bases: continue
            craw = row.get("chain") or ""; net = craw.split("-", 1)[1] if "-" in craw else craw
            ch = find_chain(net)
            if not ch: continue
            fee = fnum_or_none(row.get("fee")) if row.get("fee") not in (None, "") else fnum_or_none(row.get("minFee"))
            add_listing(out, seen, make_listing("OKX", sym, ch, row.get("ctAddr"), s2p[sym], craw, fee, fnum(row.get("minWd")), as_bool(row.get("canWd")), as_bool(row.get("canDep"))))
        return out
    def load_book(self):
        book = {}
        for t in self._tickers() or []:
            inst = t.get("instId", "")
            if not inst.endswith("-USDT"): continue
            bid, ask = fnum(t.get("bidPx")), fnum(t.get("askPx"))
            if 0 < bid < ask: book[inst.replace("-", "")] = (bid, ask, fnum(t.get("volCcy24h")))
        return book
    def raw_depth(self, pair):
        d = jget(http_get(f"{self.BASE}/api/v5/market/books", params={"instId": self.nat(pair), "sz": 200}, limiter=self.lim, timeout=12), "data", 0)
        return make_depth(clean_levels(d.get("bids")), clean_levels(d.get("asks"))) if d else None
    def load_futures(self):
        d = jget(http_get(f"{self.BASE}/api/v5/public/instruments", params={"instType": "SWAP"}, limiter=self.lim, timeout=25), "data", default=[])
        return {x["instId"].split("-")[0] for x in d or [] if x.get("instId", "").endswith("-USDT-SWAP") and x.get("state") == "live"}
    def trade_url(self, pair): return f"https://www.okx.com/trade-spot/{self.nat(pair).lower()}"

# ---- KUCOIN ----
class KucoinEx(Exchange):
    name = "KUCOIN"; BASE = "https://api.kucoin.com"; status_source = "public"
    def to_native(self, pair): return f"{base_of(pair)}-USDT"
    def load_pairs(self):
        r = http_get(f"{self.BASE}/api/v2/symbols", limiter=self.lim, timeout=25); d = jget(r, "data")
        if d is None: raise http_err(r, "KuCoin symbols")
        out = {}
        for s in d:
            if s.get("quoteCurrency") == "USDT" and as_bool(s.get("enableTrading"), True):
                pair = f"{s['baseCurrency']}USDT"; out[pair] = s["baseCurrency"]; self.native[pair] = s["symbol"]
        return out
    def load_networks(self, pairs):
        r = http_get(f"{self.BASE}/api/v3/currencies", limiter=self.lim, timeout=30); d = jget(r, "data")
        if d is None: raise http_err(r, "KuCoin currencies")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in d:
            sym = coin.get("currency")
            if sym not in bases: continue
            for c in coin.get("chains") or []:
                ch = find_chain(c.get("chainName"), c.get("chainId"))
                if not ch: continue
                wd = as_bool(c.get("isWithdrawEnabled")) if "isWithdrawEnabled" in c else None
                dep = as_bool(c.get("isDepositEnabled")) if "isDepositEnabled" in c else None
                add_listing(out, seen, make_listing("KUCOIN", sym, ch, c.get("contractAddress"), s2p[sym], c.get("chainName"),
                                                    fnum_or_none(c.get("withdrawalMinFee")), fnum(c.get("withdrawalMinSize")), wd, dep))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/api/v1/market/allTickers", limiter=self.lim, timeout=25), "data", "ticker", default=[]) or []:
            s = t.get("symbol", "")
            if not s.endswith("-USDT"): continue
            bid, ask = fnum(t.get("buy")), fnum(t.get("sell"))
            if 0 < bid < ask: book[s.replace("-", "")] = (bid, ask, fnum(t.get("volValue")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/api/v1/market/orderbook/level2_100", params={"symbol": self.nat(pair)}, limiter=self.lim, timeout=12), "data")
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get("https://api-futures.kucoin.com/api/v1/contracts/active", limiter=self.lim, timeout=25), "data", default=[])
        return {("BTC" if x.get("baseCurrency") == "XBT" else x.get("baseCurrency")) for x in d or [] if x.get("quoteCurrency") == "USDT" and x.get("isInverse") is False}
    def trade_url(self, pair): return f"https://www.kucoin.com/trade/{self.nat(pair)}"

# ---- BITGET ----
class BitgetEx(Exchange):
    name = "BITGET"; BASE = "https://api.bitget.com"; status_source = "public"
    def load_pairs(self):
        r = http_get(f"{self.BASE}/api/v2/spot/public/symbols", limiter=self.lim, timeout=25); d = jget(r, "data")
        if d is None: raise http_err(r, "Bitget symbols")
        return {s["symbol"]: s["baseCoin"] for s in d if s.get("quoteCoin") == "USDT" and s.get("status") in (None, "online")}
    def load_networks(self, pairs):
        r = http_get(f"{self.BASE}/api/v2/spot/public/coins", limiter=self.lim, timeout=30); d = jget(r, "data")
        if d is None: raise http_err(r, "Bitget coins")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in d:
            sym = coin.get("coin")
            if sym not in bases: continue
            for c in coin.get("chains") or []:
                ch = find_chain(c.get("chain"))
                if not ch: continue
                wd = as_bool(c.get("withdrawable")) if "withdrawable" in c else None
                dep = as_bool(c.get("rechargeable")) if "rechargeable" in c else None
                add_listing(out, seen, make_listing("BITGET", sym, ch, c.get("contractAddress"), s2p[sym], c.get("chain"),
                                                    fnum_or_none(c.get("withdrawFee")), fnum(c.get("minWithdrawAmount")), wd, dep))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/api/v2/spot/market/tickers", limiter=self.lim, timeout=25), "data", default=[]) or []:
            s = t.get("symbol", ""); bid, ask = fnum(t.get("bidPr")), fnum(t.get("askPr"))
            if s.endswith("USDT") and 0 < bid < ask: book[s] = (bid, ask, fnum(t.get("usdtVolume")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/api/v2/spot/market/orderbook", params={"symbol": pair, "type": "step0", "limit": 150}, limiter=self.lim, timeout=12), "data")
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get(f"{self.BASE}/api/v2/mix/market/contracts", params={"productType": "USDT-FUTURES"}, limiter=self.lim, timeout=25), "data", default=[])
        return {x.get("baseCoin") for x in d or [] if x.get("symbolStatus") in (None, "normal")}
    def trade_url(self, pair): return f"https://www.bitget.com/spot/{pair}"

# ---- HTX ----
class HtxEx(Exchange):
    name = "HTX"; BASE = "https://api.huobi.pro"; status_source = "public"
    def to_native(self, pair): return pair.lower()
    def load_pairs(self):
        r = http_get(f"{self.BASE}/v1/common/symbols", limiter=self.lim, timeout=25); d = jget(r, "data")
        if d is None: raise http_err(r, "HTX symbols")
        out = {}
        for s in d:
            if s.get("quote-currency") == "usdt" and s.get("state") in (None, "online"):
                b = s.get("base-currency", "").upper(); pair = f"{b}USDT"; out[pair] = b; self.native[pair] = s.get("symbol")
        return out
    def load_networks(self, pairs):
        r = http_get(f"{self.BASE}/v2/reference/currencies", limiter=self.lim, timeout=30); d = jget(r, "data")
        if d is None: raise http_err(r, "HTX reference/currencies")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in d:
            sym = (coin.get("currency") or "").upper()
            if sym not in bases: continue
            for c in coin.get("chains") or []:
                ch = find_chain(c.get("displayName"), c.get("baseChainProtocol"), c.get("baseChain"), c.get("chain"))
                if not ch: continue
                wd = (c.get("withdrawStatus") == "allowed") if c.get("withdrawStatus") else None
                dep = (c.get("depositStatus") == "allowed") if c.get("depositStatus") else None
                add_listing(out, seen, make_listing("HTX", sym, ch, c.get("contractAddress") or "", s2p[sym], c.get("displayName") or c.get("chain"),
                                                    fnum_or_none(c.get("transactFeeWithdraw")), fnum(c.get("minWithdrawAmt")), wd, dep))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/market/tickers", limiter=self.lim, timeout=25), "data", default=[]) or []:
            s = t.get("symbol", "").upper(); bid, ask = fnum(t.get("bid")), fnum(t.get("ask"))
            if s.endswith("USDT") and 0 < bid < ask: book[s] = (bid, ask, fnum(t.get("vol")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/market/depth", params={"symbol": self.nat(pair), "type": "step0"}, limiter=self.lim, timeout=12), "tick")
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get("https://api.hbdm.com/linear-swap-api/v1/swap_contract_info", params={"business_type": "swap"}, limiter=self.lim, timeout=25), "data", default=[])
        return {x.get("symbol") for x in d or [] if str(x.get("contract_code", "")).endswith("-USDT") and x.get("contract_status") == 1}
    def trade_url(self, pair): return f"https://www.htx.com/trade/{base_of(pair).lower()}_usdt"

# ---- BINGX ----
class BingxEx(Exchange):
    name = "BINGX"; BASE = "https://open-api.bingx.com"
    status_source = "signed" if has_key("BINGX") else "none"
    def to_native(self, pair): return f"{base_of(pair)}-USDT"
    def _ts(self): return {"timestamp": int(time.time() * 1000)}
    def load_pairs(self):
        r = http_get(f"{self.BASE}/openApi/spot/v1/common/symbols", params=self._ts(), limiter=self.lim, timeout=25); d = jget(r, "data", "symbols")
        if d is None: raise http_err(r, "BingX symbols")
        out = {}
        for s in d:
            sym = s.get("symbol", "")
            if not sym.endswith("-USDT") or str(s.get("status", 1)) not in ("1", "TRADING"): continue
            pair = sym.replace("-", ""); out[pair] = sym[:-5]; self.native[pair] = sym
        return out
    def load_networks(self, pairs):
        if not has_key("BINGX"): return None
        k, sec, _ = KEYS["BINGX"]; qs, sig = signed_query(sec, self._ts())
        r = http_get(f"{self.BASE}/openApi/wallets/v1/capital/config/getall?{qs}&signature={sig}", headers={"X-BX-APIKEY": k}, limiter=self.lim, timeout=30)
        d = jget(r, "data")
        if not isinstance(d, list): raise http_err(r, f"BingX capital/config {jget(r, 'msg', default='')}")
        bases = set(pairs.values()); s2p = _sym2pair(pairs); out, seen = [], set()
        for coin in d:
            sym = coin.get("coin")
            if sym not in bases: continue
            for n in coin.get("networkList") or []:
                ch = find_chain(n.get("network"), n.get("name"))
                if not ch: continue
                add_listing(out, seen, make_listing("BINGX", sym, ch, n.get("contractAddress") or n.get("contract"), s2p[sym], n.get("network"),
                                                    fnum_or_none(n.get("withdrawFee")), fnum(n.get("withdrawMin")), as_bool(n.get("withdrawEnable")), as_bool(n.get("depositEnable"))))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/openApi/spot/v1/ticker/24hr", params=self._ts(), limiter=self.lim, timeout=25), "data", default=[]) or []:
            s = t.get("symbol", ""); bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
            if s.endswith("USDT") and 0 < bid < ask: book[s.replace("-", "")] = (bid, ask, fnum(t.get("quoteVolume")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/openApi/spot/v1/market/depth", params=dict(self._ts(), symbol=self.nat(pair), limit=100), limiter=self.lim, timeout=12), "data")
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def load_futures(self):
        d = jget(http_get(f"{self.BASE}/openApi/swap/v2/quote/contracts", params=self._ts(), limiter=self.lim, timeout=25), "data", default=[])
        return {x.get("symbol", "")[:-5] for x in d or [] if x.get("symbol", "").endswith("-USDT") and str(x.get("status", 1)) == "1"}
    def trade_url(self, pair): return f"https://bingx.com/en/spot/{base_of(pair)}USDT"

# ---- LBANK ----
class LbankEx(Exchange):
    name = "LBANK"; BASE = "https://api.lbank.info"; status_source = "public-withdraw"
    def to_native(self, pair): return f"{base_of(pair).lower()}_usdt"
    def load_pairs(self):
        r = http_get(f"{self.BASE}/v2/currencyPairs.do", limiter=self.lim, timeout=25); d = jget(r, "data")
        if d is None: raise http_err(r, "LBank currencyPairs")
        out = {}
        for s in d:
            if isinstance(s, str) and s.endswith("_usdt"):
                b = s[:-5].upper(); pair = f"{b}USDT"; out[pair] = b; self.native[pair] = s
        return out
    def load_networks(self, pairs):
        # Public: chi co cau hinh RUT (phi, min, canWithDraw). Trang thai NAP khong cong bo -> None.
        r = http_get(f"{self.BASE}/v2/withdrawConfigs.do", limiter=self.lim, timeout=30); d = jget(r, "data")
        if not isinstance(d, list): return None
        bases = {b.upper(): p for p, b in pairs.items()}; out, seen = [], set()
        for row in d:
            sym = (row.get("assetCode") or "").upper()
            if sym not in bases: continue
            ch = find_chain(row.get("chain"), row.get("chainName"))
            if not ch: continue
            add_listing(out, seen, make_listing("LBANK", sym, ch, row.get("contractAddress") or "", bases[sym], row.get("chain"),
                                                fnum_or_none(row.get("fee")), fnum(row.get("min")), as_bool(row.get("canWithDraw")), None))
        return out
    def load_book(self):
        book = {}
        for t in jget(http_get(f"{self.BASE}/v2/supplement/ticker/bookTicker.do", limiter=self.lim, timeout=25), "data", default=[]) or []:
            s = t.get("symbol", "")
            bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
            if s.endswith("_usdt") and 0 < bid < ask: book[s.replace("_", "").upper()] = (bid, ask, None)
        if book: return book
        for t in jget(http_get(f"{self.BASE}/v2/ticker/24hr.do", params={"symbol": "all"}, limiter=self.lim, timeout=25), "data", default=[]) or []:
            s = t.get("symbol", ""); tk = t.get("ticker") or {}; last = fnum(tk.get("latest"))
            if s.endswith("_usdt") and last > 0: book[s.replace("_", "").upper()] = (last * 0.998, last * 1.002, fnum(tk.get("turnover")))
        return book
    def raw_depth(self, pair):
        j = jget(http_get(f"{self.BASE}/v2/depth.do", params={"symbol": self.nat(pair), "size": 200}, limiter=self.lim, timeout=12), "data")
        return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks"))) if j else None
    def trade_url(self, pair): return f"https://www.lbank.com/trade/{self.nat(pair)}"

_EX = {"MEXC": MexcEx, "GATE": GateEx, "KUCOIN": KucoinEx, "BITGET": BitgetEx, "BYBIT": BybitEx, "BINANCE": BinanceEx, "OKX": OkxEx, "BINGX": BingxEx, "HTX": HtxEx, "LBANK": LbankEx}
EXCHANGES = {n: _EX[n]() for n in ENABLED_CEXS}

# ============================ DU LIEU TONG HOP CEX ============================
class MultiData:
    def __init__(self):
        self.lock = threading.Lock(); self.refresh_lock = threading.Lock(); self.ts = 0.0; self.retry_after = 0.0
        self.tokens = {}; self.tok_ts = {}; self.health = {}; self.by_chain = {}; self.sym_index = {}
        self.stable = {}; self.cand = {}; self.fut_ts = 0.0; self.first_done = threading.Event()
    def ensure(self):
        now = time.time()
        if self.by_chain and now - self.ts < STATIC_TTL: return
        if now < self.retry_after: return
        if not self.refresh_lock.acquire(blocking=False): return
        events = []
        try:
            if now - self.fut_ts > 3600 and self.by_chain:      # futures: tai nen, sau khi da co du lieu chinh
                self.fut_ts = now; threading.Thread(target=self._load_futures, daemon=True, name="Futures").start()
            def load(name):
                ex = EXCHANGES[name]; t0 = time.time()
                pairs = ex.load_pairs()
                if not pairs: raise RuntimeError("0 USDT pairs")
                toks = ex.load_networks(pairs)
                return name, pairs, toks, time.time() - t0
            from concurrent.futures import as_completed, TimeoutError as FTimeout
            pool = ThreadPoolExecutor(max_workers=max(1, REFRESH_WORKERS))
            futs = {pool.submit(load, n): n for n in EXCHANGES}; done = set()
            try:
                for f in as_completed(futs, timeout=900):
                    n = futs[f]; done.add(n)
                    h = self.health.setdefault(n, {"ok": False, "err": "", "pairs": 0, "listings": 0, "verified": False, "fail_since": None})
                    try:
                        name, pairs, toks, dt = f.result()
                    except Exception as e:
                        msg = str(e) or type(e).__name__
                        h.update(ok=False, err=msg[:200]); h["fail_since"] = h["fail_since"] or time.time()
                        log.error("Lam moi %s loi: %s", n, msg)
                        if time.time() - h["fail_since"] > 1800: error_alert(f"cex:{n}", f"<b>{n}</b> failing for >30 min: {esc(h['err'])}")
                        continue
                    verified = toks is not None
                    if toks is None: toks = []
                    h.update(ok=True, err="" if verified else "no network/deposit-withdraw data (missing API key?)", pairs=len(pairs), listings=len(toks), verified=verified, fail_since=None)
                    events += self._diff_status(n, toks)
                    with self.lock: self.tokens[n] = toks; self.tok_ts[n] = time.time()
                    log.info("%s: %d cap, %d token-mang (%.1fs)%s", n, len(pairs), len(toks), dt, "" if verified else " [KHONG CO DU LIEU MANG]")
                    del pairs, toks
                    self._build(); self.first_done.set()          # quet ngay voi cac san da tai xong
                    gc.collect()
            except FTimeout:
                for f, n in futs.items():
                    if n not in done:
                        h = self.health.setdefault(n, {"ok": False, "err": "", "pairs": 0, "listings": 0, "verified": False, "fail_since": None})
                        h.update(ok=False, err="timeout (>15 min) - server too slow?"); log.error("Lam moi %s: timeout", n)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if not any(h["ok"] for h in self.health.values()) and not self.by_chain:
                self.retry_after = time.time() + 60; return
            self._build()
        finally:
            gc.collect()
            self.refresh_lock.release(); self.first_done.set()
        if events: handle_status_events(events)
    def _load_futures(self):
        def lf(ex):
            try: return ex.name, ex.load_futures()
            except Exception as e: log.debug("%s futures loi %s", ex.name, e); return ex.name, None
        for ex in EXCHANGES.values():          # tuan tu: cac file futures rat lon, tai song song de tran RAM
            name, fut = lf(ex)
            if fut: EXCHANGES[name].futures = {x.upper() for x in fut if x}
            gc.collect()
    def _diff_status(self, name, toks):
        """Chong nhay trang thai: chi bao khi trang thai moi giu nguyen STATUS_CONFIRM lan lam moi lien tiep."""
        ev = []; st = self.stable.setdefault(name, {}); cd = self.cand.setdefault(name, {}); first = not st
        for t in toks:
            for field, kind in (("withdraw_enable", "withdraw"), ("deposit_enable", "deposit")):
                k = (t["chain"], t["key"] or t["symbol"], t["symbol"], field); v = t[field]
                if v is None: continue
                if k not in st: st[k] = v; continue
                if v == st[k]: cd.pop(k, None); continue
                c = cd.get(k)
                cnt = c[1] + 1 if c and c[0] == v else 1
                if cnt >= STATUS_CONFIRM:
                    st[k] = v; cd.pop(k, None)
                    if not first: ev.append((name, t, ("open_" if v else "close_") + kind))
                else: cd[k] = (v, cnt)
        return ev
    def _build(self):
        now = time.time(); bc = {k: {} for k in CHAINS}; pending = []
        with self.lock: items = list(self.tokens.items())
        for name, toks in items:
            if now - self.tok_ts.get(name, 0) > STALE_MAX: log.warning("%s: du lieu mang cu -> bo qua", name); continue
            for t in toks:
                if t["chain"] not in bc: continue
                if not t["contract"]: pending.append(t); continue
                e = bc[t["chain"]].setdefault(t["key"], {"contract": t["contract"], "key": t["key"], "symbol": t["symbol"], "listings": []})
                e["listings"].append(t)
        idx = {}
        for ch, ents in bc.items():
            for e in ents.values():
                for l in e["listings"]: idx.setdefault((ch, norm_sym(l["symbol"])), {})[e["key"]] = e
        merged = 0
        if ALLOW_INFERRED_CONTRACT:
            for t in pending:
                cands = list(idx.get((t["chain"], norm_sym(t["symbol"])), {}).values())
                if len(cands) != 1: continue
                e = cands[0]
                if any(l["cex"] == t["cex"] for l in e["listings"]): continue
                e["listings"].append(dict(t, contract=e["contract"], key=e["key"], inferred=True)); merged += 1
        with self.lock:
            self.by_chain = {k: list(v.values()) for k, v in bc.items()}; self.sym_index = idx; self.ts = time.time()
        log.info("Tong hop: %s | ghep theo symbol: %d", ", ".join(f"{k}={len(v)}" for k, v in self.by_chain.items()), merged)
    def resolve(self, t):
        if t.get("contract"): return t
        if not ALLOW_INFERRED_CONTRACT: return None
        c = list(self.sym_index.get((t["chain"], norm_sym(t["symbol"])), {}).values())
        if len(c) != 1: return None
        return dict(t, contract=c[0]["contract"], key=c[0]["key"], inferred=True)
    def futures_for(self, sym):
        s = norm_sym(sym)
        return [n for n, ex in EXCHANGES.items() if s in ex.futures or f"1000{s}" in ex.futures]
MULTI = MultiData()

# ============================ DEX POOL (DEXSCREENER) ============================
def fetch_dex_pools(ch, addrs):
    """Gop TAT CA pool tren MOI DEX cua tung token (DexScreener). Tra ve {key: info}; None neu API loi.
    - Chi tinh pool >= MIN_POOL_LIQ_EACH, bo pool gia lech >20% so voi trung vi (pool rac / gia ao)
    - Thanh khoan, volume, so giao dich = TONG cac DEX; gia = trung binh theo thanh khoan
    - price_min / price_max: DEX re nhat / dat nhat (de loc nhanh); quote that van qua aggregator dinh tuyen moi DEX"""
    cid = CHAINS[ch]["dex"]; real = [a for a in addrs if valid_contract(ch, a)]
    if not real: return {}
    want = {norm_addr(ch, a) for a in real}
    r = http_get(f"https://api.dexscreener.com/tokens/v1/{cid}/{','.join(urlquote(a, safe='') for a in real)}", limiter=DEX_LIMITER, timeout=15)
    ok = r is not None and r.status_code == 200; API.report("dexscreener", ok)
    if not ok: return None
    try: pairs = r.json()
    except Exception: return None
    if not isinstance(pairs, list): return None
    by_tok = {}; now_ms = time.time() * 1000
    for p in pairs:
        if p.get("chainId") != cid: continue
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        bk, qk = norm_addr(ch, base.get("address")), norm_addr(ch, quote.get("address"))
        pu, pn = fnum(p.get("priceUsd")), fnum(p.get("priceNative"))
        if bk in want: key, price, me, other = bk, pu, base, quote
        elif qk in want and pu > 0 and pn > 0: key, price, me, other = qk, pu / pn, quote, base
        else: continue
        liq = fnum((p.get("liquidity") or {}).get("usd"))
        if price <= 0 or liq < MIN_POOL_LIQ_EACH: continue
        h1 = (p.get("txns") or {}).get("h1") or {}; created = fnum(p.get("pairCreatedAt")); pc = p.get("priceChange") or {}
        by_tok.setdefault(key, []).append({"price": price, "liq": liq, "vol24": fnum((p.get("volume") or {}).get("h24")),
            "txns_h1": int(fnum(h1.get("buys")) + fnum(h1.get("sells"))), "age_h": (now_ms - created) / 3.6e6 if created > 0 else 9999,
            "dex": (p.get("dexId") or "?").lower(), "url": p.get("url") or "", "pair_name": f"{me.get('symbol')}/{other.get('symbol')}",
            "dex_symbol": me.get("symbol") or "", "chg_m5": fnum(pc.get("m5")), "chg_h1": fnum(pc.get("h1"))})
    out = {}
    for key, pools in by_tok.items():
        prices = sorted(x["price"] for x in pools); med = prices[len(prices) // 2]
        good = [x for x in pools if abs(x["price"] - med) / med <= 0.20]
        if not good: continue
        liq_total = sum(x["liq"] for x in good); top = max(good, key=lambda x: x["liq"])
        dexes = {}
        for x in good:
            d = dexes.setdefault(x["dex"], {"liq": 0.0, "pv": 0.0, "vol24": 0.0, "pools": 0}); d["liq"] += x["liq"]; d["pv"] += x["price"] * x["liq"]; d["vol24"] += x["vol24"]; d["pools"] += 1
        dex_list = sorted(({"dex": k, "liq": v["liq"], "price": v["pv"] / v["liq"], "vol24": v["vol24"], "pools": v["pools"]} for k, v in dexes.items()), key=lambda x: -x["liq"])
        deep = [x for x in good if x["liq"] >= max(MIN_POOL_LIQ_EACH * 2, liq_total * 0.05)] or [top]
        info = dict(top)
        info.update(price=sum(x["price"] * x["liq"] for x in good) / liq_total, liq_total=liq_total, liq=top["liq"],
                    vol24=sum(x["vol24"] for x in good), txns_h1=sum(x["txns_h1"] for x in good), age_h=max(x["age_h"] for x in good),
                    price_min=min(x["price"] for x in deep), price_max=max(x["price"] for x in deep), dexes=dex_list, n_pools=len(good))
        if info["liq_total"] < MIN_LIQ_USD or info["vol24"] < MIN_VOL_24H or info["txns_h1"] < MIN_TXNS_H1 or info["age_h"] < MIN_POOL_AGE_H: continue
        out[key] = info
    return out

# ============================ DECIMALS ============================
DEC_CACHE = {}; DEC_LOCK = threading.Lock()
def get_decimals(ch, token, price_hint=None):
    key = (ch, norm_addr(ch, token))
    with DEC_LOCK:
        if key in DEC_CACHE: return DEC_CACHE[key]
    cfg = CHAINS[ch]; dec = None
    try:
        if cfg["kind"] == "sol":
            j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token]})
            dec = (((j or {}).get("result") or {}).get("value") or {}).get("decimals")
        elif cfg["kind"] == "near":
            j = rpc_post(cfg["rpc"], {"jsonrpc": "2.0", "id": "1", "method": "query", "params": {"request_type": "call_function", "finality": "final", "account_id": token, "method_name": "ft_metadata", "args_base64": "e30="}})
            raw = ((j or {}).get("result") or {}).get("result")
            if raw: dec = json.loads(bytes(raw).decode()).get("decimals")
        else:
            if cfg.get("rpc"):
                j = rpc_post(cfg["rpc"], {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": token, "data": "0x313ce567"}, "latest"]})
                res = (j or {}).get("result")
                if res and res not in ("0x", "0x0") and len(res) >= 3: dec = int(res, 16)
            if dec is None and cfg.get("blockscout"):
                dec = fnum_or_none(jget(http_get(f"{cfg['blockscout'].rstrip('/')}/api/v2/tokens/{token}", timeout=10), "decimals"))
    except Exception as e: log.debug("decimals loi %s %s: %s", ch, token[:10], e)
    if dec is None and price_hint and CHAINS[ch]["kind"] == "evm" and CHAINS[ch].get("kyber"):
        dec = decimals_from_kyber(ch, token, price_hint)   # mang khong co RPC (Robinhood, Arc...): suy ra tu quote
    API.report(f"rpc-{ch}", dec is not None)
    if dec is not None and 0 <= int(dec) <= 36:
        with DEC_LOCK: DEC_CACHE[key] = int(dec)
        return int(dec)
    return None

def decimals_from_kyber(ch, token, price):
    """Suy decimals: quote ~$20 vao token -> amountOut (wei) va amountOutUsd; so token = USD / gia -> 10^dec = wei / so token."""
    try:
        cfg = CHAINS[ch]; st = cfg.get("stable")
        if st: rs, _ = kyber_route(cfg["kyber"], st[0], token, 20 * 10**st[1])
        else: rs, _ = kyber_route(cfg["kyber"], NATIVE, token, 10**16)
        if not rs: return None
        raw, usd = fnum(rs.get("amountOut")), fnum(rs.get("amountOutUsd"))
        if raw <= 0 or usd <= 0 or price <= 0: return None
        x = math.log10(raw / (usd / price)); d = round(x)
        if abs(x - d) < 0.15 and 0 <= d <= 36:
            log.info("decimals %s %s suy tu KyberSwap = %d", ch, token[:10], d); return d
    except Exception as e: log.debug("decimals_from_kyber loi %s", e)
    return None

# ============================ QUOTE DEX ============================
NATIVE_PX = {}; NATIVE_LOCK = threading.Lock()
def kyber_route(slug, tin, tout, amount_raw):
    r = http_get(f"{KYBER_URL}/{slug}/api/v1/routes", params={"tokenIn": tin, "tokenOut": tout, "amountIn": str(int(amount_raw)), "gasInclude": "true"},
                 headers={"X-Client-Id": KYBER_CLIENT_ID}, limiter=KYBER_LIMITER, timeout=10, retries=1)
    if r is None: API.report("kyberswap", False); return None, "error"
    if r.status_code == 200:
        rs = jget(r, "data", "routeSummary")
        API.report("kyberswap", True)
        if not rs or fnum(rs.get("amountOut")) <= 0: return None, "noroute"
        return rs, "ok"
    if r.status_code in (400, 404, 422): API.report("kyberswap", True); return None, "noroute"
    API.report("kyberswap", False); return None, "error"
def _impact(usd_in, usd_out):
    return max(0.0, (usd_in - usd_out) / usd_in * 100) if usd_in and usd_out and usd_in > 0 else None
def native_price(ch, token):
    """Gia native (ETH...) theo USD qua KyberSwap (cho mang khong co stablecoin cau hinh)."""
    with NATIVE_LOCK:
        c = NATIVE_PX.get(ch)
        if c and time.time() - c[0] < 120: return c[1]
    rs, kind = kyber_route(CHAINS[ch]["kyber"], NATIVE, token, 10**16)
    px = fnum(rs.get("amountInUsd")) / 0.01 if rs else 0
    if px > 0:
        with NATIVE_LOCK: NATIVE_PX[ch] = (time.time(), px)
        return px
    return None
def evm_quote(ch, tin_is_usd, token, dec, amount):
    """amount: USD (khi mua) hoac so token (khi ban). Tra ve dict chuan hoa."""
    cfg = CHAINS[ch]; st = cfg.get("stable")
    if st:
        sa, sd = st
        tin, tout, raw = (sa, token, amount * 10**sd) if tin_is_usd else (token, sa, amount * 10**dec)
        cands = []; kinds = []
        rs, kind = kyber_route(cfg["kyber"], tin, tout, raw); kinds.append(kind)
        if rs:
            if tin_is_usd: out_amt = fnum(rs["amountOut"]) / 10**dec; usd_in, usd_out = amount, fnum(rs.get("amountOutUsd"))
            else: out_amt = fnum(rs["amountOut"]) / 10**sd; usd_in, usd_out = fnum(rs.get("amountInUsd")), out_amt
            cands.append({"out": out_amt, "impact": _impact(usd_in, usd_out), "gas_usd": fnum(rs.get("gasUsd")) + fnum(rs.get("l1FeeUsd")), "src": "KyberSwap"})
        if cfg.get("chain_id") in PARASWAP_CHAINS and (MULTI_AGG or kind == "error"):
            q, kind2 = paraswap_quote(cfg["chain_id"], tin, tout, raw, sd if tin_is_usd else dec, dec if tin_is_usd else sd); kinds.append(kind2)
            if q: cands.append({"out": q["out"] / 10**(dec if tin_is_usd else sd), "impact": q["impact"], "gas_usd": q["gas_usd"], "src": "ParaSwap"})
        if cands:
            best = max(cands, key=lambda x: x["out"])
            if len(cands) > 1: best = dict(best, src=f"{best['src']}, best of 2")
            return best, "ok"
        return None, ("error" if all(k == "error" for k in kinds) else "noroute")
    px = native_price(ch, token)
    if not px: return None, "error"
    if tin_is_usd:
        rs, kind = kyber_route(cfg["kyber"], NATIVE, token, amount / px * 1e18)
        if not rs: return None, kind
        return {"out": fnum(rs["amountOut"]) / 10**dec, "impact": _impact(fnum(rs.get("amountInUsd")), fnum(rs.get("amountOutUsd"))), "gas_usd": fnum(rs.get("gasUsd")) + fnum(rs.get("l1FeeUsd")), "src": "KyberSwap"}, "ok"
    rs, kind = kyber_route(cfg["kyber"], token, NATIVE, amount * 10**dec)
    if not rs: return None, kind
    usd_out = fnum(rs["amountOut"]) / 1e18 * px
    return {"out": usd_out, "impact": _impact(fnum(rs.get("amountInUsd")), usd_out), "gas_usd": fnum(rs.get("gasUsd")) + fnum(rs.get("l1FeeUsd")), "src": "KyberSwap"}, "ok"
def paraswap_quote(chain_id, src, dest, raw, sdec, ddec):
    r = http_get(PARASWAP_URL, params={"srcToken": src, "destToken": dest, "amount": str(int(raw)), "srcDecimals": sdec, "destDecimals": ddec, "side": "SELL", "network": chain_id, "version": "6.2"}, limiter=PARA_LIMITER, timeout=10, retries=1)
    if r is None: API.report("paraswap", False); return None, "error"
    if r.status_code == 200:
        API.report("paraswap", True); pr = jget(r, "priceRoute", default={}); out = fnum(pr.get("destAmount"))
        if out <= 0: return None, "noroute"
        return {"out": out, "impact": _impact(fnum(pr.get("srcUSD")), fnum(pr.get("destUSD"))), "gas_usd": fnum(pr.get("gasCostUSD"))}, "ok"
    if r.status_code in (400, 404): API.report("paraswap", True); return None, "noroute"
    API.report("paraswap", False); return None, "error"
def jup_quote(imint, omint, raw):
    params = {"inputMint": imint, "outputMint": omint, "amount": str(int(raw)), "slippageBps": 100, "restrictIntermediateTokens": "true"}
    eps = ([(JUPITER_PRO_URL, {"x-api-key": JUPITER_API_KEY})] if JUPITER_API_KEY else []) + [(JUPITER_LITE_URL, None)]
    for url, h in eps:
        r = http_get(url, params=params, headers=h, limiter=JUP_LIMITER, timeout=10, retries=1)
        if r is None: API.report("jupiter", False); continue
        if r.status_code == 200:
            API.report("jupiter", True); out = fnum(jget(r, "outAmount"))
            if out <= 0: return None, "noroute"
            return {"out": out, "impact": abs(fnum(jget(r, "priceImpactPct"))) * 100}, "ok"
        if r.status_code in (400, 404): API.report("jupiter", True); return None, "noroute"
        API.report("jupiter", False)
    return None, "error"
def ref_quote(tin, tout, raw):
    r = http_get(REF_ROUTER_URL, params={"amountIn": str(int(raw)), "tokenIn": tin, "tokenOut": tout, "pathDeep": 3, "slippage": 0.005}, limiter=REF_LIMITER, timeout=12, retries=1)
    if r is None or r.status_code != 200: API.report("ref-finance", False); return None, "error"
    API.report("ref-finance", True)
    out = fnum(jget(r, "result_data", "amount_out"))
    if jget(r, "result_code") not in (0, "0", None) or out <= 0: return None, "noroute"
    return {"out": out, "impact": None}, "ok"
def dex_buy(ch, token, dec, usd):
    """Nap usd vao DEX -> nhan bao nhieu token."""
    k = chain_kind(ch)
    if k == "evm": q, kind = evm_quote(ch, True, token, dec, usd)
    elif k == "sol":
        j, kind = jup_quote(SOL_USDC, token, usd * 1e6)
        q = {"out": j["out"] / 10**dec, "impact": j["impact"], "gas_usd": None, "src": "Jupiter"} if j else None
    else:
        j, kind = ref_quote(NEAR_USDC, token, usd * 1e6)
        q = {"out": j["out"] / 10**dec, "impact": None, "gas_usd": None, "src": "Ref Finance"} if j else None
    if not q or q["out"] <= 0: return None, kind if not q else "noroute"
    q["price"] = usd / q["out"]; return q, "ok"
def dex_sell(ch, token, dec, tokens):
    """Ban tokens tren DEX -> nhan bao nhieu USD."""
    if tokens <= 0: return None, "noroute"
    k = chain_kind(ch)
    if k == "evm": q, kind = evm_quote(ch, False, token, dec, tokens)
    elif k == "sol":
        j, kind = jup_quote(token, SOL_USDC, tokens * 10**dec)
        q = {"out": j["out"] / 1e6, "impact": j["impact"], "gas_usd": None, "src": "Jupiter"} if j else None
    else:
        j, kind = ref_quote(token, NEAR_USDC, tokens * 10**dec)
        q = {"out": j["out"] / 1e6, "impact": None, "gas_usd": None, "src": "Ref Finance"} if j else None
    if not q or q["out"] <= 0: return None, kind if not q else "noroute"
    q["price"] = q["out"] / tokens; return q, "ok"

# ============================ TAX & BAO MAT ============================
SEC_CACHE = {}; SEC_LOCK = threading.Lock()
GP_HARD = {"is_honeypot": "honeypot", "cannot_sell_all": "cannot sell all", "owner_change_balance": "owner can change balances", "cannot_buy": "cannot buy"}
GP_SOFT = {"transfer_pausable": "transfers pausable", "is_blacklisted": "has blacklist", "slippage_modifiable": "owner can change tax",
           "hidden_owner": "hidden owner", "trading_cooldown": "trading cooldown", "can_take_back_ownership": "can reclaim ownership", "is_mintable": "mintable",
           "personal_slippage_modifiable": "per-wallet tax", "external_call": "external calls"}
def _pct(v):
    v = fnum_or_none(v)
    if v is None: return None
    if v > 1: v = v / 100.0
    return max(0.0, min(v, 1.0))
def _honeypot(chain_id, token):
    r = http_get(HONEYPOT_URL, params={"address": token, "chainID": chain_id}, limiter=SEC_LIMITER, timeout=12, retries=1)
    ok = r is not None and r.status_code == 200; API.report("honeypot.is", ok)
    if not ok: return None
    j = jget(r, default={}) or {}
    if not j.get("simulationSuccess"):
        return {"bad": "honeypot.is: buy/sell simulation failed"} if (j.get("honeypotResult") or {}).get("isHoneypot") else None
    sim = j.get("simulationResult") or {}
    bad = "honeypot" if (j.get("honeypotResult") or {}).get("isHoneypot") else None
    return {"buy": _pct(sim.get("buyTax")) or 0.0, "sell": _pct(sim.get("sellTax")) or 0.0, "transfer": _pct(sim.get("transferTax")) or 0.0, "bad": bad}
def _goplus(chain_id, token):
    r = http_get(f"{GOPLUS_URL}/{chain_id}", params={"contract_addresses": token}, limiter=SEC_LIMITER, timeout=12, retries=1)
    ok = r is not None and r.status_code == 200; API.report("goplus", ok)
    if not ok: return None
    res = jget(r, "result", default={}) or {}
    d = res.get(token.lower()) or res.get(token) or {}
    if not d: return None
    bad = next((n for k, n in GP_HARD.items() if str(d.get(k)) == "1"), None)
    soft = [n for k, n in GP_SOFT.items() if str(d.get(k)) == "1"]
    bt, stx, tt = _pct(d.get("buy_tax")), _pct(d.get("sell_tax")), _pct(d.get("transfer_tax"))
    if str(d.get("is_open_source")) == "0": soft.append("contract not verified")
    return {"buy": bt, "sell": stx, "transfer": tt, "bad": bad, "soft": soft, "known": bt is not None and stx is not None}
def _sol_sec(token):
    j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [token, {"encoding": "jsonParsed"}]})
    data = ((((j or {}).get("result") or {}).get("value")) or {}).get("data")
    if not isinstance(data, dict): return None
    info = (data.get("parsed") or {}).get("info") or {}
    if not info: return None
    soft, bad, fee = [], None, 0.0
    if info.get("freezeAuthority"): soft.append("freeze authority active")
    if info.get("mintAuthority"): soft.append("mint authority active")
    for ext in info.get("extensions") or []:
        e = ext.get("extension"); st = ext.get("state") or {}
        if e == "transferFeeConfig":
            nf = st.get("newerTransferFee") or {}; of = st.get("olderTransferFee") or {}
            fee = max(fee, fnum(nf.get("transferFeeBasisPoints", of.get("transferFeeBasisPoints", 0))) / 10000.0)
        elif e in ("permanentDelegate", "nonTransferable"): bad = e
        elif e == "transferHook": soft.append("transfer hook")
        elif e == "pausableConfig": soft.append("pausable")
    return {"buy": fee, "sell": fee, "transfer": fee, "bad": bad, "soft": soft, "known": True, "src": "Solana RPC"}
def get_security(ch, token):
    key = (ch, norm_addr(ch, token))
    with SEC_LOCK:
        c = SEC_CACHE.get(key)
        if c and time.time() - c[0] < (3600 if c[1]["known"] else 600): return c[1]
    cfg = CHAINS[ch]
    sec = {"known": False, "buy": 0.0, "sell": 0.0, "transfer": 0.0, "bad": None, "soft": [], "src": "no source"}
    try:
        if cfg["kind"] == "sol":
            s = _sol_sec(token); API.report("sol-rpc", s is not None)
            if s: sec = s
        elif cfg["kind"] == "near":
            sec = {"known": True, "buy": 0.0, "sell": 0.0, "transfer": 0.0, "bad": None, "soft": [], "src": "NEP-141 (no tax mechanism)"}
        else:
            cid = cfg["chain_id"]; srcs = []; buy = sell = tr = None; bad = None; soft = []
            if cid in HONEYPOT_CHAINS:
                h = _honeypot(cid, token)
                if h:
                    srcs.append("honeypot.is"); bad = bad or h.get("bad")
                    if "buy" in h: buy, sell, tr = h["buy"], h["sell"], h["transfer"]
            if cid in GOPLUS_CHAINS:
                g = _goplus(cid, token)
                if g:
                    srcs.append("GoPlus"); bad = bad or g["bad"]; soft += g["soft"]
                    if g["known"]:
                        buy = max(buy or 0.0, g["buy"]); sell = max(sell or 0.0, g["sell"])
                        if g["transfer"] is not None: tr = max(tr or 0.0, g["transfer"])
            has_source = cid in HONEYPOT_CHAINS or cid in GOPLUS_CHAINS
            sec = {"known": buy is not None and sell is not None, "buy": buy or 0.0, "sell": sell or 0.0, "transfer": tr or 0.0,
                   "bad": bad, "soft": soft, "src": "+".join(srcs) or "no source", "nosource": not has_source}
    except Exception as e: log.warning("Security loi %s %s: %s", ch, token[:10], e)
    with SEC_LOCK:
        SEC_CACHE[key] = (time.time(), sec)
        if len(SEC_CACHE) > 5000: SEC_CACHE.pop(next(iter(SEC_CACHE)), None)
    return sec
def effective_tax(sec):
    """Tax dung de tinh lai. Neu chua xac minh: gia dinh UNKNOWN_TAX_ASSUME (che do assume)."""
    if sec["known"]: return sec["buy"], sec["sell"], sec["transfer"], False
    if sec.get("nosource"): return NO_SOURCE_TAX, NO_SOURCE_TAX, NO_SOURCE_TAX, False   # mang chua co nguon -> mac dinh 0%
    return max(sec["buy"], UNKNOWN_TAX_ASSUME), max(sec["sell"], UNKNOWN_TAX_ASSUME), sec["transfer"], True

# ============================ TINH LOI NHUAN ============================
class Diag:
    """Ghi ly do loai (dung cho /check)."""
    def __init__(self, on=False): self.on = on; self.lines = []
    def add(self, s):
        if self.on: self.lines.append(s)

def cex_to_dex(ch, tok, pool, depth, sec, dec, usd, mult, fee_cex):
    """Mua usd tren CEX -> rut ve vi -> ban tren DEX."""
    w = walk_usd(depth["asks"], usd)
    if not w: return None, "CEX ask book too thin"
    avg, tokens = w
    slip = (avg / depth["asks"][0][0] - 1) * 100
    if slip > MAX_CEX_SLIP_PCT: return None, f"CEX slippage {slip:.1f}%"
    t_after_fee = tokens * (1 - fee_cex)
    wfee = tok["withdraw_fee"] * mult
    if t_after_fee < tok["withdraw_min"] * mult: return None, "below minimum withdrawal"
    _, sell_tax, tr_tax, assumed = effective_tax(sec)
    t_received = (t_after_fee - wfee) * (1 - tr_tax)
    if t_received <= 0: return None, "withdraw fee exceeds token amount"
    q, kind = dex_sell(ch, tok["contract"], dec, t_received)
    if not q: return None, f"DEX sell quote: {kind}"
    gas = q["gas_usd"] if q.get("gas_usd") else CHAINS[ch]["gas_usd"]
    usd_out = q["out"] * (1 - sell_tax) * (1 - EXTRA_SLIPPAGE)
    net = usd_out - gas - usd
    return {"direction": "CEX_TO_DEX", "usd_size": usd, "cex_avg": avg, "cex_slip": slip, "tokens_cex": tokens, "tokens_after_fee": t_after_fee,
            "withdraw_fee_tok": wfee, "withdraw_fee_usd": wfee * avg, "tokens_received": t_received, "dex_quote_usd": q["out"], "dex_price": q["price"],
            "impact": q["impact"], "usd_out": usd_out, "gas": gas, "net_usd": net, "net_pct": net / usd * 100,
            "gross_pct": (q["price"] / avg - 1) * 100, "quote_src": q["src"], "sell_tax": sell_tax, "transfer_tax": tr_tax, "buy_tax": 0.0, "tax_assumed": assumed}, "ok"

def dex_to_cex(ch, tok, pool, depth, sec, dec, usd, mult, fee_cex):
    """Mua usd tren DEX -> nap len CEX -> ban vao so lenh mua CEX."""
    buy_tax, _, tr_tax, assumed = effective_tax(sec)
    q, kind = dex_buy(ch, tok["contract"], dec, usd)
    if not q: return None, f"DEX buy quote: {kind}"
    t_after_buy = q["out"] * (1 - buy_tax) * (1 - EXTRA_SLIPPAGE)
    t_deposit = t_after_buy * (1 - tr_tax)
    proceeds = walk_tokens(depth["bids"], t_deposit)
    if not proceeds: return None, "CEX bid book cannot absorb all tokens"
    avg = proceeds / t_deposit; slip = (1 - avg / depth["bids"][0][0]) * 100
    if slip > MAX_CEX_SLIP_PCT: return None, f"CEX slippage {slip:.1f}%"
    gas_buy = q["gas_usd"] if q.get("gas_usd") else CHAINS[ch]["gas_usd"]
    gas = gas_buy + CHAINS[ch]["gas_usd"]          # gas swap + gas chuyen len san
    revenue = proceeds * (1 - fee_cex); cost = usd + gas; net = revenue - cost
    return {"direction": "DEX_TO_CEX", "usd_size": usd, "dex_price": q["price"], "impact": q["impact"], "tokens_dex": q["out"], "tokens_after_buy": t_after_buy,
            "tokens_deposit": t_deposit, "cex_avg": avg, "cex_slip": slip, "proceeds": proceeds, "revenue": revenue, "gas": gas,
            "net_usd": net, "net_pct": net / cost * 100, "gross_pct": (avg / q["price"] - 1) * 100, "quote_src": q["src"],
            "buy_tax": buy_tax, "transfer_tax": tr_tax, "sell_tax": 0.0, "tax_assumed": assumed}, "ok"

def size_cap(pool, depth, direction):
    cap_dex = pool["liq_total"] * SIZE_LIQ_MAX_FRAC
    side = depth["asks"] if direction == "CEX_TO_DEX" else depth["bids"]
    cap_cex = depth_usd(side, MAX_CEX_SLIP_PCT) * 0.9
    return min(ORDER_SIZE_MAX, cap_dex, cap_cex), cap_dex, cap_cex

def best_size(fn, direction, ch, tok, pool, depth, sec, dec, mult, fee_cex, diag, fixed=None):
    """Thu cac muc size tu nho den lon (chi phi co dinh nhu phi rut/gas lam size qua nho bi thiet, size lon bi truot gia).
    SIZE_MODE=pct: chon size co % lai cao nhat (mac dinh) | usd: chon size co $ lai cao nhat. Ca hai deu phai dat nguong."""
    if fixed:
        r, why = fn(ch, tok, pool, depth, sec, dec, fixed, mult, fee_cex)
        return r, [(fixed, r["net_pct"] if r else None)], why
    cap, cap_dex, cap_cex = size_cap(pool, depth, direction)
    if cap < ORDER_SIZE_MIN:
        diag.add(f"{direction}: max size ${cap:.0f} < ${ORDER_SIZE_MIN:.0f} (DEX allows ${cap_dex:.0f}, CEX book allows ${cap_cex:.0f})")
        return None, [], "thin"
    steps = sorted({s for s in SIZE_STEPS if ORDER_SIZE_MIN <= s <= cap} | ({math.floor(cap / 10) * 10} if cap < ORDER_SIZE_MAX else set()))
    tried, res, last_why, falling = [], {}, "", 0
    for s in steps:
        if len(tried) >= MAX_SIZE_QUOTES: break
        r, why = fn(ch, tok, pool, depth, sec, dec, s, mult, fee_cex)
        tried.append((s, r["net_pct"] if r else None)); last_why = why; res[s] = r
        if not r: break                                   # so lenh/quote khong du o size nay -> size lon hon cung khong du
        if len(tried) == 1 and r["net_pct"] < 0: break    # lo ngay o size nho nhat -> bo
        prev = [x for x in res.values() if x and x is not r]
        if prev and r["net_pct"] < max(x["net_pct"] for x in prev):
            falling += 1
            if SIZE_MODE == "pct" or r["net_pct"] < MIN_NET_PCT or falling >= 2: break
    ok = [r for r in res.values() if r and r["net_pct"] >= MIN_NET_PCT and r["net_usd"] >= MIN_NET_USD]
    if not ok:
        top = max((r for r in res.values() if r), key=lambda x: x["net_pct"], default=None)
        return None, tried, (f"best profit {top['net_pct']:.2f}% (${top['net_usd']:.2f}) < threshold" if top else last_why)
    best = max(ok, key=(lambda x: (x["net_pct"], x["net_usd"])) if SIZE_MODE == "pct" else (lambda x: x["net_usd"]))
    return best, tried, "ok"

def evaluate(ch, tok, pool, depth, diag=None, fixed=None, only_dir=None):
    """Tra ve danh sach ket qua dat nguong (toi da 1 moi chieu)."""
    diag = diag or Diag(); st = STATS[ch]; ex = EXCHANGES[tok["cex"]]
    mult = unit_mult(tok["symbol"], pool["dex_symbol"]); d = scale_depth(depth, mult)
    bid, ask = d["bids"][0][0], d["asks"][0][0]; mid = (bid + ask) / 2
    spread = (ask - bid) / ask * 100
    if spread > MAX_CEX_SPREAD: diag.add(f"spread CEX {spread:.2f}% > {MAX_CEX_SPREAD}%"); return []
    dev = abs(mid - pool["price"]) / pool["price"]
    lim_dev = MAX_PRICE_DEV_INFERRED if tok.get("inferred") else MAX_PRICE_DEV
    if dev > lim_dev:
        st.inc("contract_skip"); diag.add(f"CEX price deviates {dev*100:.0f}% from DEX > {lim_dev*100:.0f}% -> likely different token / redenomination"); return []
    sec = get_security(ch, tok["contract"])
    if sec["bad"]: st.inc("tax_skip"); diag.add(f"security: {sec['bad']}"); return []
    if max(sec["buy"], sec["sell"], sec["transfer"]) > TAX_MAX: st.inc("tax_skip"); diag.add(f"tax too high (buy {sec['buy']*100:.1f}% / sell {sec['sell']*100:.1f}% / transfer {sec['transfer']*100:.1f}%)"); return []
    if not sec["known"] and not sec.get("nosource") and UNKNOWN_TAX_MODE == "skip": st.inc("tax_skip"); diag.add("tax could not be verified (UNKNOWN_TAX_MODE=skip)"); return []
    dec = get_decimals(ch, tok["contract"], pool["price"])
    if dec is None: st.inc("quote_fail"); diag.add("cannot read decimals (check RPC)"); return []
    fee_cex = ex.taker_fee(tok["cex_pair"]); out = []
    plans = []
    if status_ok(tok.get("withdraw_enable")) and tok.get("withdraw_fee") is not None: plans.append(("CEX_TO_DEX", cex_to_dex))
    else: diag.add(f"CEX→DEX: withdraw {status_icon(tok.get('withdraw_enable'))}, withdraw fee {'?' if tok.get('withdraw_fee') is None else tok['withdraw_fee']}")
    if status_ok(tok.get("deposit_enable")): plans.append(("DEX_TO_CEX", dex_to_cex))
    else: diag.add(f"DEX→CEX: deposit {status_icon(tok.get('deposit_enable'))}")
    for direction, fn in plans:
        if only_dir and direction != only_dir: continue
        st.inc("evaluated")
        r, tried, why = best_size(fn, direction, ch, tok, pool, d, sec, dec, mult, fee_cex, diag, fixed)
        diag.add(f"{direction}: " + (", ".join(f"${s:.0f}→{p:+.2f}%" if p is not None else f"${s:.0f}→x" for s, p in tried) or "-") + f" | {why}")
        if why == "thin": st.inc("thin")
        if not r:
            if "quote" in why: st.inc("quote_fail")
            continue
        imp = r["impact"]
        if imp is not None and imp > MAX_IMPACT_PCT: diag.add(f"{direction}: impact DEX {imp:.2f}% > {MAX_IMPACT_PCT}%"); continue
        if abs(r["dex_price"] - pool["price"]) / pool["price"] > 0.25: st.inc("suspect"); diag.add(f"{direction}: quote price deviates >25% from pool -> skipped"); continue
        r.update(cex=tok["cex"], mult=mult, cex_fee=fee_cex, cex_bid=bid, cex_ask=ask, spread=spread, sec=sec, tried=tried, checked_at=time.time(),
                 withdraw_enable=tok.get("withdraw_enable"), deposit_enable=tok.get("deposit_enable"), inferred=tok.get("inferred", False))
        out.append(r)
    return out

# ============================ TELEGRAM ============================
_TAG_RE = re.compile(r"<[^>]+>")
def send_telegram(text, chat_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id or CHAT_ID, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    for _ in range(4):
        try:
            TG_LIMITER.wait()
            r = session().post(url, data=payload, timeout=15)
            if r.status_code == 200: return True
            if r.status_code == 429:
                try: ra = float(r.json().get("parameters", {}).get("retry_after", 5))
                except Exception: ra = 5
                time.sleep(min(ra, 30)); continue
            if r.status_code == 400 and payload.get("parse_mode"):
                log.warning("Telegram 400 (HTML loi): %s -> gui dang text", r.text[:150])
                payload = {"chat_id": payload["chat_id"], "text": html.unescape(_TAG_RE.sub("", text))[:4000], "disable_web_page_preview": True}; continue
            log.warning("Telegram that bai %s: %s", r.status_code, r.text[:200]); return False
        except Exception as e: log.warning("Telegram loi: %s", e); time.sleep(3)
    return False
def send_long(text):
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) > 3800: send_telegram(chunk); chunk = ""
        chunk += line + "\n"
    if chunk.strip(): send_telegram(chunk)

def fmt_price(v):
    v = fnum(v)
    if v == 0: return "0"
    if abs(v) >= 100: return f"{v:,.2f}"
    if abs(v) >= 1: return f"{v:.4f}"
    if abs(v) >= 0.0001: return f"{v:.6f}"
    s = f"{v:.12f}".rstrip("0"); return s
def fmt_tok(v):
    v = fnum(v)
    if v >= 1e9: return f"{v/1e9:,.2f}B"
    if v >= 1e6: return f"{v/1e6:,.2f}M"
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.4f}"
def fmt_usd(v): return f"${fnum(v):,.0f}" if abs(fnum(v)) >= 10000 else f"${fnum(v):,.2f}"
def pct(v): return f"{fnum(v)*100:.1f}%"

def swap_urls(ch, token):
    cfg = CHAINS[ch]
    if cfg["kind"] == "sol": return f"https://jup.ag/swap/USDC-{token}", f"https://jup.ag/swap/{token}-USDC"
    if cfg["kind"] == "near": return f"https://app.ref.finance/#{NEAR_USDC}|{token}", f"https://app.ref.finance/#{token}|{NEAR_USDC}"
    base = (cfg.get("stable") or (NATIVE,))[0]
    return (f"https://kyberswap.com/swap/{cfg['kyber']}/{base}-to-{token}", f"https://kyberswap.com/swap/{cfg['kyber']}/{token}-to-{base}")

FIRST_SEEN = {}; FS_LOCK = threading.Lock()
def spread_age(key):
    with FS_LOCK:
        now = time.time(); v = FIRST_SEEN.get(key)
        if not v or now - v[1] > 900: FIRST_SEEN[key] = [now, now]; return 0.0
        v[1] = now; return (now - v[0]) / 60

def fmt_k(v):
    v = fnum(v)
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"
def px8(v):
    v = fnum(v)
    if v >= 1: return f"{v:,.4f}"
    if v >= 0.0001: return f"{v:.8f}"
    return f"{v:.12f}"
def build_alert(ch, tok, pool, p, tag=""):
    cfg = CHAINS[ch]; ex = EXCHANGES[tok["cex"]]; sym = tok["symbol"]; cex = tok["cex"]; sec = p["sec"]
    cex_url = ex.trade_url(tok["cex_pair"]); buy_url, sell_url = swap_urls(ch, tok["contract"])
    pair = esc(tok["cex_pair"]); c2d = p["direction"] == "CEX_TO_DEX"
    usd = p["usd_size"]; tm = cfg["transfer_min"] + CEX_EXTRA_MIN.get(cex, 5)
    tax_total = (p["sell_tax"] if c2d else p["buy_tax"]) + p["transfer_tax"]
    after_tax = p["gross_pct"] - tax_total * 100
    imp = f"{p['impact']:.2f}%" if p.get("impact") is not None else "n/a"
    hr = "────────────────────────"
    # --- tax ---
    if sec["known"]:
        tax = "0%" if max(sec["buy"], sec["sell"], sec["transfer"]) == 0 else f"buy {pct(sec['buy'])} / sell {pct(sec['sell'])} / transfer {pct(sec['transfer'])}"
    elif sec.get("nosource"): tax = f"{pct(NO_SOURCE_TAX)} (default, no tax source)"
    else: tax = f"assumed {pct(UNKNOWN_TAX_ASSUME)}/{pct(UNKNOWN_TAX_ASSUME)}"
    # --- route ---
    if c2d:
        route = f"📤 <a href=\"{esc_url(cex_url)}\">BUY {pair}</a> → WITHDRAW {esc(cfg['name'])} → <a href=\"{esc_url(sell_url)}\">SELL DEX</a>"
        amount = p["tokens_received"]; wfee_usd = p["withdraw_fee_usd"]
    else:
        route = f"📥 <a href=\"{esc_url(buy_url)}\">BUY DEX</a> → DEPOSIT {cex} → <a href=\"{esc_url(cex_url)}\">SELL {pair}</a>"
        amount = p["tokens_deposit"]; wfee_usd = 0.0
    # --- canh bao ---
    warn = []
    if tok.get("inferred"): warn.append(f"contract inferred by symbol ({cex} does not publish contract) - verify!")
    if not sec["known"] and not sec.get("nosource"): warn.append("no security data")
    if sec.get("bad"): warn.append(sec["bad"])
    if sec.get("soft"): warn.append(", ".join(sec["soft"]))
    if p["gross_pct"] > WARN_GROSS: warn.append(f"gross {p['gross_pct']:.1f}% unusually high - check deposit/withdraw on {cex} app")
    if p.get("age_min", 0) >= 30: warn.append(f"spread alive {p['age_min']:.0f} min - deposit/withdraw may be stuck")
    if pool["age_h"] < 48: warn.append(f"new pool {pool['age_h']:.0f}h")
    if abs(pool["chg_m5"]) >= 5: warn.append(f"volatile 5m {pool['chg_m5']:+.1f}%")
    if p["mult"] != 1: warn.append(f"{cex} lists x{p['mult']:.0f} units - converted")
    fut = MULTI.futures_for(sym)
    dexes = pool.get("dexes") or []
    others = " · ".join(f"{esc(x['dex'])} {fmt_k(x['liq'])}" for x in dexes[:4])
    lines = [
        f"<b>{esc(sym)} [{ch}] {'%s_TO_DEX' % cex if c2d else 'DEX_TO_%s' % cex}</b> | {esc(tag) if tag else 'confirmed'} | {cfg['emoji']}",
        hr,
        f"💰 <b>PROFIT {p['net_pct']:.2f}% ({fmt_usd(p['net_usd'])})</b> after all fees | Gross {p['gross_pct']:.2f}%",
        f"📈 Gross {p['gross_pct']:.2f}% → After tax {after_tax:.2f}% | Impact {imp}",
        f"🛡️ Buffer {EXTRA_SLIPPAGE*100:.2f}% | Threshold {MIN_NET_PCT:.2f}%",
        hr,
        route,
        hr,
        f"💸 Tax: {tax} | 🏦 Deposit: {status_icon(p['deposit_enable'])} | Withdraw: {status_icon(p['withdraw_enable'])}",
        f"💵 Capital ${usd:,.0f} | Amount {fnum(amount):,.0f}",
        f"⛽️ Gas ${p['gas']:.2f} | Withdraw Fee ${wfee_usd:.2f} | {cex} Fee {p['cex_fee']*100:.2f}%",
        hr,
        f"🏦 {cex} {pair}",
        f"Bid {px8(p['cex_bid'])} / Ask {px8(p['cex_ask'])} → Avg ${px8(p['cex_avg'])}",
        f"🦄 {esc(pool['dex'])} {esc(pool['pair_name'])}" + (f" (+{len(dexes)-1} DEX)" if len(dexes) > 1 else ""),
        f"DS ${px8(pool['price'])} → Exec ${px8(p['dex_price'])} ({esc(p['quote_src'])})",
        f"💧 {fmt_usd(pool['liq_total'])} | Vol {fmt_usd(pool['vol24'])} | Tx1h {pool['txns_h1']} | Age {pool['age_h']:.0f}h",
        f"Change 5m {pool['chg_m5']:+.1f}% | 1h {pool['chg_h1']:+.1f}%",
    ]
    if len(dexes) > 1: lines.append(f"DEX: {others}")
    if fut: lines.append(f"🔁 Hedge: futures on {', '.join(fut)}")
    lines.append(hr)
    lines += [f"⚠️ {esc(w)}" for w in warn]
    lines += [
        f"📜 Contract: <code>{esc(tok['contract'])}</code>",
        f"🔗 <a href=\"{esc_url(cex_url)}\">{cex} Trade</a> | <a href=\"{esc_url(buy_url)}\">DEX Buy</a> | <a href=\"{esc_url(sell_url)}\">DEX Sell</a> | "
        f"<a href=\"{esc_url(pool['url'])}\">Info</a> | <a href=\"{esc_url(cfg['explorer'] + tok['contract'])}\">Explorer</a>",
        f"⏰ {datetime.now(VN_TZ).strftime('%H:%M %d/%m')} | Transfer ~{tm:.0f} min | Price changes fast!",
    ]
    return "\n".join(lines)

# ============================ DATABASE ============================
class DB_:
    def __init__(self, path):
        self.lock = threading.Lock(); self.c = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            try: self.c.execute("PRAGMA journal_mode=WAL")
            except Exception: pass
            cols = [r[1] for r in self.c.execute("PRAGMA table_info(signals)").fetchall()]
            if cols and "usd" not in cols:   # DB cu tu ban truoc -> doi ten, tao bang moi
                self.c.execute(f"ALTER TABLE signals RENAME TO signals_old_{int(time.time())}")
                self.c.execute("DROP TABLE IF EXISTS followups")
            self.c.execute("""CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chain TEXT, cex TEXT, symbol TEXT, contract TEXT,
                direction TEXT, usd REAL, gross_pct REAL, net_pct REAL, net_usd REAL, pnl_a REAL, st_a TEXT, pnl_b REAL, st_b TEXT, raw TEXT)""")
            self.c.execute("CREATE TABLE IF NOT EXISTS followups(fid INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, slot TEXT, due REAL, done INTEGER DEFAULT 0)")
            fcols = [r[1] for r in self.c.execute("PRAGMA table_info(followups)").fetchall()]
            if "slot" not in fcols: self.c.execute("DROP TABLE followups"); self.c.execute("CREATE TABLE followups(fid INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, slot TEXT, due REAL, done INTEGER DEFAULT 0)")
            self.c.execute("CREATE TABLE IF NOT EXISTS blacklist(key TEXT PRIMARY KEY, until REAL, reason TEXT)")
            self.c.execute("CREATE TABLE IF NOT EXISTS status_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, cex TEXT, chain TEXT, symbol TEXT, kind TEXT)")
            self.c.execute("UPDATE followups SET done=0 WHERE done=2"); self.c.commit()
    def q(self, sql, args=(), commit=False, one=False):
        with self.lock:
            cur = self.c.execute(sql, args)
            if commit: self.c.commit(); return cur.lastrowid
            return cur.fetchone() if one else cur.fetchall()
    def insert(self, ch, tok, pool, p):
        raw = json.dumps({"tok": tok, "pool": pool, "p": {k: v for k, v in p.items() if k != "sec"}, "sec": p["sec"]}, default=str)
        sid = self.q("INSERT INTO signals(ts,chain,cex,symbol,contract,direction,usd,gross_pct,net_pct,net_usd,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), ch, tok["cex"], tok["symbol"], tok["contract"], p["direction"], p["usd_size"], p["gross_pct"], p["net_pct"], p["net_usd"], raw), commit=True)
        now = time.time()
        for slot, m in zip(("a", "b"), FOLLOWUP_MIN): self.q("INSERT INTO followups(sid,slot,due) VALUES(?,?,?)", (sid, slot, now + m * 60), commit=True)
        return sid
    def due(self):
        rows = self.q("SELECT f.fid,f.sid,f.slot,s.chain,s.direction,s.raw FROM followups f JOIN signals s ON s.id=f.sid WHERE f.done=0 AND f.due<=? ORDER BY f.due LIMIT 20", (time.time(),))
        for r in rows: self.q("UPDATE followups SET done=2 WHERE fid=?", (r[0],), commit=True)
        return rows
    def finish(self, fid, sid, slot, val, st):
        self.q(f"UPDATE signals SET pnl_{slot}=?, st_{slot}=? WHERE id=?", (val, st, sid), commit=True)
        self.q("UPDATE followups SET done=1 WHERE fid=?", (fid,), commit=True)
DB = DB_(DB_PATH)
BL = {k: u for k, u in DB.q("SELECT key, until FROM blacklist WHERE until>?", (time.time(),))}; BL_LOCK = threading.Lock(); BAD_STREAK = {}
def is_blacklisted(ch, tok):
    now = time.time()
    with BL_LOCK:
        return any((BL.get(k) or 0) > now for k in (f"SYM:{norm_sym(tok['symbol'])}", f"TOK:{ch}:{tok['key']}"))

# ============================ QUET ============================
COOLDOWN = {}; CD_LOCK = threading.Lock(); PAUSED = set()
def should_alert(key, net_pct):
    with CD_LOCK: last = COOLDOWN.get(key)
    if not last: return True
    t, p = last; age = time.time() - t
    return age >= COOLDOWN_MIN * 60 or (age >= 300 and net_pct > p * 1.5)
def prefilter(tok, pool, book):
    """Loc nhanh bang gia ticker (khong ton quote)."""
    bt = book.get(tok["cex_pair"])
    if not bt: return False
    mult = unit_mult(tok["symbol"], pool["dex_symbol"]); bid, ask = bt[0] / mult, bt[1] / mult
    hi, lo = pool.get("price_max", pool["price"]), pool.get("price_min", pool["price"]); fee = TAKER_FEE.get(tok["cex"], 0.002)
    imp = min(0.05, 300 / max(pool["liq_total"], 1)) + 0.003   # uoc tinh truot gia + phi pool cho size nho
    g1 = ((hi * (1 - imp)) / (ask * (1 + fee)) - 1) * 100 if status_ok(tok.get("withdraw_enable")) and tok.get("withdraw_fee") is not None else -99   # ban o DEX dat nhat
    g2 = ((bid * (1 - fee)) / (lo * (1 + imp)) - 1) * 100 if status_ok(tok.get("deposit_enable")) else -99                                            # mua o DEX re nhat
    return max(g1, g2) >= MIN_NET_PCT * 0.6
def check_candidate(ch, tok, pool, diag=None):
    try:
        depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"])
        if not depth:
            if diag: diag.add("cannot fetch CEX order book")
            return []
        return [(tok, pool, p) for p in evaluate(ch, tok, pool, depth, diag)]
    except Exception:
        STATS[ch].inc("errors"); log.exception("Loi xu ly %s %s", ch, tok["symbol"]); return []
def confirm(ch, tok, pool, p):
    time.sleep(CONFIRM_DELAY)
    depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"])
    if not depth: return None
    for p2 in evaluate(ch, tok, pool, depth, fixed=p["usd_size"], only_dir=p["direction"]):
        if p2["net_pct"] >= MIN_NET_PCT and p2["net_usd"] >= MIN_NET_USD: return p2
    return None
def process(ch, results, tag=""):
    for tok, pool, p in sorted(results, key=lambda r: -r[2]["net_usd"]):
        key = (ch, tok["cex"], tok["key"], p["direction"])
        p["age_min"] = spread_age(key)
        if not should_alert(key, p["net_pct"]): continue
        p2 = confirm(ch, tok, pool, p)
        if not p2: STATS[ch].inc("unconfirmed"); continue
        p2["age_min"] = p["age_min"]
        if send_telegram(build_alert(ch, tok, pool, p2, tag)):
            with CD_LOCK: COOLDOWN[key] = (time.time(), p2["net_pct"])
            STATS[ch].inc("signals"); DB.insert(ch, tok, pool, p2)
# ---------------- BO LAP LICH QUET DUY NHAT (thay cho 11 luong quet song song) ----------------
# Ly do: 11 luong cung goi DexScreener -> vuot gioi han -> 429 -> ca bot dung. Gio chi 1 luong goi DexScreener,
# toc do co dinh (DEX_RPM), uu tien token "nong" (dang co chenh lech), token khong co pool dat loc thi it quet lai.
DEX_RPM = env_float("DEX_RPM", 50)                      # so request DexScreener / phut (gioi han chinh thuc 300, IP Render dung chung -> de thap)
HOT_SEC = env_float("HOT_SEC", 60); WARM_SEC = env_float("WARM_SEC", 180); COLD_SEC = env_float("COLD_SEC", 1200)
TSTATE = {}; TS_LOCK = threading.Lock()                 # (chain, key) -> {"next": ts, "tier": str}
EVAL_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="Eval"); INFLIGHT = set(); IF_LOCK = threading.Lock()
SCAN_HEALTH = {"last_batch": 0.0, "cycle": 0}
def entry_active(e):
    """Chi quet token co it nhat 1 san dang mo nap hoac rut (va co gia tren san)."""
    return any(status_ok(l.get("deposit_enable")) or (status_ok(l.get("withdraw_enable")) and l.get("withdraw_fee") is not None) for l in e["listings"])
def pick_batch():
    """Chon chain + toi da DEX_BATCH token den han, uu tien hot > warm > cold, chain lau chua quet nhat."""
    now = time.time(); best = None
    for ch, ents in MULTI.by_chain.items():
        if ch in PAUSED or not ents: continue
        due = []
        with TS_LOCK:
            for e in ents:
                stt = TSTATE.get((ch, e["key"]))
                nxt = stt["next"] if stt else 0
                if nxt <= now and entry_active(e): due.append((0 if (stt or {}).get("tier") == "hot" else 1 if (stt or {}).get("tier") == "warm" else 2, nxt, e))
        if not due: continue
        due.sort(key=lambda x: (x[0], x[1])); score = (due[0][0], STATS[ch].last_scan)
        if best is None or score < best[0]: best = (score, ch, [x[2] for x in due[:DEX_BATCH]])
    return (best[1], best[2]) if best else (None, [])
def set_tier(ch, key, tier):
    delay = {"hot": HOT_SEC, "warm": WARM_SEC, "cold": COLD_SEC}[tier]
    with TS_LOCK: TSTATE[(ch, key)] = {"tier": tier, "next": time.time() + delay * (0.8 + 0.4 * (hash(key) % 100) / 100)}
LAST_EVAL = {}; EVAL_COOLDOWN = env_float("EVAL_COOLDOWN", 90)
def eval_async(ch, tok, pool):
    k = (ch, tok["cex"], tok["key"])
    with IF_LOCK:
        if k in INFLIGHT or time.time() - LAST_EVAL.get(k, 0) < EVAL_COOLDOWN: return   # khong quote lai cung token qua day
        INFLIGHT.add(k); LAST_EVAL[k] = time.time()
    def job():
        try:
            res = check_candidate(ch, tok, pool)
            if res: process(ch, res)
        finally:
            with IF_LOCK: INFLIGHT.discard(k)
    EVAL_POOL.submit(job)
def gap_estimate(tok, pool, book):
    bt = book.get(tok["cex_pair"])
    if not bt: return None
    mult = unit_mult(tok["symbol"], pool["dex_symbol"]); bid, ask = bt[0] / mult, bt[1] / mult
    return max((pool.get("price_max", pool["price"]) / ask - 1) * 100, (bid / pool.get("price_min", pool["price"]) - 1) * 100)
def scan_batch(ch, chunk):
    st = STATS[ch]; st.beat()
    pools = fetch_dex_pools(ch, [e["contract"] for e in chunk])
    if pools is None:
        for e in chunk: set_tier(ch, e["key"], "warm")    # loi API: thu lai sau, khong danh dau cold
        st.inc("errors"); return
    books = {n: EXCHANGES[n].get_book() for n in {l["cex"] for e in chunk for l in e["listings"]}}
    for e in chunk:
        pool = pools.get(e["key"])
        if not pool: set_tier(ch, e["key"], "cold"); continue
        st.inc("pools"); best_gap = -99
        for tok in e["listings"]:
            st.inc("scanned")
            if not symbol_ok(tok["symbol"], pool["dex_symbol"]): st.inc("contract_skip"); continue
            if is_blacklisted(ch, tok): continue
            g = gap_estimate(tok, pool, books.get(tok["cex"]) or {})
            if g is not None: best_gap = max(best_gap, g)
            if prefilter(tok, pool, books.get(tok["cex"]) or {}): st.inc("candidates"); eval_async(ch, tok, pool)
        set_tier(ch, e["key"], "hot" if best_gap >= MIN_NET_PCT * 0.4 else "warm")
    st.last_scan = time.time()
def dex_scheduler_loop(stop):
    log.info("Bo lap lich quet: %.0f request DexScreener/phut", DEX_RPM)
    gap = 60.0 / max(DEX_RPM, 1)
    while not stop.is_set():
        try:
            with BACKOFF_LOCK: wait = BACKOFF.get("api.dexscreener.com", 0) - time.time()
            if wait > 0: stop.wait(wait + 1); continue          # dang bi gioi han -> cho, KHONG tinh la loi API
            ch, chunk = pick_batch()
            if not ch: stop.wait(3); continue
            t0 = time.time(); scan_batch(ch, chunk); SCAN_HEALTH["last_batch"] = time.time(); SCAN_HEALTH["cycle"] += 1
            stop.wait(max(0.0, gap - (time.time() - t0)))
        except Exception: log.exception("loi bo lap lich"); stop.wait(10)
def refresher_loop():
    while True:
        try: MULTI.ensure()
        except Exception: log.exception("refresh loi")
        time.sleep(20)
class Worker:
    def __init__(self): self.stop = None; self.thread = None
    def start(self):
        self.stop = threading.Event(); SCAN_HEALTH["last_batch"] = SCAN_HEALTH["last_batch"] or time.time()
        self.thread = threading.Thread(target=dex_scheduler_loop, args=(self.stop,), daemon=True, name="DexScan"); self.thread.start()
    def restart(self):
        if self.stop: self.stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
            if self.thread.is_alive(): log.warning("luong quet cu chua thoat"); return
        self.start()

# ============================ SU KIEN NAP / RUT ============================
KIND_TXT = {"open_withdraw": "🔓 WITHDRAW OPEN", "open_deposit": "🔓 DEPOSIT OPEN", "close_withdraw": "🔒 WITHDRAW CLOSED", "close_deposit": "🔒 DEPOSIT CLOSED"}
PRIO = ThreadPoolExecutor(max_workers=2, thread_name_prefix="PRIO")
def status_title(cex, kinds):
    opened = {k.split("_")[1] for k in kinds if k.startswith("open")}
    closed = {k.split("_")[1] for k in kinds if k.startswith("close")}
    name = lambda s: "Deposits & Withdrawals" if s == {"deposit", "withdraw"} else ("Deposits" if "deposit" in s else "Withdrawals")
    if opened and not closed: return f"🔓 {cex} {name(opened)} Reopened"
    if closed and not opened: return f"🔒 {cex} {name(closed)} Suspended"
    return f"🔄 {cex} Deposit/Withdrawal Status Changed"
def handle_status_events(events):
    for cex, tok, kind in events: DB.q("INSERT INTO status_events(ts,cex,chain,symbol,kind) VALUES(?,?,?,?,?)", (time.time(), cex, tok["chain"], tok["symbol"], kind), commit=True)
    if STATUS_NOTIFY:
        groups = {}
        for cex, tok, kind in events:
            g = groups.setdefault((cex, tok["chain"], tok["key"] or tok["symbol"], tok["symbol"]), {"tok": tok, "kinds": []})
            g["kinds"].append(kind)
        items = sorted(groups.items(), key=lambda kv: (not any(k.startswith("open") for k in kv[1]["kinds"]), kv[0][0], kv[0][3]))
        for (cex, ch, _, sym), g in items[:STATUS_MAX_LINES]:
            tok = g["tok"]; contract = tok["contract"] or (MULTI.resolve(tok) or {}).get("contract", "")
            ct = f"<code>{esc(contract)}</code>" + (" (inferred)" if contract and not tok["contract"] else "") if contract else "not published by exchange"
            send_telegram(f"<b>{status_title(cex, g['kinds'])}</b>\n\n"
                          f"<b>{esc(sym)}</b> [{ch}] — Deposit {status_icon(tok['deposit_enable'])} | Withdrawal {status_icon(tok['withdraw_enable'])}\n\n"
                          f"Contract: {ct}")
        if len(items) > STATUS_MAX_LINES:
            by_cex = Counter(k[0] for k, _ in items[STATUS_MAX_LINES:])
            send_telegram("ℹ️ ... and " + ", ".join(f"{n} more changes on {c}" for c, n in by_cex.items()))
    for cex, tok, kind in events:
        if kind.startswith("open"): PRIO.submit(check_opened, cex, tok, kind)
def check_opened(cex, tok, kind):
    try:
        t = MULTI.resolve(tok); ch = tok["chain"]
        if not t or is_blacklisted(ch, t): return
        pools = fetch_dex_pools(ch, [t["contract"]]); pool = (pools or {}).get(t["key"])
        if not pool or not symbol_ok(t["symbol"], pool["dex_symbol"]): return
        res = check_candidate(ch, t, pool)
        if res: process(ch, res, tag=f"{KIND_TXT[kind]} on {cex}")
    except Exception: log.exception("check_opened loi")

# ============================ THEO DOI KET QUA (PAPER) ============================
def realized(ch, direction, tok, p):
    """Lenh mo tai luc bao, dong sau X phut voi gia THAT luc do."""
    dec = get_decimals(ch, tok["contract"])
    if dec is None: return None, "error"
    if direction == "CEX_TO_DEX":
        q, kind = dex_sell(ch, tok["contract"], dec, p["tokens_received"])
        if kind == "error": return None, "error"
        if not q: return None, "gone"
        out = q["out"] * (1 - p["sell_tax"]) * (1 - EXTRA_SLIPPAGE) - p["gas"]
        return (out - p["usd_size"]) / p["usd_size"] * 100, "ok"
    depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"]) if tok["cex"] in EXCHANGES else None
    if not depth: return None, "error"
    d = scale_depth(depth, p.get("mult", 1.0)); proceeds = walk_tokens(d["bids"], p["tokens_deposit"])
    if not proceeds: return None, "gone"
    cost = p["usd_size"] + p["gas"]
    return (proceeds * (1 - p["cex_fee"]) - cost) / cost * 100, "ok"
def followup_loop():
    while True:
        time.sleep(15)
        try:
            for fid, sid, slot, ch, direction, raw in DB.due():
                val, st = None, "error"
                try:
                    j = json.loads(raw); tok, p = j["tok"], j["p"]
                    val, st = realized(ch, direction, tok, p)
                except Exception as e: log.warning("follow-up loi %s", e)
                DB.finish(fid, sid, slot, val, st)
                if slot == "a" and st in ("ok", "gone"):
                    key = (ch, tok["cex"], tok["key"], direction); win = st == "ok" and (val or 0) > 0
                    BAD_STREAK[key] = 0 if win else BAD_STREAK.get(key, 0) + 1
                    if BAD_STREAK[key] >= BAD_STREAK_BLACKLIST:
                        until = time.time() + BLACKLIST_HOURS * 3600; bk = f"TOK:{ch}:{tok['key']}"
                        with BL_LOCK: BL[bk] = until
                        DB.q("INSERT OR REPLACE INTO blacklist VALUES(?,?,?)", (bk, until, "lo lien tiep"), commit=True); BAD_STREAK[key] = 0
                        log.info("Tu dong blacklist %s %s", ch, tok["symbol"])
        except Exception: log.exception("followup loi")

# ============================ BAO CAO NGAY ============================
def cex_health_lines():
    out = []
    for n in EXCHANGES:
        h = MULTI.health.get(n)
        if not h: out.append(f"⏳ {n}: not loaded yet"); continue
        if not h["ok"]: out.append(f"❌ {n}: {esc(h['err'])}")
        elif not h["verified"]: out.append(f"⚠ {n}: {h['pairs']} pairs, NO network/deposit-withdraw data → {esc(h['err'])}")
        else: out.append(f"✅ {n}: {h['pairs']} pairs, {h['listings']} token-networks")
    return out
def daily_report():
    now = datetime.now(VN_TZ); since = time.time() - 86400
    rows = DB.q("SELECT symbol,chain,cex,direction,net_pct,net_usd,pnl_a,st_a FROM signals WHERE ts>? ORDER BY net_usd DESC", (since,))
    ev = DB.q("SELECT kind, COUNT(*) FROM status_events WHERE ts>? GROUP BY kind", (since,))
    done = [r for r in rows if r[7] in ("ok", "gone")]; wins = [r for r in done if r[7] == "ok" and (r[6] or 0) > 0]
    L = [f"📊 <b>DAILY REPORT {now.strftime('%d/%m/%Y')} · {VERSION}</b>",
         f"Signals (24h): <b>{len(rows)}</b> · total est. profit {fmt_usd(sum(r[5] or 0 for r in rows))}",
         (f"Paper check after {FOLLOWUP_MIN[0]} min: won {len(wins)}/{len(done)} ({len(wins)/len(done)*100:.0f}%)" if done else "Paper check: no data yet")]
    if rows:
        L.append("\n<b>Top signals:</b>")
        for s, ch, cx, d, npct, nusd, pa, sa in rows[:8]:
            res = f"→ {pa:+.1f}%" if sa == "ok" and pa is not None else (f"→ {sa}" if sa else "")
            L.append(f"• {esc(s)} [{ch}] {'DEX→' + cx if d == 'DEX_TO_CEX' else cx + '→DEX'}: {npct:+.2f}% ({fmt_usd(nusd)}) {res}")
    L.append("\n<b>Scan by chain (24h):</b>")
    for k, st in STATS.items():
        s = st.snapshot(daily=True)
        L.append(f"{CHAINS[k]['emoji']} {k}: pools {s.get('pools',0)} · candidates {s.get('candidates',0)} · signals {s.get('signals',0)} · unconfirmed {s.get('unconfirmed',0)} · tax skip {s.get('tax_skip',0)} · contract/price mismatch {s.get('contract_skip',0)} · thin {s.get('thin',0)}")
    if ev: L.append("\nDeposit/withdraw changes: " + ", ".join(f"{KIND_TXT.get(k, k)} {c}" for k, c in ev))
    L.append("\n<b>Exchanges:</b>"); L += cex_health_lines()
    L.append("\nAPI: " + esc(API.line()))
    send_long("\n".join(L))
    for st in STATS.values(): st.reset_day()
    API.reset()
def scheduler_loop():
    last = None
    while True:
        time.sleep(20)
        try:
            now = datetime.now(VN_TZ)
            if now.hour == DAILY_REPORT_HOUR and now.minute < 5 and last != now.date():
                last = now.date(); daily_report()
        except Exception: log.exception("scheduler loi")

# ============================ LENH TELEGRAM ============================
def cmd_check(sym):
    """Chan doan: vi sao token khong duoc bao."""
    s = norm_sym(sym); lines = [f"🔍 <b>CHECK {esc(sym)}</b>"]; found = False
    for ch in CHAINS:
        for e in MULTI.by_chain.get(ch, []):
            lst = [l for l in e["listings"] if norm_sym(l["symbol"]) == s]
            if not lst: continue
            found = True
            pools = fetch_dex_pools(ch, [e["contract"]]); pool = (pools or {}).get(e["key"])
            lines.append(f"\n{CHAINS[ch]['emoji']} <b>{CHAINS[ch]['name']}</b> <code>{esc(e['contract'])}</code>")
            if not pool:
                lines.append(f"  ✗ No pool passes filters (liq ≥ {fmt_usd(MIN_LIQ_USD)}, vol24h ≥ {fmt_usd(MIN_VOL_24H)}, tx1h ≥ {MIN_TXNS_H1}, age ≥ {MIN_POOL_AGE_H:.0f}h)"); continue
            lines.append(f"  DEX: {len(pool['dexes'])} DEXs / {pool['n_pools']} pools · avg price {fmt_price(pool['price'])} (low {fmt_price(pool['price_min'])} - high {fmt_price(pool['price_max'])}) · liq {fmt_usd(pool['liq_total'])}")
            lines.append("  " + ", ".join(f"{esc(x['dex'])} {fmt_usd(x['liq'])}" for x in pool["dexes"][:6]))
            for tok in lst:
                d = Diag(True); res = check_candidate(ch, tok, pool, d)
                lines.append(f"  • {tok['cex']}{' (contract inferred)' if tok.get('inferred') else ''}: deposit {status_icon(tok['deposit_enable'])} withdraw {status_icon(tok['withdraw_enable'])}")
                lines += [f"     - {esc(x)}" for x in d.lines[:8]]
                if res:
                    ok_txt = ", ".join("%s %+.2f%%" % (p["direction"], p["net_pct"]) for _, _, p in res)
                    lines.append(f"     ✅ PASS: {ok_txt}")
    nocontract = [(n, t) for n, ts in MULTI.tokens.items() for t in ts if norm_sym(t["symbol"]) == s and not t["contract"]]
    for n, t in nocontract[:6]:
        lines.append(f"• {n} [{t['chain']}]: exchange does not publish contract" + (" and it cannot be matched (0 or >1 contracts with same symbol)" if not MULTI.resolve(t) else ""))
    if not found and not nocontract: lines.append("Token not found on scanned chains/exchanges (or exchange has no network data).")
    return "\n".join(lines)
def handle_command(text):
    parts = text.strip().split(); cmd = parts[0].lower().split("@")[0]; arg = parts[1].upper() if len(parts) > 1 else ""
    if cmd in ("/start", "/help"):
        return ("<b>Commands</b>\n/status - bot & exchange status\n/check SYMBOL - why a token has / has no signal\n/deposit SYMBOL - deposit/withdraw status per chain & exchange\n"
                "/top - last 10 signals\n/report - send daily report now\n/networks - unrecognized network names\n/config - current settings\n"
                "/pause CHAIN · /resume CHAIN\n/blacklist SYMBOL · /unblacklist SYMBOL")
    if cmd == "/status":
        L = [f"<b>STATUS {VERSION}</b> {datetime.now(VN_TZ).strftime('%H:%M %d/%m')}"] + cex_health_lines() + [""]
        for k, st in STATS.items():
            s = st.snapshot(daily=True); ago = int(time.time() - st.last_scan) if st.last_scan else -1
            ents = MULTI.by_chain.get(k, []); act = sum(1 for e in ents if entry_active(e))
            if not ents: L.append(f"{CHAINS[k]['emoji']} {k}: no tokens listed on scanned exchanges"); continue
            with TS_LOCK: tiers = Counter(TSTATE[(k, e["key"])]["tier"] for e in ents if (k, e["key"]) in TSTATE)
            L.append(f"{CHAINS[k]['emoji']} {k}{' (PAUSED)' if k in PAUSED else ''}: tokens {len(ents)} (active {act}, hot {tiers.get('hot',0)}, warm {tiers.get('warm',0)}, cold {tiers.get('cold',0)})")
            src = Counter(l["cex"] for e in ents for l in e["listings"])
            L.append("     by exchange: " + ", ".join(f"{c} {n}" for c, n in src.most_common(5)))
            L.append(f"     {s.get('pools',0)} pools seen · {s.get('candidates',0)} candidates · {s.get('signals',0)} signals · last scan {ago}s ago")
        L.append(f"\nMemory: {rss_mb():.0f} MB (Render Free limit 512 MB)")
        lb = f"{int(time.time() - SCAN_HEALTH['last_batch'])}s ago" if SCAN_HEALTH["last_batch"] else "not started (loading exchange data...)"
        L.append(f"Scanner: {SCAN_HEALTH['cycle']} batches done · last batch {lb} · eval queue {len(INFLIGHT)}")
        tot = DB.q("SELECT COUNT(*) FROM signals WHERE ts>?", (time.time() - 86400,), one=True)[0]
        L.append(f"Signals sent (24h): {tot}")
        L.append("API: " + esc(API.line())); return "\n".join(L)
    if cmd == "/check": return cmd_check(arg) if arg else "Usage: /check SYMBOL"
    if cmd == "/report": daily_report(); return "Report sent."
    if cmd == "/networks":
        with UNKNOWN_LOCK: top = UNKNOWN_NETS.most_common(40)
        return "<b>Unrecognized network names</b> (add via EXTRA_ALIASES if needed):\n" + ", ".join(f"{esc(n)}({c})" for n, c in top)
    if cmd == "/config":
        return (f"Net profit ≥ {MIN_NET_PCT}% and ≥ {fmt_usd(MIN_NET_USD)} · size {fmt_usd(ORDER_SIZE_MIN)}–{fmt_usd(ORDER_SIZE_MAX)} (≤ {SIZE_LIQ_MAX_FRAC*100:.0f}% liq)\n"
                f"Pool: liq ≥ {fmt_usd(MIN_LIQ_USD)}, vol ≥ {fmt_usd(MIN_VOL_24H)}, tx1h ≥ {MIN_TXNS_H1}, age ≥ {MIN_POOL_AGE_H:.0f}h\n"
                f"Size mode: {'highest %' if SIZE_MODE == 'pct' else 'highest $'} · quote: {'Kyber + ParaSwap (best of)' if MULTI_AGG else 'Kyber'}\n"
                f"Impact ≤ {MAX_IMPACT_PCT}% · CEX slippage ≤ {MAX_CEX_SLIP_PCT}% · tax ≤ {TAX_MAX*100:.0f}% · chains without tax source: default {NO_SOURCE_TAX*100:.0f}% · tax lookup failed: {UNKNOWN_TAX_MODE} ({UNKNOWN_TAX_ASSUME*100:.0f}%)\n"
                f"Inferred contracts: {'on' if ALLOW_INFERRED_CONTRACT else 'off'} · unverified status: {'allowed' if ALLOW_UNVERIFIED_STATUS else 'skipped'}\n"
                f"Chains: {', '.join(CHAINS)}\nExchanges: {', '.join(EXCHANGES)}")
    if cmd == "/top":
        rows = DB.q("SELECT id,symbol,chain,cex,direction,net_pct,usd,pnl_a,st_a FROM signals ORDER BY id DESC LIMIT 10")
        if not rows: return "No signals yet."
        return "<b>Last 10 signals</b>\n" + "\n".join(f"#{i} {esc(s)} [{ch}] {'DEX→' + cx if d == 'DEX_TO_CEX' else cx + '→DEX'} {n:+.2f}% {fmt_usd(u)} → {f'{pa:+.2f}%' if pa is not None else (sa or '...')}" for i, s, ch, cx, d, n, u, pa, sa in rows)
    if cmd == "/deposit":
        if not arg: return "Usage: /deposit SYMBOL"
        L = [f"<b>DEPOSIT/WITHDRAW {esc(arg)}</b> (❓ = not published by exchange)"]
        for n, toks in MULTI.tokens.items():
            for t in toks:
                if norm_sym(t["symbol"]) == norm_sym(arg):
                    fee = "?" if t["withdraw_fee"] is None else fmt_tok(t["withdraw_fee"])
                    L.append(f"{n} · {CHAINS[t['chain']]['name']} ({esc(t['raw_network'])}): Deposit {status_icon(t['deposit_enable'])} Withdraw {status_icon(t['withdraw_enable'])} · withdraw fee {fee}" + ("" if t["contract"] else " · no contract"))
        return "\n".join(L) if len(L) > 1 else f"{esc(arg)} not found on scanned chains."
    if cmd == "/pause":
        if arg in CHAINS: PAUSED.add(arg); return f"Paused {arg}"
        return "Valid chains: " + ", ".join(CHAINS)
    if cmd == "/resume": PAUSED.discard(arg); return f"Resumed {arg}"
    if cmd == "/blacklist" and arg:
        k = f"SYM:{norm_sym(arg)}"; until = time.time() + 7 * 86400
        with BL_LOCK: BL[k] = until
        DB.q("INSERT OR REPLACE INTO blacklist VALUES(?,?,?)", (k, until, "manual"), commit=True); return f"Blacklisted {esc(arg)} for 7 days"
    if cmd == "/unblacklist" and arg:
        k = f"SYM:{norm_sym(arg)}"
        with BL_LOCK: BL.pop(k, None)
        DB.q("DELETE FROM blacklist WHERE key=?", (k,), commit=True); return f"Removed {esc(arg)} from blacklist"
    return "Unknown command. /help"
def telegram_poll_loop():
    offset = None; c409 = [0]
    try:   # webhook cu (tu bot khac / ban cu) lam getUpdates bi 409 -> go bo
        r = session().post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", data={"drop_pending_updates": "false"}, timeout=15)
        log.info("Telegram deleteWebhook: %s", r.text[:120])
        me = session().get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=15).json().get("result", {})
        log.info("Telegram bot: @%s", me.get("username"))
    except Exception as e: log.warning("deleteWebhook loi: %s", e)
    while True:
        try:
            r = session().get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"timeout": 25, "offset": offset}, timeout=35)
            if r.status_code == 409:
                c409[0] += 1
                if c409[0] in (1, 20) or c409[0] % 120 == 0: log.warning("Telegram 409: ANOTHER instance is polling this bot token (old Render service / PC / other host). Telegram commands will NOT work until it is stopped. Alerts still send. Detail: %s", r.text[:150])
                time.sleep(30); continue
            if r.status_code != 200: time.sleep(10); continue
            c409[0] = 0
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1; m = u.get("message") or u.get("channel_post") or {}
                if str((m.get("chat") or {}).get("id")) != CHAT_ID or not m.get("text", "").startswith("/"): continue
                def run(txt=m["text"]):
                    try: send_long(handle_command(txt))
                    except Exception: log.exception("lenh loi")
                threading.Thread(target=run, daemon=True).start()
        except Exception as e: log.warning("Poll Telegram loi: %s", e); time.sleep(10)

# ============================ WEB (Render can cong mo) ============================
def start_web():
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        import http.server, socketserver
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(f"{VERSION} OK".encode())
            def log_message(self, *a): pass
        socketserver.TCPServer(("0.0.0.0", PORT), H).serve_forever(); return
    app = Flask(__name__)
    def authed(): return bool(ADMIN_TOKEN) and hmac.compare_digest(request.args.get("token") or "", ADMIN_TOKEN)
    def plain(txt): return (html.unescape(_TAG_RE.sub("", txt)), 200, {"Content-Type": "text/plain; charset=utf-8"})
    @app.route("/")
    def home():
        try: return plain(handle_command("/status"))
        except Exception as e: return plain(f"{VERSION} running (status error: {e})")
    @app.route("/check/<sym>")
    def check(sym): return plain(cmd_check(sym.upper()))
    @app.route("/debug/networks")
    def dbg_networks():
        """Moi san / moi chain: ten mang goc va vi du token -> phat hien san gan nham chain."""
        out = []
        for n, toks in MULTI.tokens.items():
            by = {}
            for t in toks: by.setdefault(t["chain"], Counter())[t["raw_network"]] += 1
            for ch, cnt in sorted(by.items()):
                ex_syms = [t["symbol"] for t in toks if t["chain"] == ch][:8]
                out.append(f"{n} -> {ch}: {sum(cnt.values())} | raw names: {dict(cnt.most_common(5))} | e.g. {', '.join(ex_syms)}")
        with UNKNOWN_LOCK: out.append("\nUnrecognized: " + ", ".join(f"{k}({v})" for k, v in UNKNOWN_NETS.most_common(40)))
        return plain("\n".join(out))
    @app.route("/config")
    def config(): return plain(handle_command("/config"))
    @app.route("/health")
    def health(): return "OK", 200
    @app.route("/signals")
    def signals():
        if not authed(): return "forbidden", 403
        return jsonify([dict(zip(("id","ts","chain","cex","symbol","direction","usd","net_pct","pnl_a"), r)) for r in DB.q("SELECT id,ts,chain,cex,symbol,direction,usd,net_pct,pnl_a FROM signals ORDER BY id DESC LIMIT 100")])
    try:
        from waitress import serve; serve(app, host="0.0.0.0", port=PORT)
    except ImportError: app.run(host="0.0.0.0", port=PORT)

def keepalive_loop():
    """Render goi Free tat service sau ~15 phut khong co request -> tu ping URL cong khai cua chinh minh."""
    url = env_str("RENDER_EXTERNAL_URL") or env_str("KEEPALIVE_URL")
    if not KEEPALIVE or not url: return
    log.info("Keepalive: ping %s/health moi 10 phut", url)
    while True:
        time.sleep(600)
        try: requests.get(url.rstrip("/") + "/health", timeout=20)
        except Exception as e: log.debug("keepalive loi %s", e)

def rss_mb():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"): return int(line.split()[1]) / 1024
    except Exception: pass
    return 0.0
def cleanup():
    now = time.time()
    with CD_LOCK:
        for k in [k for k, (t, _) in COOLDOWN.items() if now - t > 6 * 3600]: COOLDOWN.pop(k, None)
    with FS_LOCK:
        for k in [k for k, v in FIRST_SEEN.items() if now - v[1] > 3600]: FIRST_SEEN.pop(k, None)
    with BL_LOCK:
        for k in [k for k, u in BL.items() if u < now]: BL.pop(k, None)
    with IF_LOCK:
        for k in [k for k, t in LAST_EVAL.items() if now - t > 3600]: LAST_EVAL.pop(k, None)
    with SEC_LOCK:
        for k in [k for k, (t, _) in SEC_CACHE.items() if now - t > 6 * 3600]: SEC_CACHE.pop(k, None)
    gc.collect()

STARTUP_MARK = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", ".startup_sent")
def startup_report():
    MULTI.first_done.wait(timeout=900)
    for _ in range(120):                       # cho lan tai dau tien cua tat ca san xong (toi da 10 phut)
        if not MULTI.refresh_lock.locked(): break
        time.sleep(5)
    try:
        if time.time() - os.path.getmtime(STARTUP_MARK) < 6 * 3600: log.info("Bo qua tin khoi dong (da gui trong 6h)"); return
    except OSError: pass
    try: open(STARTUP_MARK, "w").write(str(time.time()))
    except OSError: pass
    warn = []
    for ch, cfg in CHAINS.items():
        if cfg["kind"] == "evm" and not cfg.get("rpc") and not cfg.get("blockscout"): warn.append(f"{ch}: missing RPC_{ch} → cannot read token decimals")
        if cfg["kind"] == "evm" and cfg.get("chain_id") not in GOPLUS_CHAINS | HONEYPOT_CHAINS: warn.append(f"{ch}: no tax data source → default tax {NO_SOURCE_TAX*100:.0f}%")
    for n, h in MULTI.health.items():
        if h.get("err") and ("403" in h["err"] or "451" in h["err"]): warn.append(f"{n} blocks the server IP → change server region (Singapore/Frankfurt/Tokyo)")
    L = [f"🚀 <b>BOT {VERSION} started</b>",
         f"Chains: {', '.join(CHAINS)}",
         f"Alert when net profit ≥ {MIN_NET_PCT}% & ≥ {fmt_usd(MIN_NET_USD)} · dynamic size {fmt_usd(ORDER_SIZE_MIN)}–{fmt_usd(ORDER_SIZE_MAX)}",
         f"Daily report: {DAILY_REPORT_HOUR}:00 (UTC+7)", "", "<b>Exchanges:</b>"] + cex_health_lines()
    L.append("\nTokens per chain: " + ", ".join(f"{k}={len(v)}" for k, v in MULTI.by_chain.items()))
    L.append(f"Scanning: 1 queue · {DEX_RPM:.0f} DexScreener req/min · hot {HOT_SEC:.0f}s / warm {WARM_SEC:.0f}s / cold {COLD_SEC/60:.0f}m")
    if ALLOW_UNVERIFIED_STATUS: warn.append("ALLOW_UNVERIFIED_STATUS=1 → may alert when deposit/withdraw is unverified. Recommend 0")
    if OBSOLETE_ENV: warn.append("Obsolete env vars, please remove: " + ", ".join(OBSOLETE_ENV))
    if not env_str("RENDER_EXTERNAL_URL") and not env_str("KEEPALIVE_URL"): warn.append("RENDER_EXTERNAL_URL not found → keepalive off, Free plan may sleep")
    if warn: L += ["", "<b>Notes:</b>"] + [f"⚠ {esc(w)}" for w in warn]
    L.append("\nType /help for commands, /check SYMBOL to inspect a token.")
    send_long("\n".join(L))

def main():
    log.info("BOT %s | chains=%s | cex=%s", VERSION, list(CHAINS), list(EXCHANGES))
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    threading.Thread(target=telegram_poll_loop, daemon=True, name="TG").start()
    threading.Thread(target=followup_loop, daemon=True, name="Follow").start()
    threading.Thread(target=scheduler_loop, daemon=True, name="Sched").start()
    threading.Thread(target=keepalive_loop, daemon=True, name="Keepalive").start()
    threading.Thread(target=refresher_loop, daemon=True, name="Refresh").start()
    threading.Thread(target=startup_report, daemon=True, name="Startup").start()
    MULTI.first_done.wait(timeout=240)
    w = Worker(); w.start()
    while True:
        time.sleep(120); cleanup(); log.info("RAM %.0f MB | batches %d", rss_mb(), SCAN_HEALTH["cycle"])
        if not w.thread.is_alive() or time.time() - SCAN_HEALTH["last_batch"] > STUCK_SEC:
            log.warning("[SUPERVISOR] khoi dong lai luong quet"); w.restart()

if __name__ == "__main__": main()
