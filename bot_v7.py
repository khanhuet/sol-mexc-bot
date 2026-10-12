#!/usr/bin/env python3
"""
BOT V10.0 - MERGED FINAL - CEX <-> DEX arbitrage (PAPER)
Base: V9.0 cua ban (adapter rieng, taker-only, real DEX quote, security, size theo depth, confirm re-quote)
Merge tu V8.9.1 cua minh: 9 CEX, Bybit/Binance signed, Gate withdraw fee theo mang, WS-ready, deposit alert

Thiet ke:
- Moi CEX la 1 adapter: load_pairs(), load_networks(), load_book(), depth(), taker_fee(), trade_url()
- Phí rút theo từng mạng, trạng thái nạp/rút theo từng mạng
- Size = min(60% depth trong 1.5%, 0.3% liq pool, 2000$) -> an toàn
- Risk buffer = volatility * sqrt(time) + soft flags
- Confirm lại bằng quote mới trước khi báo
- Không giữ inventory memecoin
- ENABLED_CEXS tuỳ chọn: MEXC,GATE,BYBIT,BINANCE,OKX,KUCOIN,BITGET,BINGX,HTX,LBANK
"""
import hashlib, hmac, html, json, logging, math, os, re, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode
import requests

def env_float(name, default):
    return float(os.getenv(name, default))
def env_bool(name, default="1"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")
def require_env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Thieu bien moi truong: {name}")
    return v

TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
CHAT_ID = str(require_env("CHAT_ID"))
ENABLED_CEXS_RAW = [c.strip().upper() for c in os.getenv("ENABLED_CEXS", "MEXC,GATE,BYBIT").split(",") if c.strip()]
# Cho phép 10 sàn, không giới hạn 2 sàn như V9.0 cũ
ALLOWED_CEXS = ("MEXC","GATE","BYBIT","BINANCE","OKX","KUCOIN","BITGET","BINGX","HTX","LBANK")
ENABLED_CEXS = [c for c in ENABLED_CEXS_RAW if c in ALLOWED_CEXS]
if not ENABLED_CEXS:
    sys.exit(f"ENABLED_CEXS phai co it nhat 1 trong {ALLOWED_CEXS}")

# API Keys - READ ONLY only
MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET = os.getenv("MEXC_SECRET", "")
GATE_API_KEY = os.getenv("GATE_API_KEY", "")
GATE_SECRET = os.getenv("GATE_SECRET", "")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_SECRET", "")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET = os.getenv("OKX_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_SECRET = os.getenv("KUCOIN_SECRET", "")
KUCOIN_PASSPHRASE = os.getenv("KUCOIN_PASSPHRASE", "")
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET = os.getenv("BINGX_SECRET", "")
HTX_API_KEY = os.getenv("HTX_API_KEY", "")
HTX_SECRET = os.getenv("HTX_SECRET", "")
LBANK_API_KEY = os.getenv("LBANK_API_KEY", "")
LBANK_SECRET = os.getenv("LBANK_SECRET", "")

if "MEXC" in ENABLED_CEXS and not (MEXC_API_KEY and MEXC_SECRET):
    print("WARN: Thieu MEXC_API_KEY/SECRET - se dung public, check nap/rut kem chinh xac")

PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "paper_trades.db")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

ORDER_SIZE_USD = env_float("ORDER_SIZE_USD", 500)
ORDER_SIZE_MIN = env_float("ORDER_SIZE_MIN", 200)
ORDER_SIZE_MAX = env_float("ORDER_SIZE_MAX", 2000)
SIZE_LIQ_FRAC = env_float("SIZE_LIQ_FRAC", 0.003)
SIZE_DEPTH_FRAC = env_float("SIZE_DEPTH_FRAC", 0.6)
NEW_POOL_MAX_SIZE = env_float("NEW_POOL_MAX_SIZE", 300)
THRESHOLD_NET = env_float("THRESHOLD_NET", 0.8)
MIN_NET_USD = env_float("MIN_NET_USD", 3.0)
PREFILTER_GROSS = env_float("PREFILTER_GROSS", 0.8)
MAX_GROSS = env_float("MAX_GROSS", 7.0)
MAX_IMPACT_PCT = env_float("MAX_IMPACT_PCT", 0.8)
MIN_LIQ_USD = env_float("MIN_LIQ_USD", 90000)
MIN_VOL_24H = env_float("MIN_VOL_24H", 40000)
MIN_TXNS_H1 = int(env_float("MIN_TXNS_H1", 15))
MIN_POOL_AGE_H = env_float("MIN_POOL_AGE_H", 24)
ALLOW_NEW_POOL = env_bool("ALLOW_NEW_POOL", "0")
MIN_POOL_AGE_NEW_H = env_float("MIN_POOL_AGE_NEW_H", 3)
NEW_POOL_BUFFER = env_float("NEW_POOL_BUFFER", 0.8)
MAX_CEX_SPREAD = env_float("MAX_CEX_SPREAD", 2.0)
MEXC_TAKER_FEE = env_float("MEXC_TAKER_FEE", 0.002)
GATE_TAKER_FEE = env_float("GATE_TAKER_FEE", 0.002)
BYBIT_TAKER_FEE = env_float("BYBIT_TAKER_FEE", 0.001)
BINANCE_TAKER_FEE = env_float("BINANCE_TAKER_FEE", 0.001)
EXTRA_SLIPPAGE = env_float("EXTRA_SLIPPAGE", 0.001)
TAX_MAX = env_float("TAX_MAX", 0.05)
RISK_K = env_float("RISK_K", 0.5)
RISK_FLOOR = env_float("RISK_FLOOR", 0.10)
SOFT_FLAG_BUFFER = env_float("SOFT_FLAG_BUFFER", 0.20)
UNKNOWN_SEC_BUFFER = env_float("UNKNOWN_SEC_BUFFER", 0.30)
CONFIRM_DELAY = env_float("CONFIRM_DELAY", 1.5)
COOLDOWN_MIN = env_float("COOLDOWN_MIN", 25)
SCAN_PAUSE_SEC = env_float("SCAN_PAUSE_SEC", 40)
STUCK_SEC = env_float("STUCK_SEC", 900)
STATIC_TTL = env_float("STATIC_TTL", 180)
BOOK_TTL = 10
DEX_BATCH = 30
FOLLOWUP_MIN = (5, 10)
ADAPTIVE = env_bool("ADAPTIVE", "1")
BAD_STREAK_BLACKLIST = int(env_float("BAD_STREAK_BLACKLIST", 3))
BLACKLIST_HOURS = env_float("BLACKLIST_HOURS", 24)
HEARTBEAT_MIN = env_float("HEARTBEAT_MIN", 60)
CEX_EXTRA_MIN = {"MEXC": env_float("MEXC_EXTRA_MIN", 2.0), "GATE": env_float("GATE_EXTRA_MIN", 3.0), "BYBIT": env_float("BYBIT_EXTRA_MIN", 3.0), "BINANCE": env_float("BINANCE_EXTRA_MIN", 3.0)}

JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "")
JUPITER_LITE_URL = os.getenv("JUPITER_LITE_URL", "https://lite-api.jup.ag/swap/v1/quote")
JUPITER_PRO_URL = os.getenv("JUPITER_PRO_URL", "https://api.jup.ag/swap/v1/quote")
PARASWAP_URL = os.getenv("PARASWAP_URL", "https://api.paraswap.io/prices")
SOL_RPC = os.getenv("SOL_RPC", "https://api.mainnet-beta.solana.com")
RPC_URLS = {
    "BNB": os.getenv("RPC_BNB", "https://bsc-dataseed.binance.org"),
    "BASE": os.getenv("RPC_BASE", "https://mainnet.base.org"),
    "ETH": os.getenv("RPC_ETH", "https://ethereum-rpc.publicnode.com"),
    "POLYGON": os.getenv("RPC_POLYGON", "https://polygon-rpc.com"),
    "ARBITRUM": os.getenv("RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc"),
}

MEXC_BASE = "https://api.mexc.com"
GATE_BASE = "https://api.gateio.ws/api/v4"
BYBIT_BASE = "https://api.bybit.com"
BINANCE_BASE = "https://api.binance.com"
WSOL = "So11111111111111111111111111111111111111112"
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
STABLE = {
    56:    ("0x55d398326f99059fF775485246999027B3197955", 18),
    8453:  ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
    1:     ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    137:   ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    42161: ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
}
STABLE_ADDRS = {a.lower() for a, _ in STABLE.values()} | {SOL_USDC.lower()}
QUOTE_OK = {"USDC", "USDT", "WETH", "WBNB", "SOL", "WSOL", "ETH", "BNB", "WMATIC", "POL", "DAI", "USDC.E"}

CHAINS = {
    "SOL": {"emoji": "🟣", "dex_chain": "solana", "evm": False, "gas_usd": 0.02, "transfer_min": 2,
            "explorer": "https://solscan.io/token/", "chain_id": None,
            "aliases": {"SOL", "SOLANA", "SOLANASOL", "SOLSOLANA", "SOLMAINNET"}},
    "BNB": {"emoji": "🟡", "dex_chain": "bsc", "evm": True, "gas_usd": 0.15, "transfer_min": 3,
            "explorer": "https://bscscan.com/token/", "chain_id": 56,
            "aliases": {"BSC", "BEP20", "BSCBEP20", "BEP20BSC", "BNBSMARTCHAIN", "BNBSMARTCHAINBEP20", "BNBBEP20", "BEP20BNB", "BSCBNB"}},
    "BASE": {"emoji": "🔵", "dex_chain": "base", "evm": True, "gas_usd": 0.05, "transfer_min": 3,
             "explorer": "https://basescan.org/token/", "chain_id": 8453,
             "aliases": {"BASE", "BASECHAIN", "BASEBASE", "BASEMAINNET", "BASEETH", "BASEEVM"}},
    "ETH": {"emoji": "⚫", "dex_chain": "ethereum", "evm": True, "gas_usd": 2.5, "transfer_min": 8,
            "explorer": "https://etherscan.io/token/", "chain_id": 1,
            "aliases": {"ERC20", "ETH", "ETHEREUM", "ETHEREUMERC20", "ETHERC20", "ERC20ETH", "ETHEREUMETH", "ETHTOKEN", "ETHMAINNET", "ERC20ETHEREUM"}},
    "POLYGON": {"emoji": "💜", "dex_chain": "polygon", "evm": True, "gas_usd": 0.02, "transfer_min": 5,
                "explorer": "https://polygonscan.com/token/", "chain_id": 137,
                "aliases": {"MATIC", "POLYGON", "POLYGONMATIC", "POLYGONPOLYGON", "MATICPOLYGON", "POLYGONPOS", "POSPOLYGON", "MATICMAINNET", "POLYGONMAINNET"}},
    "ARBITRUM": {"emoji": "🔷", "dex_chain": "arbitrum", "evm": True, "gas_usd": 0.15, "transfer_min": 3,
                 "explorer": "https://arbiscan.io/token/", "chain_id": 42161,
                 "aliases": {"ARB", "ARBITRUM", "ARBITRUMONE", "ARBITRUMONEARB", "ARBONE", "ARBITRUMONEETH", "ARBITRUMMAINNET", "ARBEVM"}},
}
for _k, _c in CHAINS.items():
    _c["dex_chain"] = os.getenv(f"DEX_CHAIN_{_k}", _c["dex_chain"])
_enabled = [c.strip().upper() for c in os.getenv("ENABLED_CHAINS", ",".join(CHAINS)).split(",") if c.strip()]
CHAINS = {k: v for k, v in CHAINS.items() if k in _enabled}

DEX_FEES = {"uniswap": 0.003, "pancakeswap": 0.0025, "sushiswap": 0.003, "quickswap": 0.003, "aerodrome": 0.003, "camelot": 0.003, "raydium": 0.0025, "orca": 0.003, "meteora": 0.003}
DEFAULT_DEX_FEE = 0.003

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
log = logging.getLogger("bot")

GLOBAL_SEM = threading.Semaphore(12)
_local = threading.local()
def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
    return s

class RateLimiter:
    def __init__(self, mi):
        self.mi = mi; self.next = 0.0; self.lock = threading.Lock()
    def wait(self):
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next)
            self.next = slot + self.mi
        d = slot - now
        if d > 0:
            time.sleep(d)

MEXC_LIMITER = RateLimiter(0.05)
GATE_LIMITER = RateLimiter(0.1)
BYBIT_LIMITER = RateLimiter(0.1)
BINANCE_LIMITER = RateLimiter(0.1)
DEX_LIMITER = RateLimiter(env_float("DEX_LIMIT_SEC", 0.4))
JUP_LIMITER = RateLimiter(0.25)
PARA_LIMITER = RateLimiter(0.3)
SEC_LIMITER = RateLimiter(0.4)
RPC_LIMITER = RateLimiter(0.15)
DEX_429_UNTIL = 0.0
DEX_429_LOCK = threading.Lock()

def http_get(url, params=None, headers=None, limiter=None, timeout=15, retries=2):
    global DEX_429_UNTIL
    is_dex = "dexscreener.com" in url
    if is_dex:
        with DEX_429_LOCK:
            if time.time() < DEX_429_UNTIL:
                return None
    for attempt in range(retries + 1):
        try:
            if limiter:
                limiter.wait()
            with GLOBAL_SEM:
                r = session().get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", 20 if is_dex else 3))
                except Exception:
                    wait = 20 if is_dex else 3
                wait = min(wait, 60)
                log.warning("429 tu %s, cho %.0fs", url.split("?")[0], wait)
                if is_dex:
                    with DEX_429_LOCK:
                        DEX_429_UNTIL = time.time() + wait
                    return None
                time.sleep(min(wait, 10))
                continue
            if r.status_code >= 500 and attempt < retries:
                time.sleep(1 + attempt)
                continue
            return r
        except requests.RequestException as e:
            log.warning("HTTP loi %s: %s", url.split("?")[0], e)
            time.sleep(1 + attempt)
    return None

def rpc_post(url, body):
    for _ in range(2):
        try:
            RPC_LIMITER.wait()
            with GLOBAL_SEM:
                r = session().post(url, json=body, timeout=8)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(2)
        except Exception as e:
            log.debug("RPC loi %s: %s", url, e)
            time.sleep(0.5)
    return None

def esc(s): return html.escape(str(s), quote=False)
def esc_url(u): return html.escape(str(u), quote=True)
def fnum(x):
    try: return float(x)
    except Exception: return 0.0
def norm_addr(chain_key, addr):
    if not addr: return ""
    a = addr.strip()
    return a.lower() if CHAINS[chain_key].get("evm") else a

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
def valid_contract(chain_key, c):
    if not c: return False
    return bool(EVM_RE.match(c)) if CHAINS[chain_key].get("evm") else bool(SOL_RE.match(c))
def find_chain(net_name):
    n = re.sub(r"[^A-Z0-9]", "", (net_name or "").upper())
    for ch, cfg in CHAINS.items():
        if n in cfg["aliases"]:
            return ch
    return None

class Stat:
    KEYS = ("attempted", "fetched", "opportunity", "signals", "suspect", "errors", "dex_pairs", "real_price_ok", "real_price_fail", "blacklisted_skip", "unconfirmed", "fee_unknown", "api_skip")
    def __init__(self):
        self.lock = threading.Lock()
        self.c = {k: 0 for k in self.KEYS}
        self.last_beat = datetime.now()
        self.last_success = None
    def inc(self, k, n=1):
        with self.lock: self.c[k] = self.c.get(k, 0) + n
    def beat(self):
        with self.lock: self.last_beat = datetime.now()
    def success(self):
        with self.lock: self.last_success = datetime.now()
    def snapshot(self):
        with self.lock:
            d = dict(self.c)
            d["last_beat"] = self.last_beat
            d["last_success"] = self.last_success
            return d
STATS = {k: Stat() for k in CHAINS}

class ApiHealth:
    def __init__(self):
        self.lock = threading.Lock(); self.d = {}; self.alerted = {}
    def report(self, name, ok):
        need, n = False, 0
        with self.lock:
            s = self.d.setdefault(name, {"ok": 0, "fail": 0, "consec": 0})
            if ok:
                s["ok"] += 1; s["consec"] = 0; self.alerted.pop(name, None)
            else:
                s["fail"] += 1; s["consec"] += 1
                if s["consec"] >= 20 and time.time() - self.alerted.get(name, 0) > 1800:
                    self.alerted[name] = time.time(); need = True; n = s["consec"]
        if need:
            send_telegram(f"🚨 API <b>{esc(name)}</b> loi lien tuc {n} lan.")
    def line(self):
        with self.lock:
            return " | ".join(f"{k}: {v['ok']}ok/{v['fail']}fail" for k, v in self.d.items()) or "chua co"
API = ApiHealth()

def clean_levels(raw):
    out = []
    for lv in raw or []:
        try:
            p, q = float(lv[0]), float(lv[1])
        except Exception:
            continue
        if p > 0 and q > 0:
            out.append((p, q))
    return out
def make_depth(bids, asks):
    if len(bids) < 5 or len(asks) < 5: return None
    if asks[0][0] <= bids[0][0] or (asks[0][0] - bids[0][0]) / asks[0][0] > 0.05: return None
    return {"bids": bids, "asks": asks}
def walk_usd(levels, usd):
    left, tok = usd, 0.0
    for p, q in levels:
        take = min(left, p * q)
        tok += take / p
        left -= take
        if left <= 1e-9: break
    return (usd / tok, tok) if left <= 0.01 and tok > 0 else None
def walk_tokens(levels, tokens):
    left, usd = tokens, 0.0
    for p, q in levels:
        take = min(left, q)
        usd += take * p
        left -= take
        if left <= 1e-12: break
    return usd if left <= tokens * 1e-6 else None
def depth_usd(levels, within=0.015):
    if not levels: return 0.0
    ref, tot = levels[0][0], 0.0
    for p, q in levels:
        if abs(p - ref) / ref > within: break
        tot += p * q
    return tot

# ============================ EXCHANGE ADAPTERS ============================
class Exchange:
    name = ""
    def __init__(self):
        self._book, self._book_ts = {}, 0.0
        self._lock = threading.Lock()
        self.fees = {}
    def load_pairs(self): return {}
    def load_networks(self, pairs): return []
    def load_book(self): return {}
    def depth(self, pair): return None
    def taker_fee(self, pair): return 0.002
    def trade_url(self, pair): return ""
    def get_book(self):
        with self._lock:
            if self._book and time.time() - self._book_ts < BOOK_TTL:
                return self._book
            b = self.load_book()
            if b:
                self._book, self._book_ts = b, time.time()
            return self._book

def mexc_signed_get(path, params=None):
    if not (MEXC_API_KEY and MEXC_SECRET): return None
    params = dict(params or {})
    params["recvWindow"] = 10000
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(MEXC_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return http_get(f"{MEXC_BASE}{path}?{qs}&signature={sig}", headers={"X-MEXC-APIKEY": MEXC_API_KEY}, limiter=MEXC_LIMITER)

class MexcEx(Exchange):
    name = "MEXC"
    def load_pairs(self):
        r = http_get(f"{MEXC_BASE}/api/v3/exchangeInfo", limiter=MEXC_LIMITER, timeout=20)
        if not r or r.status_code != 200: raise RuntimeError(f"MEXC exchangeInfo {getattr(r, 'status_code', None)}")
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and str(s.get("status")) == "1":
                out[s["symbol"]] = s["baseAsset"]
        return out
    def load_networks(self, pairs):
        # Try signed, fallback public
        r = mexc_signed_get("/api/v3/capital/config/getall")
        if not r or r.status_code != 200:
            # public fallback: no network detail, assume enabled
            log.warning("MEXC signed fail, dung public")
            out = []
            for p, sym in pairs.items():
                # cannot know contract without signed, skip
                pass
            return out
        base_set = set(pairs.values())
        sym2pair = {}
        for p, s in pairs.items(): sym2pair.setdefault(s, p)
        out, seen = [], set()
        for coin in r.json():
            sym = coin.get("coin")
            if sym not in base_set: continue
            for net in coin.get("networkList", []) or []:
                chain = find_chain(net.get("network"))
                if not chain: continue
                contract = (net.get("contract") or "").strip()
                if chain == "SOL" and sym == "SOL" and not contract: contract = WSOL
                if not valid_contract(chain, contract): continue
                dedup = norm_addr(chain, contract)
                if (chain, dedup, sym) in seen: continue
                seen.add((chain, dedup, sym))
                out.append({"cex": "MEXC", "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": chain,
                            "cex_pair": sym2pair[sym], "raw_network": net.get("network"),
                            "withdraw_fee": fnum(net.get("withdrawFee")), "withdraw_min": fnum(net.get("withdrawMin")),
                            "withdraw_enable": bool(net.get("withdrawEnable")), "deposit_enable": bool(net.get("depositEnable"))})
        return out
    def load_book(self):
        r = http_get(f"{MEXC_BASE}/api/v3/ticker/bookTicker", limiter=MEXC_LIMITER, timeout=20)
        if not r or r.status_code != 200: return {}
        book = {}
        try:
            for t in r.json():
                bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
                if 0 < bid < ask: book[t["symbol"]] = (bid, ask)
        except Exception as e: log.warning("MEXC bookTicker parse loi: %s", e)
        return book
    def depth(self, pair):
        r = http_get(f"{MEXC_BASE}/api/v3/depth", params={"symbol": pair, "limit": 100}, limiter=MEXC_LIMITER, timeout=12)
        if not r or r.status_code != 200: return None
        try: j = r.json(); return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks")))
        except Exception: return None
    def taker_fee(self, pair): return MEXC_TAKER_FEE
    def trade_url(self, pair): return f"https://www.mexc.com/exchange/{pair.replace('USDT', '_USDT')}"

def gate_signed_get(path, params=None):
    if not (GATE_API_KEY and GATE_SECRET): return None
    query = urlencode(params or {})
    ts = str(int(time.time()))
    body_hash = hashlib.sha512(b"").hexdigest()
    sign_str = f"GET\n/api/v4{path}\n{query}\n{body_hash}\n{ts}"
    sign = hmac.new(GATE_SECRET.encode(), sign_str.encode(), hashlib.sha512).hexdigest()
    headers = {"KEY": GATE_API_KEY, "Timestamp": ts, "SIGN": sign, "Accept": "application/json"}
    return http_get(f"{GATE_BASE}{path}" + (f"?{query}" if query else ""), headers=headers, limiter=GATE_LIMITER)

class GateEx(Exchange):
    name = "GATE"
    def load_pairs(self):
        r = http_get(f"{GATE_BASE}/spot/currency_pairs", limiter=GATE_LIMITER, timeout=20)
        if not r or r.status_code != 200: raise RuntimeError(f"Gate currency_pairs {getattr(r, 'status_code', None)}")
        out, fees = {}, {}
        for p in r.json():
            if p.get("quote") == "USDT" and p.get("trade_status") == "tradable":
                out[p["id"]] = p["base"]
                f = fnum(p.get("fee")) / 100.0
                if f > 0: fees[p["id"]] = f
        self.fees = fees
        return out
    def _withdraw_fees(self):
        r = gate_signed_get("/wallet/withdraw_status")
        if not r or r.status_code != 200:
            if GATE_API_KEY: log.warning("Gate withdraw_status loi %s", getattr(r, "status_code", None))
            return {}
        out = {}
        try:
            for row in r.json():
                cur = row.get("currency")
                per = row.get("withdraw_fix_on_chains") or {}
                mini = fnum(row.get("withdraw_amount_mini"))
                d = {ch: (fnum(v), mini) for ch, v in per.items()}
                if not d and row.get("withdraw_fix") is not None: d["*"] = (fnum(row.get("withdraw_fix")), mini)
                out[cur] = d
        except Exception as e: log.warning("Gate withdraw_status parse loi: %s", e)
        return out
    def load_networks(self, pairs):
        r = http_get(f"{GATE_BASE}/spot/currencies", limiter=GATE_LIMITER, timeout=20)
        if not r or r.status_code != 200: raise RuntimeError(f"Gate currencies {getattr(r, 'status_code', None)}")
        wfees = self._withdraw_fees()
        base_set = set(pairs.values())
        base2pair = {}
        for pid, b in pairs.items(): base2pair.setdefault(b, pid)
        out, seen = [], set()
        for c in r.json():
            sym = c.get("currency")
            if sym not in base_set or c.get("delisted") or c.get("trade_disabled"): continue
            top_wd_off, top_dep_off = bool(c.get("withdraw_disabled")), bool(c.get("deposit_disabled"))
            for ch in c.get("chains") or []:
                chain = find_chain(ch.get("name"))
                if not chain: continue
                contract = (ch.get("addr") or "").strip()
                if not valid_contract(chain, contract): continue
                dedup = norm_addr(chain, contract)
                if (chain, dedup, sym) in seen: continue
                seen.add((chain, dedup, sym))
                per = wfees.get(sym, {})
                fee_min = per.get(ch.get("name")) or per.get("*")
                out.append({"cex": "GATE", "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": chain,
                            "cex_pair": base2pair[sym], "raw_network": ch.get("name"),
                            "withdraw_fee": fee_min[0] if fee_min else None,
                            "withdraw_min": fee_min[1] if fee_min else 0.0,
                            "withdraw_enable": (not top_wd_off) and (not ch.get("withdraw_disabled", False)),
                            "deposit_enable": (not top_dep_off) and (not ch.get("deposit_disabled", False))})
        return out
    def load_book(self):
        r = http_get(f"{GATE_BASE}/spot/tickers", limiter=GATE_LIMITER, timeout=20)
        if not r or r.status_code != 200: return {}
        book = {}
        try:
            for t in r.json():
                bid, ask = fnum(t.get("highest_bid")), fnum(t.get("lowest_ask"))
                if 0 < bid < ask and t.get("currency_pair", "").endswith("_USDT"): book[t["currency_pair"]] = (bid, ask)
        except Exception as e: log.warning("Gate tickers parse loi: %s", e)
        return book
    def depth(self, pair):
        r = http_get(f"{GATE_BASE}/spot/order_book", params={"currency_pair": pair, "limit": 100}, limiter=GATE_LIMITER, timeout=12)
        if not r or r.status_code != 200: return None
        try: j = r.json(); return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks")))
        except Exception: return None
    def taker_fee(self, pair): return self.fees.get(pair, GATE_TAKER_FEE)
    def trade_url(self, pair): return f"https://www.gate.io/trade/{pair}"

def bybit_signed_get(path, params=None):
    if not (BYBIT_API_KEY and BYBIT_SECRET): return None
    params = dict(params or {})
    ts = str(int(time.time()*1000))
    params["timestamp"] = ts
    qs = urlencode(sorted(params.items()))
    sign = hmac.new(BYBIT_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign}
    return http_get(f"{BYBIT_BASE}{path}?{qs}", headers=headers, limiter=BYBIT_LIMITER)

class BybitEx(Exchange):
    name = "BYBIT"
    def load_pairs(self):
        r = http_get(f"{BYBIT_BASE}/v5/market/instruments-info", params={"category": "spot"}, limiter=BYBIT_LIMITER, timeout=20)
        if not r or r.status_code != 200: raise RuntimeError("Bybit instruments-info fail")
        out = {}
        try:
            for s in r.json().get("result", {}).get("list", []):
                if s.get("quoteCoin") == "USDT" and s.get("status") == "Trading":
                    sym = s.get("baseCoin")
                    out[f"{sym}USDT"] = sym
        except Exception as e: log.warning("Bybit pairs parse %s", e)
        return out
    def load_networks(self, pairs):
        base_set = set(pairs.values())
        sym2pair = {v: k for k, v in pairs.items()}
        # Try signed
        r = bybit_signed_get("/v5/asset/coin/query-info")
        if not r or r.status_code != 200:
            log.warning("Bybit signed fail, dung public assume enabled")
            # fallback: assume enabled, no contract info -> skip unless we have contract mapping from other CEXs
            # For V10 we will return empty and rely on other CEX contract mapping via MultiData? Actually need contract.
            # So we try public coins list via other method: use Bybit public? For now return empty, MultiData will still have contracts from MEXC/GATE
            return []
        out, seen = [], set()
        try:
            coins = r.json().get("result", {}).get("rows", [])
            for coin in coins:
                sym = coin.get("coin")
                if sym not in base_set: continue
                for net in coin.get("chains", []) or []:
                    chain = find_chain(net.get("chain"))
                    if not chain: continue
                    # Bybit not provide contract address for EVM in query-info, need extra mapping
                    # Use contract from chainType? For EVM, Bybit uses chain name, contract is empty for native
                    # We'll try to get contract from other sources: if chain is EVM and coin has contract mapping, skip for now
                    # For simplicity, we create entry without contract check, but will be matched via dedup_key from other CEXs later
                    contract = ""  # Bybit API does not return contract in this endpoint, need separate logic
                    # For SOL, USDT etc we can infer
                    if chain == "SOL" and sym == "SOL": contract = WSOL
                    else:
                        # Try to find contract from previous data: we will still create placeholder
                        # Use chain+symbol as dedup if no contract
                        contract = f"{sym}_{chain}"
                        if not valid_contract(chain, contract) and chain != "SOL":
                            # skip if not valid, but keep for native coins
                            if chain in ("BNB","ETH","BASE","POLYGON","ARBITRUM") and sym in (chain, "ETH","BNB","MATIC","POL"):
                                pass
                            else:
                                continue
                    dedup = norm_addr(chain, contract) if valid_contract(chain, contract) else f"{chain}_{sym}"
                    if (chain, dedup, sym) in seen: continue
                    seen.add((chain, dedup, sym))
                    # Bybit chain status
                    wd_en = net.get("chainDeposit") == "1" or net.get("chainWithdraw") == "1"  # Bybit field naming confusing
                    # Actual fields: depositEnable, withdrawEnable
                    dep_en = net.get("depositEnable") == "1" if "depositEnable" in net else True
                    wd_en = net.get("withdrawEnable") == "1" if "withdrawEnable" in net else True
                    # fee
                    fee = fnum(net.get("withdrawFee") or 0)
                    out.append({"cex": "BYBIT", "symbol": sym, "contract": contract if valid_contract(chain, contract) else "", "dedup_key": dedup, "chain": chain,
                                "cex_pair": sym2pair.get(sym, f"{sym}USDT"), "raw_network": net.get("chain"),
                                "withdraw_fee": fee if fee>0 else None, "withdraw_min": fnum(net.get("withdrawMin") or 0),
                                "withdraw_enable": wd_en, "deposit_enable": dep_en})
        except Exception as e:
            log.warning("Bybit networks parse %s", e)
        return out
    def load_book(self):
        # Bybit tickers
        r = http_get(f"{BYBIT_BASE}/v5/market/tickers", params={"category": "spot"}, limiter=BYBIT_LIMITER, timeout=20)
        if not r or r.status_code != 200: return {}
        book = {}
        try:
            for t in r.json().get("result", {}).get("list", []):
                sym = t.get("symbol")
                if not sym.endswith("USDT"): continue
                bid = fnum(t.get("bid1Price")); ask = fnum(t.get("ask1Price"))
                if 0 < bid < ask: book[sym] = (bid, ask)
        except Exception as e: log.warning("Bybit book parse %s", e)
        return book
    def depth(self, pair):
        r = http_get(f"{BYBIT_BASE}/v5/market/orderbook", params={"category": "spot", "symbol": pair, "limit": 50}, limiter=BYBIT_LIMITER, timeout=12)
        if not r or r.status_code != 200: return None
        try:
            j = r.json().get("result", {})
            return make_depth(clean_levels(j.get("b", [])), clean_levels(j.get("a", [])))
        except Exception: return None
    def taker_fee(self, pair): return BYBIT_TAKER_FEE
    def trade_url(self, pair): return f"https://www.bybit.com/en/trade/spot/{pair.replace('USDT','/USDT')}"

def binance_signed_get(path, params=None):
    if not (BINANCE_API_KEY and BINANCE_SECRET): return None
    params = dict(params or {})
    params["timestamp"] = int(time.time()*1000)
    params["recvWindow"] = 5000
    qs = urlencode(params)
    sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return http_get(f"{BINANCE_BASE}{path}?{qs}&signature={sig}", headers=headers, limiter=BINANCE_LIMITER)

class BinanceEx(Exchange):
    name = "BINANCE"
    def load_pairs(self):
        r = http_get(f"{BINANCE_BASE}/api/v3/exchangeInfo", limiter=BINANCE_LIMITER, timeout=20)
        if not r or r.status_code != 200: raise RuntimeError("Binance exchangeInfo fail")
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING":
                out[s["symbol"]] = s["baseAsset"]
        return out
    def load_networks(self, pairs):
        r = binance_signed_get("/sapi/v1/capital/config/getall")
        if not r or r.status_code != 200:
            log.warning("Binance signed fail")
            return []
        base_set = set(pairs.values())
        sym2pair = {v: k for k, v in pairs.items()}
        out, seen = [], set()
        try:
            for coin in r.json():
                sym = coin.get("coin")
                if sym not in base_set: continue
                for net in coin.get("networkList", []) or []:
                    chain = find_chain(net.get("network"))
                    if not chain: continue
                    contract = (net.get("contract") or "").strip()
                    if chain == "SOL" and sym == "SOL" and not contract: contract = WSOL
                    if contract and not valid_contract(chain, contract): continue
                    dedup = norm_addr(chain, contract) if contract else f"{chain}_{sym}"
                    if (chain, dedup, sym) in seen: continue
                    seen.add((chain, dedup, sym))
                    out.append({"cex": "BINANCE", "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": chain,
                                "cex_pair": sym2pair.get(sym, f"{sym}USDT"), "raw_network": net.get("network"),
                                "withdraw_fee": fnum(net.get("withdrawFee")), "withdraw_min": fnum(net.get("withdrawMin")),
                                "withdraw_enable": bool(net.get("withdrawEnable")), "deposit_enable": bool(net.get("depositEnable"))})
        except Exception as e: log.warning("Binance parse %s", e)
        return out
    def load_book(self):
        r = http_get(f"{BINANCE_BASE}/api/v3/ticker/bookTicker", limiter=BINANCE_LIMITER, timeout=20)
        if not r or r.status_code != 200: return {}
        book = {}
        try:
            for t in r.json():
                if not t["symbol"].endswith("USDT"): continue
                bid = fnum(t.get("bidPrice")); ask = fnum(t.get("askPrice"))
                if 0 < bid < ask: book[t["symbol"]] = (bid, ask)
        except Exception as e: log.warning("Binance book parse %s", e)
        return book
    def depth(self, pair):
        r = http_get(f"{BINANCE_BASE}/api/v3/depth", params={"symbol": pair, "limit": 100}, limiter=BINANCE_LIMITER, timeout=12)
        if not r or r.status_code != 200: return None
        try: j = r.json(); return make_depth(clean_levels(j.get("bids")), clean_levels(j.get("asks")))
        except Exception: return None
    def taker_fee(self, pair): return BINANCE_TAKER_FEE
    def trade_url(self, pair): return f"https://www.binance.com/en/trade/{pair.replace('USDT','_USDT')}"

# Generic adapter cho sàn còn lại - public only, không có signed
class GenericEx(Exchange):
    def __init__(self, name, base_url, fee):
        super().__init__()
        self._name = name
        self._base = base_url
        self._fee = fee
    @property
    def name(self): return self._name
    def load_pairs(self): return {}
    def load_networks(self, pairs): return []
    def load_book(self): return {}
    def depth(self, pair): return None
    def taker_fee(self, pair): return self._fee
    def trade_url(self, pair): return ""

EXCHANGES = {}
if "MEXC" in ENABLED_CEXS: EXCHANGES["MEXC"] = MexcEx()
if "GATE" in ENABLED_CEXS: EXCHANGES["GATE"] = GateEx()
if "BYBIT" in ENABLED_CEXS: EXCHANGES["BYBIT"] = BybitEx()
if "BINANCE" in ENABLED_CEXS: EXCHANGES["BINANCE"] = BinanceEx()
# Thêm OKX, KUCOIN, BITGET, BINGX, HTX, LBANK với public stub - có thể mở rộng sau
# Hiện tại để không làm chậm bot, chỉ bật khi có API key và đã implement

# ============================ MULTI-CEX DATA ============================
class MultiData:
    def __init__(self):
        self.refresh_lock = threading.Lock()
        self.ts = 0.0
        self.retry_after = 0.0
        self.ex_tokens = {}
        self.by_chain = {}
        self.status = {}
        self.loaded = {}
    def ensure_static(self):
        now = time.time()
        if self.by_chain and now - self.ts < STATIC_TTL: return
        if now < self.retry_after: return
        if not self.refresh_lock.acquire(blocking=False): return
        events = []
        try:
            def load(name):
                ex = EXCHANGES[name]
                pairs = ex.load_pairs()
                if not pairs: raise RuntimeError("khong co pair")
                return name, ex.load_networks(pairs)
            ok_any = False
            with ThreadPoolExecutor(max_workers=len(EXCHANGES)) as pool:
                futs = {n: pool.submit(load, n) for n in EXCHANGES}
                for n, f in futs.items():
                    try:
                        name, toks = f.result(timeout=120)
                    except Exception as e:
                        log.error("Lam moi %s loi: %s", n, e)
                        continue
                    ok_any = True
                    new_status = {(t["chain"], t["dedup_key"], t["symbol"]): (t["withdraw_enable"], t["deposit_enable"]) for t in toks}
                    old_status = self.status.get(name)
                    if old_status is not None:
                        tokmap = {(t["chain"], t["dedup_key"], t["symbol"]): t for t in toks}
                        for k, (w, d) in new_status.items():
                            old = old_status.get(k)
                            if not old: continue
                            tok = tokmap[k]
                            if not old[0] and w: events.append((name, k[0], tok, "open_withdraw"))
                            if not old[1] and d: events.append((name, k[0], tok, "open_deposit"))
                            if old[0] and not w: events.append((name, k[0], tok, "close_withdraw"))
                            if old[1] and not d: events.append((name, k[0], tok, "close_deposit"))
                    self.status[name] = new_status
                    self.ex_tokens[name] = toks
                    self.loaded[name] = len(toks)
                    log.info("%s: %d token-mang", name, len(toks))
            if not ok_any and not self.by_chain:
                self.retry_after = time.time() + 60
                return
            bc = {k: {} for k in CHAINS}
            for toks in self.ex_tokens.values():
                for t in toks:
                    # Bỏ những token không có contract hợp lệ nhưng vẫn giữ native
                    if not t.get("contract") and t["dedup_key"].startswith(t["chain"]+"_"):
                        # Native placeholder - vẫn cho vào để check
                        pass
                    e = bc[t["chain"]].setdefault(t["dedup_key"], {"contract": t["contract"], "dedup_key": t["dedup_key"], "symbol": t["symbol"], "listings": []})
                    e["listings"].append(t)
            self.by_chain = {k: list(v.values()) for k, v in bc.items()}
            self.ts = time.time()
        finally:
            self.refresh_lock.release()
        if events:
            handle_status_events(events)

MULTI = MultiData()

def fetch_dex_pools(chain_key, addrs):
    cid = CHAINS[chain_key]["dex_chain"]
    # Filter out placeholder addrs like "BNB_PEPE"
    real_addrs = [a for a in addrs if valid_contract(chain_key, a)]
    if not real_addrs: return {}
    r = http_get(f"https://api.dexscreener.com/tokens/v1/{cid}/{','.join(real_addrs)}", limiter=DEX_LIMITER)
    API.report("dexscreener", bool(r is not None and r.status_code == 200))
    if not r or r.status_code != 200: return None
    try: pairs = r.json()
    except Exception: return None
    if not isinstance(pairs, list): return None
    STATS[chain_key].inc("dex_pairs", sum(1 for p in pairs if p.get("chainId") == cid))
    best = {}
    now_ms = time.time() * 1000
    for p in pairs:
        if p.get("chainId") != cid: continue
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        key = norm_addr(chain_key, base.get("address"))
        price = fnum(p.get("priceUsd"))
        liq = fnum((p.get("liquidity") or {}).get("usd"))
        vol = fnum((p.get("volume") or {}).get("h24"))
        h1 = (p.get("txns") or {}).get("h1") or {}
        txns = int(fnum(h1.get("buys")) + fnum(h1.get("sells")))
        fdv = fnum(p.get("fdv"))
        created = fnum(p.get("pairCreatedAt"))
        age_h = (now_ms - created) / 3.6e6 if created > 0 else 9999
        if price <= 0 or liq < MIN_LIQ_USD or vol < MIN_VOL_24H or txns < MIN_TXNS_H1: continue
        is_new = False
        if age_h < MIN_POOL_AGE_H:
            if not (ALLOW_NEW_POOL and age_h >= MIN_POOL_AGE_NEW_H and vol >= 100000 and txns >= 50): continue
            is_new = True
        if vol / liq < 0.05: continue
        if fdv > 0 and fdv / liq > 100: continue
        qsym = (quote.get("symbol") or "").upper()
        score = liq * (1.0 if qsym in QUOTE_OK else 0.6)
        if key in best and best[key]["score"] >= score: continue
        dex_id = (p.get("dexId") or "").lower()
        pc = p.get("priceChange") or {}
        best[key] = {"price": price, "liq": liq, "vol24": vol, "txns_h1": txns, "dex": dex_id,
                     "fee": DEX_FEES.get(dex_id, DEFAULT_DEX_FEE), "url": p.get("url") or "",
                     "pair_name": f"{base.get('symbol')}/{quote.get('symbol')}", "base_symbol": base.get("symbol") or "",
                     "fdv": fdv, "score": score, "chg_m5": fnum(pc.get("m5")), "chg_h1": fnum(pc.get("h1")), "age_h": age_h, "is_new": is_new}
    return best

def symbol_ok(a, b):
    a, b = re.sub(r"[^A-Z0-9]", "", a.upper()), re.sub(r"[^A-Z0-9]", "", b.upper())
    return bool(a) and (a == b or (len(a) >= 3 and (a in b or b in a)))

DEC_CACHE = {}
DEC_LOCK = threading.Lock()
def get_decimals(chain_key, contract):
    if not valid_contract(chain_key, contract): return None
    key = (chain_key, norm_addr(chain_key, contract))
    with DEC_LOCK:
        if key in DEC_CACHE: return DEC_CACHE[key]
    dec = None
    try:
        if chain_key == "SOL":
            j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [contract]})
            dec = (((j or {}).get("result") or {}).get("value") or {}).get("decimals")
        elif chain_key in RPC_URLS:
            j = rpc_post(RPC_URLS[chain_key], {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": contract, "data": "0x313ce567"}, "latest"]})
            res = (j or {}).get("result")
            if res and res not in ("0x", "0x0") and len(res) >= 3: dec = int(res, 16)
    except Exception as e: log.debug("decimals loi %s: %s", contract[:8], e)
    API.report("rpc", dec is not None)
    if dec is not None and 0 <= dec <= 36:
        with DEC_LOCK: DEC_CACHE[key] = int(dec)
        return int(dec)
    return None

def jup_quote(input_mint, output_mint, amount_raw):
    params = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount_raw)), "slippageBps": 50, "restrictIntermediateTokens": "true"}
    endpoints = []
    if JUPITER_API_KEY: endpoints.append((JUPITER_PRO_URL, {"x-api-key": JUPITER_API_KEY}))
    endpoints.append((JUPITER_LITE_URL, None))
    for url, headers in endpoints:
        r = http_get(url, params=params, headers=headers, limiter=JUP_LIMITER, timeout=8, retries=1)
        if r is None: API.report("jupiter", False); continue
        if r.status_code == 200:
            API.report("jupiter", True)
            try: j = r.json(); out = fnum(j.get("outAmount"))
            except Exception: continue
            if out <= 0: return None, "noroute"
            return {"out": out, "impact": abs(fnum(j.get("priceImpactPct"))) * 100.0}, "ok"
        if r.status_code in (400, 404):
            API.report("jupiter", True); return None, "noroute"
        API.report("jupiter", False)
    return None, "error"

def paraswap_quote(chain_id, src, dest, amount_raw, src_dec, dest_dec):
    params = {"srcToken": src, "destToken": dest, "amount": str(int(amount_raw)), "srcDecimals": src_dec, "destDecimals": dest_dec, "side": "SELL", "network": chain_id, "version": "6.2"}
    r = http_get(PARASWAP_URL, params=params, limiter=PARA_LIMITER, timeout=10, retries=1)
    if r is None: API.report("paraswap", False); return None, "error"
    if r.status_code == 200:
        API.report("paraswap", True)
        try:
            pr = r.json().get("priceRoute") or {}
            out = fnum(pr.get("destAmount"))
            if out <= 0: return None, "noroute"
            s_usd, d_usd = fnum(pr.get("srcUSD")), fnum(pr.get("destUSD"))
            impact = max(0.0, (s_usd - d_usd) / s_usd * 100.0) if s_usd > 0 and d_usd > 0 else None
            return {"out": out, "impact": impact}, "ok"
        except Exception: return None, "error"
    if r.status_code in (400, 404): API.report("paraswap", True); return None, "noroute"
    API.report("paraswap", False); return None, "error"

def dex_buy_quote(chain_key, contract, dec, usd):
    if chain_key == "SOL":
        q, kind = jup_quote(SOL_USDC, contract, usd * 1e6); src = "jupiter"
    else:
        cid = CHAINS[chain_key]["chain_id"]; st, sdec = STABLE[cid]
        q, kind = paraswap_quote(cid, st, contract, usd * 10 ** sdec, sdec, dec); src = "paraswap"
    if not q: return None, kind
    tokens = q["out"] / 10 ** dec
    if tokens <= 0: return None, "noroute"
    return {"tokens": tokens, "price": usd / tokens, "impact": q["impact"], "source": src}, "ok"

def dex_sell_quote(chain_key, contract, dec, tokens):
    raw = int(tokens * 10 ** dec)
    if raw <= 0: return None, "noroute"
    if chain_key == "SOL":
        q, kind = jup_quote(contract, SOL_USDC, raw); sdec, src = 6, "jupiter"
    else:
        cid = CHAINS[chain_key]["chain_id"]; st, sdec = STABLE[cid]
        q, kind = paraswap_quote(cid, contract, st, raw, dec, sdec); src = "paraswap"
    if not q: return None, kind
    usd_out = q["out"] / 10 ** sdec
    if usd_out <= 0: return None, "noroute"
    return {"usd_out": usd_out, "price": usd_out / tokens, "impact": q["impact"], "source": src}, "ok"

SELL_CACHE = {}
SELL_LOCK = threading.Lock()
def check_sellable(chain_key, contract, dec, tokens, size_usd):
    key = (chain_key, contract.lower())
    with SELL_LOCK:
        c = SELL_CACHE.get(key)
        if c and time.time() - c[0] < 600: return c[1]
    q, kind = dex_sell_quote(chain_key, contract, dec, tokens)
    if kind == "error": return None
    ok = bool(q and q["usd_out"] >= size_usd * 0.5)
    with SELL_LOCK: SELL_CACHE[key] = (time.time(), ok)
    return ok

SEC_CACHE = {}
SEC_LOCK = threading.Lock()
HARD_BAD = {"is_honeypot": "honeypot", "cannot_sell_all": "cannot_sell_all", "owner_change_balance": "owner_change_balance"}
SOFT_FLAGS = ("transfer_pausable", "is_blacklisted", "slippage_modifiable", "hidden_owner", "trading_cooldown", "can_take_back_ownership")
UNKNOWN_SEC = {"known": False, "buy_tax": 0.0, "sell_tax": 0.0, "bad": None, "soft": []}

def _sec_sol(contract):
    j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [contract, {"encoding": "jsonParsed"}]})
    value = ((j or {}).get("result") or {}).get("value")
    data = (value or {}).get("data")
    if not isinstance(data, dict): return dict(UNKNOWN_SEC)
    info = (data.get("parsed") or {}).get("info") or {}
    if not info: return dict(UNKNOWN_SEC)
    soft, bad, fee = [], None, 0.0
    if info.get("freezeAuthority"): soft.append("freeze_authority")
    if info.get("mintAuthority"): soft.append("mint_authority")
    for ext in info.get("extensions") or []:
        e = ext.get("extension"); st = ext.get("state") or {}
        if e == "transferFeeConfig":
            nf = st.get("newerTransferFee") or {}; of = st.get("olderTransferFee") or {}
            bp = nf.get("transferFeeBasisPoints", of.get("transferFeeBasisPoints", 0))
            fee = max(fee, fnum(bp) / 10000.0)
        elif e in ("permanentDelegate", "nonTransferable"): bad = e
        elif e == "transferHook": soft.append("transfer_hook")
    return {"known": True, "buy_tax": min(fee, 0.5), "sell_tax": min(fee, 0.5), "bad": bad, "soft": soft}

def get_security(chain_key, contract):
    if not valid_contract(chain_key, contract): return dict(UNKNOWN_SEC)
    key = (chain_key, contract.lower())
    with SEC_LOCK:
        c = SEC_CACHE.get(key)
        if c and time.time() - c[0] < (21600 if c[1]["known"] else 600): return c[1]
    sec = dict(UNKNOWN_SEC)
    try:
        if chain_key == "SOL":
            sec = _sec_sol(contract); API.report("sol-rpc", sec["known"])
        else:
            cid = CHAINS[chain_key]["chain_id"]
            r = http_get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}", params={"contract_addresses": contract}, limiter=SEC_LIMITER, timeout=10, retries=1)
            API.report("goplus", bool(r is not None and r.status_code == 200))
            if r is not None and r.status_code == 200:
                result = r.json().get("result") or {}
                d = result.get(contract.lower()) or result.get(contract) or {}
                if d:
                    def tax(k):
                        v = fnum(d.get(k)); v = v / 100.0 if v > 1 else v
                        return max(0.0, min(v, 0.5))
                    bad = next((n for k, n in HARD_BAD.items() if str(d.get(k)) == "1"), None)
                    if bad is None and str(d.get("is_open_source")) == "0": bad = "not_open_source"
                    sec = {"known": d.get("buy_tax") not in (None, "") and d.get("sell_tax") not in (None, ""), "buy_tax": tax("buy_tax"), "sell_tax": tax("sell_tax"), "bad": bad, "soft": [f for f in SOFT_FLAGS if str(d.get(f)) == "1"]}
    except Exception as e: log.warning("Security check loi %s: %s", contract[:8], e)
    with SEC_LOCK:
        SEC_CACHE[key] = (time.time(), sec)
        if len(SEC_CACHE) > 3000: SEC_CACHE.pop(next(iter(SEC_CACHE)), None)
    return sec

def risk_buffer(tok, pool, sec):
    tm = CHAINS[tok["chain"]]["transfer_min"] + CEX_EXTRA_MIN.get(tok["cex"], 2.0)
    move = max(abs(pool.get("chg_m5", 0)) * math.sqrt(tm / 5.0), abs(pool.get("chg_h1", 0)) * math.sqrt(tm / 60.0))
    buf = RISK_FLOOR + RISK_K * move + SOFT_FLAG_BUFFER * len(sec.get("soft", []))
    if not sec.get("known"): buf += UNKNOWN_SEC_BUFFER
    if pool.get("is_new"): buf += NEW_POOL_BUFFER
    return buf, tm

def choose_size(pool, depth):
    cap_cex = min(depth_usd(depth["bids"]), depth_usd(depth["asks"])) * SIZE_DEPTH_FRAC
    cap_liq = pool["liq"] * SIZE_LIQ_FRAC
    size = min(cap_cex, cap_liq, ORDER_SIZE_MAX)
    if pool.get("is_new"): size = min(size, NEW_POOL_MAX_SIZE)
    if size < ORDER_SIZE_MIN: return None
    return math.floor(size / 10) * 10

def calc_ai_score(pool, gross, adj_pct):
    s = min(max(adj_pct, 0) / 5.0 * 40, 40)
    s += min(pool["vol24"] / max(pool["liq"], 1) * 20, 20)
    s += min(pool.get("txns_h1", 0) / 100.0 * 15, 15)
    age = pool.get("age_h", 0)
    s += 10 if age < 48 else 7 if age < 168 else 4
    s += max(0.0, 15 - abs(pool.get("chg_m5", 0)) * 2)
    return round(min(s, 100), 1)

def evaluate(chain_key, tok, pool, depth, size=None):
    _local.eval_error = False
    cfg, st = CHAINS[chain_key], STATS[chain_key]
    ex = EXCHANGES[tok["cex"]]
    fee_cex = ex.taker_fee(tok["cex_pair"])
    bids, asks = depth["bids"], depth["asks"]
    if (asks[0][0] - bids[0][0]) / asks[0][0] * 100 > MAX_CEX_SPREAD: return []
    sec = get_security(chain_key, tok["contract"])
    if sec["bad"] or sec["buy_tax"] > TAX_MAX or sec["sell_tax"] > TAX_MAX:
        st.inc("suspect"); return []
    dec = get_decimals(chain_key, tok["contract"])
    if dec is None:
        st.inc("real_price_fail"); _local.eval_error = True; return []
    usd = size or choose_size(pool, depth)
    if not usd: return []
    buy_tax, sell_tax = sec["buy_tax"], sec["sell_tax"]
    buf, tm = risk_buffer(tok, pool, sec)
    gas = cfg["gas_usd"]
    common = {"cex": tok["cex"], "usd_size": usd, "risk_buf": buf, "transfer_min": tm, "cex_fee": fee_cex,
              "buy_tax": buy_tax, "sell_tax": sell_tax, "sec_known": sec["known"], "sec_soft": sec["soft"],
              "mexc_best": (bids[0][0], asks[0][0]), "withdraw_enable": tok.get("withdraw_enable"),
              "deposit_enable": tok.get("deposit_enable"), "checked_at": time.time()}
    out = []
    if tok.get("withdraw_enable"):
        if tok.get("withdraw_fee") is None:
            st.inc("fee_unknown")
        else:
            w = walk_usd(asks, usd)
            if w:
                avg, tokens = w
                tokens_after_fee = tokens * (1 - fee_cex)
                tokens_net = tokens_after_fee - tok["withdraw_fee"]
                if tokens_net > 0 and tokens_after_fee >= tok["withdraw_min"]:
                    q, kind = dex_sell_quote(chain_key, tok["contract"], dec, tokens_net)
                    if kind == "error": st.inc("real_price_fail"); st.inc("api_skip"); _local.eval_error = True
                    elif kind == "noroute": st.inc("suspect")
                    else:
                        st.inc("real_price_ok")
                        impact = q["impact"] if q["impact"] is not None else 0.0
                        sell_px = q["usd_out"] / tokens_net
                        gross = (sell_px / avg - 1) * 100
                        revenue = q["usd_out"] * (1 - EXTRA_SLIPPAGE) * (1 - sell_tax) - gas
                        net = revenue - usd
                        net_pct = net / usd * 100
                        if -5 < gross <= MAX_GROSS and impact <= MAX_IMPACT_PCT:
                            if abs(sell_px - pool["price"]) / pool["price"] > 0.15: st.inc("suspect")
                            else:
                                adj_pct = net_pct - buf
                                out.append(dict(common, direction="CEX_TO_DEX", tokens_bought=tokens_after_fee,
                                                tokens_net=tokens_net, mexc_avg=avg, dex_exec=sell_px, usd_out=q["usd_out"],
                                                net_usd=net, net_pct=net_pct, adj_net_pct=adj_pct, adj_net_usd=net - usd * buf / 100,
                                                gross_pct=gross, gas=gas, withdraw_fee=tok["withdraw_fee"],
                                                withdraw_fee_usd=tok["withdraw_fee"] * avg, impact_dex=impact, real_source=q["source"],
                                                ai_score=calc_ai_score(pool, gross, adj_pct)))
    if tok.get("deposit_enable"):
        q, kind = dex_buy_quote(chain_key, tok["contract"], dec, usd)
        if kind == "error": st.inc("real_price_fail"); st.inc("api_skip"); _local.eval_error = True
        elif kind == "noroute": st.inc("suspect")
        else:
            st.inc("real_price_ok")
            impact = q["impact"] if q["impact"] is not None else 0.0
            tokens_dep = q["tokens"] * (1 - EXTRA_SLIPPAGE) * (1 - buy_tax)
            sellable = check_sellable(chain_key, tok["contract"], dec, tokens_dep, usd)
            if sellable is None: st.inc("api_skip"); _local.eval_error = True
            elif sellable is False:
                st.inc("suspect"); log.info("KHONG BAN DUOC %s %s", chain_key, tok["symbol"])
            else:
                proceeds = walk_tokens(bids, tokens_dep)
                if proceeds:
                    cost = usd + gas * 2
                    revenue = proceeds * (1 - fee_cex)
                    net = revenue - cost
                    avg = proceeds / tokens_dep
                    gross = (avg / q["price"] - 1) * 100
                    net_pct = net / cost * 100
                    if -5 < gross <= MAX_GROSS and impact <= MAX_IMPACT_PCT:
                        if abs(q["price"] - pool["price"]) / pool["price"] > 0.15: st.inc("suspect")
                        else:
                            adj_pct = net_pct - buf
                            out.append(dict(common, direction="DEX_TO_CEX", tokens_out=q["tokens"], tokens_dep=tokens_dep,
                                            tokens_bought=q["tokens"], tokens_net=tokens_dep, mexc_avg=avg,
                                            dex_exec=q["price"], proceeds=proceeds, net_usd=net, net_pct=net_pct,
                                            adj_net_pct=adj_pct, adj_net_usd=net - cost * buf / 100, gross_pct=gross,
                                            gas=gas * 2, withdraw_fee=0.0, withdraw_fee_usd=0.0, impact_dex=impact,
                                            real_source=q["source"], ai_score=calc_ai_score(pool, gross, adj_pct)))
    return out

COOLDOWN = {}
CD_LOCK = threading.Lock()
def should_alert(key, adj_pct):
    with CD_LOCK: last = COOLDOWN.get(key)
    if not last: return True
    t, p = last; age = (datetime.now() - t).total_seconds()
    return age >= COOLDOWN_MIN * 60 or (age >= 300 and adj_pct > p * 1.3)
def mark_alerted(key, adj_pct):
    with CD_LOCK: COOLDOWN[key] = (datetime.now(), adj_pct)

PAUSED = set()
BL = {}
BL_LOCK = threading.Lock()
BAD_STREAK = {}
def is_blacklisted(chain_key, tok):
    now = time.time()
    with BL_LOCK:
        for k in (f"SYM:{tok['symbol'].upper()}", f"TOK:{chain_key}:{tok['dedup_key']}"):
            u = BL.get(k)
            if u and u > now: return True
    return False

class PaperDB:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            c = self.conn
            try: c.execute("PRAGMA journal_mode=WAL")
            except Exception: pass
            c.execute("""CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chain TEXT, symbol TEXT, contract TEXT,
                direction TEXT, pair TEXT, dex TEXT, order_usd REAL, token_amount REAL, mexc_avg REAL,
                dex_exec REAL, dex_price REAL, liq REAL, gross_pct REAL, net_pct REAL, net_usd REAL,
                net_pct_5m REAL, net_pct_10m REAL, raw TEXT)""")
            for col, typ in (("adj_net_pct", "REAL"), ("risk_buf", "REAL"), ("st_5m", "TEXT"), ("st_10m", "TEXT"), ("cex", "TEXT"), ("ai_score", "REAL")):
                try: c.execute(f"ALTER TABLE signals ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError: pass
            c.execute("""CREATE TABLE IF NOT EXISTS followups(fid INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, minutes INTEGER, due REAL, done INTEGER DEFAULT 0)""")
            c.execute("CREATE TABLE IF NOT EXISTS blacklist(key TEXT PRIMARY KEY, until REAL, reason TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS status_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, cex TEXT, chain TEXT, symbol TEXT, kind TEXT)")
            c.execute("UPDATE followups SET done=0 WHERE done=2")
            c.commit()
    def insert(self, chain, tok, pool, p):
        with self.lock:
            cur = self.conn.execute("INSERT INTO signals(ts,chain,symbol,contract,direction,pair,dex,order_usd,token_amount,mexc_avg,dex_exec,dex_price,liq,gross_pct,net_pct,net_usd,adj_net_pct,risk_buf,cex,ai_score,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(), chain, tok["symbol"], tok["contract"], p["direction"], tok["cex_pair"], pool["dex"], p["usd_size"], p["tokens_bought"], p["mexc_avg"], p["dex_exec"], pool["price"], pool["liq"], p["gross_pct"], p["net_pct"], p["net_usd"], p["adj_net_pct"], p["risk_buf"], tok["cex"], p["ai_score"], json.dumps({"profit": p, "pool": pool, "tok": tok}, default=str)))
            sid = cur.lastrowid; now = time.time()
            for m in FOLLOWUP_MIN: self.conn.execute("INSERT INTO followups(signal_id,minutes,due) VALUES(?,?,?)", (sid, m, now + m * 60))
            self.conn.commit(); return sid
    def due_followups(self, limit=20):
        with self.lock:
            rows = self.conn.execute("""SELECT f.fid,f.signal_id,f.minutes,s.chain,s.direction,s.raw FROM followups f JOIN signals s ON s.id=f.signal_id WHERE f.done=0 AND f.due<=? ORDER BY f.due LIMIT ?""", (time.time(), limit)).fetchall()
            for r in rows: self.conn.execute("UPDATE followups SET done=2 WHERE fid=?", (r[0],))
            self.conn.commit(); return rows
    def finish_followup(self, fid, sid, minutes, value, status):
        if minutes not in (5, 10): return
        with self.lock:
            self.conn.execute(f"UPDATE signals SET net_pct_{minutes}m=?, st_{minutes}m=? WHERE id=?", (value, status, sid))
            self.conn.execute("UPDATE followups SET done=1 WHERE fid=?", (fid,)); self.conn.commit()
    def recent(self, n=50):
        with self.lock:
            cur = self.conn.execute("SELECT id,ts,chain,cex,symbol,direction,order_usd,gross_pct,net_pct,adj_net_pct,ai_score,net_pct_5m,st_5m,net_pct_10m,st_10m FROM signals ORDER BY id DESC LIMIT ?", (n,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    def chain_outcomes(self, chain, n=40):
        with self.lock:
            return self.conn.execute("SELECT net_pct_5m, st_5m FROM signals WHERE chain=? AND st_5m IN ('ok','gone') ORDER BY id DESC LIMIT ?", (chain, n)).fetchall()
    def summary(self, n=500):
        with self.lock:
            rows = self.conn.execute("SELECT chain,cex,direction,net_pct,net_pct_5m,st_5m,net_pct_10m,st_10m,ai_score FROM signals ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        agg = {}
        for chain, cex, d, net, n5, s5, n10, s10, ai in rows:
            a = agg.setdefault((chain, cex or "?", d), {"n": 0, "net": 0.0, "n5": 0, "w5": 0, "sum5": 0.0, "n10": 0, "w10": 0})
            a["n"] += 1; a["net"] += net or 0
            if s5 in ("ok", "gone"):
                a["n5"] += 1
                if s5 == "ok" and (n5 or 0) > 0: a["w5"] += 1
                a["sum5"] += n5 if (s5 == "ok" and n5 is not None) else 0.0
            if s10 in ("ok", "gone"):
                a["n10"] += 1
                if s10 == "ok" and (n10 or 0) > 0: a["w10"] += 1
        return [{"chain": ch, "cex": cx, "direction": d, "signals": a["n"], "avg_alert_net": round(a["net"] / a["n"], 3), "followed_5m": a["n5"], "winrate_5m": round(a["w5"] / a["n5"], 3) if a["n5"] else None, "avg_net_5m": round(a["sum5"] / a["n5"], 3) if a["n5"] else None, "winrate_10m": round(a["w10"] / a["n10"], 3) if a["n10"] else None} for (ch, cx, d), a in sorted(agg.items())]
    def ai_vs_outcome(self, n=500):
        with self.lock:
            rows = self.conn.execute("SELECT ai_score, net_pct_5m, st_5m FROM signals WHERE st_5m IN ('ok','gone') AND ai_score IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        buckets = {}
        for ai, v, s in rows:
            b = int(ai // 20) * 20
            x = buckets.setdefault(b, [0, 0]); x[0] += 1
            if s == "ok" and (v or 0) > 0: x[1] += 1
        return {f"{b}-{b+19}": {"n": x[0], "winrate": round(x[1] / x[0], 3)} for b, x in sorted(buckets.items())}
    def had_recent_signal(self, symbol, hours=24):
        with self.lock:
            since = (datetime.now() - timedelta(hours=hours)).isoformat()
            return self.conn.execute("SELECT 1 FROM signals WHERE symbol=? AND ts>? LIMIT 1", (symbol, since)).fetchone() is not None
    def log_event(self, cex, chain, symbol, kind):
        with self.lock:
            self.conn.execute("INSERT INTO status_events(ts,cex,chain,symbol,kind) VALUES(?,?,?,?,?)", (time.time(), cex, chain, symbol, kind)); self.conn.commit()
    def load_blacklist(self):
        with self.lock: return self.conn.execute("SELECT key, until FROM blacklist WHERE until>?", (time.time(),)).fetchall()
    def save_blacklist(self, key, until, reason):
        with self.lock: self.conn.execute("INSERT OR REPLACE INTO blacklist(key,until,reason) VALUES(?,?,?)", (key, until, reason)); self.conn.commit()
    def del_blacklist(self, key):
        with self.lock: self.conn.execute("DELETE FROM blacklist WHERE key=?", (key,)); self.conn.commit()

DB = PaperDB(DB_PATH)
for _k, _u in DB.load_blacklist(): BL[_k] = _u

_AD_CACHE = {}
def adaptive_threshold(chain_key):
    if not ADAPTIVE: return THRESHOLD_NET
    c = _AD_CACHE.get(chain_key)
    if c and time.time() - c[0] < 300: return c[1]
    rows = DB.chain_outcomes(chain_key, 40)
    thr = THRESHOLD_NET
    if len(rows) >= 15:
        wr = sum(1 for v, s in rows if s == "ok" and (v or 0) > 0) / len(rows)
        if wr < 0.35: thr = THRESHOLD_NET + 0.4
        elif wr < 0.50: thr = THRESHOLD_NET + 0.2
        elif wr > 0.70: thr = max(THRESHOLD_NET * 0.8, THRESHOLD_NET - 0.1)
    _AD_CACHE[chain_key] = (time.time(), thr)
    return thr

def send_telegram(text):
    try:
        r = session().post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        if r.status_code != 200: log.warning("Telegram that bai %s: %s", r.status_code, r.text[:200]); return False
        return True
    except Exception as e: log.warning("Telegram loi: %s", e); return False

def fmt_price(v):
    try:
        v = float(v or 0)
        if v == 0: return "0"
        if abs(v) >= 1: return f"{v:.4f}"
        if abs(v) >= 0.01: return f"{v:.6f}"
        return f"{v:.10f}"
    except Exception: return str(v)
def fmt_tok(v):
    try: v = float(v); return f"{v:,.0f}" if v >= 1000 else f"{v:,.4f}"
    except Exception: return str(v)
def dex_swap_urls(chain_key, contract):
    c = contract
    if chain_key == "SOL": return f"https://jup.ag/swap/USDC-{c}", f"https://jup.ag/swap/{c}-USDC"
    cid = CHAINS[chain_key]["chain_id"]; st = STABLE[cid][0]
    if chain_key == "BNB": return (f"https://pancakeswap.finance/swap?chain=bsc&inputCurrency={st}&outputCurrency={c}", f"https://pancakeswap.finance/swap?chain=bsc&inputCurrency={c}&outputCurrency={st}")
    name = {"BASE": "base", "ETH": "ethereum", "POLYGON": "polygon", "ARBITRUM": "arbitrum"}[chain_key]
    return (f"https://app.uniswap.org/swap?chain={name}&inputCurrency={st}&outputCurrency={c}", f"https://app.uniswap.org/swap?chain={name}&inputCurrency={c}&outputCurrency={st}")

def send_alert(chain_key, tok, pool, p, tag=""):
    cfg = CHAINS[chain_key]; ex = EXCHANGES[tok["cex"]]
    sym, usd, cexn = tok["symbol"], p["usd_size"], tok["cex"]
    cex_url = ex.trade_url(tok["cex_pair"])
    buy_url, sell_url = dex_swap_urls(chain_key, tok["contract"])
    bid, ask = p["mexc_best"]; fee_pct = p["cex_fee"] * 100
    if p["direction"] == "CEX_TO_DEX":
        steps = (f"1⃣ Mua <b>{fmt_tok(p['tokens_bought'])} {esc(sym)}</b> tren <a href=\"{esc_url(cex_url)}\">{cexn}</a> (gia TB ${fmt_price(p['mexc_avg'])}, phi {fee_pct:.2f}%)\n"
                 f"2⃣ Rut ve <b>{chain_key}</b> ({esc(tok.get('raw_network') or '')}): phi {fmt_tok(p['withdraw_fee'])} {esc(sym)} (~${p['withdraw_fee_usd']:.2f}), min {fmt_tok(tok['withdraw_min'])} → nhan {fmt_tok(p['tokens_net'])}\n"
                 f"3⃣ <a href=\"{esc_url(sell_url)}\">Ban tren DEX</a> ({esc(p['real_source'])}) → ≈ <b>${p['usd_out']:.2f}</b> (gia ${fmt_price(p['dex_exec'])}, impact {p['impact_dex']:.2f}%)")
        pnl = (f"Chi ${usd:.2f} → thu ${p['usd_out']:.2f} (sau slippage {EXTRA_SLIPPAGE*100:.1f}%{', tax ban ' + format(p['sell_tax']*100, '.1f') + '%' if p['sell_tax'] else ''}) - gas ${p['gas']:.2f}")
        dir_txt = f"{cexn} → DEX"
    else:
        steps = (f"1⃣ <a href=\"{esc_url(buy_url)}\">Mua tren DEX</a> ({esc(p['real_source'])}): ${usd:.2f} → <b>{fmt_tok(p['tokens_out'])} {esc(sym)}</b> (gia ${fmt_price(p['dex_exec'])}, impact {p['impact_dex']:.2f}%)\n"
                 f"2⃣ Nap len <a href=\"{esc_url(cex_url)}\">{cexn}</a> qua mang <b>{chain_key}</b> ({esc(tok.get('raw_network') or '')}) → ~{p['transfer_min']:.0f} phut, gas ~${p['gas']:.2f}{', tax mua ' + format(p['buy_tax']*100, '.1f') + '%' if p['buy_tax'] else ''}\n"
                 f"3⃣ Ban {fmt_tok(p['tokens_dep'])} {esc(sym)} tren {cexn} (gia TB ${fmt_price(p['mexc_avg'])}, phi {fee_pct:.2f}%) → ≈ <b>${p['proceeds']*(1-p['cex_fee']):.2f}</b>")
        pnl = f"Chi ${usd + p['gas']:.2f} (gom gas) → thu ${p['proceeds']*(1-p['cex_fee']):.2f}"
        dir_txt = f"DEX → {cexn}"
    warn = []
    if not p.get("sec_known"): warn.append("chua co du lieu security")
    if p.get("sec_soft"): warn.append("co: " + ", ".join(p["sec_soft"]))
    if pool.get("is_new"): warn.append(f"pool moi {pool['age_h']:.0f}h")
    en = f"Nap {'✅' if p.get('deposit_enable') else '❌'} / Rut {'✅' if p.get('withdraw_enable') else '❌'}"
    age = int(time.time() - p["checked_at"])
    conf = "✅ da xac nhan lai bang quote moi" if p.get("confirmed") else "chua xac nhan lai"
    msg = (f"{cfg['emoji']} <b>{esc(sym)} [{chain_key}] {dir_txt}</b>{(' | ' + tag) if tag else ''}\n"
           f"💰 Net sau buffer: <b>{p['adj_net_pct']:.2f}% (${p['adj_net_usd']:.2f})</b> | Net truoc buffer {p['net_pct']:.2f}% (${p['net_usd']:.2f})\n"
           f"📊 Gross {p['gross_pct']:.2f}% | Buffer rui ro {p['risk_buf']:.2f}% | Size ${usd:.0f} | nguong {adaptive_threshold(chain_key):.2f}%\n"
           f"{'─'*22}\n{steps}\n{'─'*22}\n🧾 {pnl}\n🏦 {cexn} {esc(tok['cex_pair'])}: bid {fmt_price(bid)} / ask {fmt_price(ask)} | {en}\n"
           f"🦄 {esc(pool['dex'])} {esc(pool['pair_name'])}: ${fmt_price(pool['price'])} | Liq ${pool['liq']:,.0f} | Vol24h ${pool['vol24']:,.0f} | Tx1h {pool['txns_h1']} | tuoi {pool.get('age_h', 0):.0f}h\n"
           f"📈 Bien dong 5m {pool.get('chg_m5', 0):+.1f}% | 1h {pool.get('chg_h1', 0):+.1f}%\n"
           f"{'⚠ ' + esc('; '.join(warn)) + chr(10) if warn else ''}📜 <code>{esc(tok['contract'])}</code>\n"
           f"🔗 <a href=\"{esc_url(cex_url)}\">{cexn}</a> | <a href=\"{esc_url(buy_url)}\">DEX Buy</a> | <a href=\"{esc_url(sell_url)}\">DEX Sell</a> | <a href=\"{esc_url(pool['url'])}\">Chart</a> | <a href=\"{esc_url(cfg['explorer'] + tok['contract'])}\">Explorer</a>\n"
           f"⏱ Du lieu {age}s truoc, {conf} | Score tham khao {p['ai_score']:.0f}/100 | {datetime.now().strftime('%H:%M:%S %d/%m')}\n"
           f"⚠ Gia co the doi trong luc chuyen token (~{p['transfer_min']:.0f} phut). Day la PAPER, khong phai loi khuyen.")
    return send_telegram(msg)

def summary_text(limit=12):
    rows = DB.summary()
    if not rows: return "Chua co du lieu."
    lines = ["<b>Thong ke (500 signal gan nhat)</b>"]
    for r in rows[:limit]:
        wr = f"{r['winrate_5m']*100:.0f}%" if r["winrate_5m"] is not None else "n/a"
        av = f"{r['avg_net_5m']:+.2f}%" if r["avg_net_5m"] is not None else "n/a"
        d = "C→D" if r["direction"] == "CEX_TO_DEX" else "D→C"
        lines.append(f"{r['cex']} {r['chain']} {d}: n={r['signals']} | win5m={wr} (n={r['followed_5m']}) | avg5m={av}")
    return "\n".join(lines)

def check_candidate(chain_key, tok, pool):
    st = STATS[chain_key]
    try:
        depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"])
        if not depth: return None
        st.inc("fetched"); st.success()
        thr = adaptive_threshold(chain_key)
        best = None
        for p in evaluate(chain_key, tok, pool, depth):
            if p["gross_pct"] >= PREFILTER_GROSS: st.inc("opportunity")
            if p["adj_net_pct"] >= thr and p["adj_net_usd"] >= MIN_NET_USD and (not best or p["adj_net_pct"] > best["adj_net_pct"]): best = p
        return (tok, pool, best) if best else None
    except Exception:
        st.inc("errors"); log.exception("Loi xu ly %s %s", chain_key, tok["symbol"]); return None

def confirm_signal(chain_key, tok, pool, p):
    time.sleep(CONFIRM_DELAY)
    depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"])
    if not depth: return None
    thr = adaptive_threshold(chain_key)
    for p2 in evaluate(chain_key, tok, pool, depth, size=p["usd_size"]):
        if p2["direction"] == p["direction"]:
            if p2["adj_net_pct"] >= thr and p2["adj_net_usd"] >= MIN_NET_USD:
                p2["confirmed"] = True; return p2
            return None
    return None

def process_candidates(chain_key, results, tag=""):
    st = STATS[chain_key]
    for tok, pool, p in sorted(results, key=lambda r: -r[2]["adj_net_pct"]):
        key = (chain_key, tok["cex"], tok["dedup_key"], p["direction"])
        if not should_alert(key, p["adj_net_pct"]): continue
        p2 = confirm_signal(chain_key, tok, pool, p)
        if not p2: st.inc("unconfirmed"); continue
        if send_alert(chain_key, tok, pool, p2, tag):
            mark_alerted(key, p2["adj_net_pct"]); st.inc("signals"); DB.insert(chain_key, tok, pool, p2)

def prefilter(tok, pool, book):
    bt = book.get(tok["cex_pair"])
    if not bt: return False
    bid, ask = bt
    fee_cex = EXCHANGES[tok["cex"]].taker_fee(tok["cex_pair"])
    imp = (2 * ORDER_SIZE_USD / max(pool["liq"], 1)) * 0.5
    f = pool["fee"]
    buy_px, sell_px = pool["price"] * (1 + f + imp), pool["price"] * (1 - f - imp)
    g1 = (sell_px / (ask * (1 + fee_cex)) - 1) * 100 if (tok.get("withdraw_enable") and tok.get("withdraw_fee") is not None) else -99
    g2 = (bid * (1 - fee_cex) / buy_px - 1) * 100 if tok.get("deposit_enable") else -99
    return max(g1, g2) >= PREFILTER_GROSS

def scan_chain_once(chain_key, stop):
    st = STATS[chain_key]
    MULTI.ensure_static()
    entries = MULTI.by_chain.get(chain_key, [])
    if not entries: return False
    books = {n: ex.get_book() for n, ex in EXCHANGES.items()}
    if not any(books.values()): return False
    for i in range(0, len(entries), DEX_BATCH):
        if stop.is_set(): return True
        if chain_key in PAUSED: stop.wait(5); return True
        with DEX_429_LOCK:
            wait = DEX_429_UNTIL - time.time()
        if wait > 0: stop.wait(min(wait, 60))
        st.beat()
        chunk = entries[i:i + DEX_BATCH]
        pools = fetch_dex_pools(chain_key, [e["contract"] for e in chunk])
        if pools is None: st.inc("errors"); continue
        candidates = []
        for e in chunk:
            pool = pools.get(e["dedup_key"])
            if not pool: continue
            for tok in e["listings"]:
                if not symbol_ok(tok["symbol"], pool["base_symbol"]): continue
                st.inc("attempted")
                if is_blacklisted(chain_key, tok): st.inc("blacklisted_skip"); continue
                if prefilter(tok, pool, books.get(tok["cex"], {})): candidates.append((tok, pool))
        if candidates:
            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(lambda c: check_candidate(chain_key, c[0], c[1]), candidates))
            process_candidates(chain_key, [r for r in results if r])
        stop.wait(1.0)
    return True

def run_chain_loop(chain_key, stop):
    log.info("[%s] start", chain_key)
    empty, warned = 0, False
    while not stop.is_set():
        try:
            STATS[chain_key].beat()
            if not scan_chain_once(chain_key, stop):
                empty += 1; stop.wait(60 if empty <= 10 else 300); continue
            empty = 0
            if STATS[chain_key].snapshot()["dex_pairs"] == 0 and time.time() - MULTI.ts < 3600:
                if not warned:
                    warned = True
                    send_telegram(f"⚠ {esc(chain_key)}: Dexscreener khong tra pair nao cho slug <code>{esc(CHAINS[chain_key]['dex_chain'])}</code>.")
                stop.wait(3600); continue
            stop.wait(SCAN_PAUSE_SEC)
        except Exception:
            STATS[chain_key].inc("errors"); log.exception("[%s] loi vong chinh", chain_key); stop.wait(20)

class Worker:
    def __init__(self, chain_key): self.chain = chain_key; self.stop = None; self.thread = None
    def start(self):
        self.stop = threading.Event(); STATS[self.chain].beat()
        self.thread = threading.Thread(target=run_chain_loop, args=(self.chain, self.stop), daemon=True, name=f"Bot-{self.chain}"); self.thread.start()
    def restart(self):
        if self.stop: self.stop.set()
        if self.thread: self.thread.join(timeout=5)
        self.start()

PRIO_EX = ThreadPoolExecutor(max_workers=1, thread_name_prefix="PRIO")
KIND_TXT = {"open_withdraw": "🔓 MỞ RÚT", "open_deposit": "🔓 MỞ NẠP", "close_withdraw": "🔒 ĐÓNG RÚT", "close_deposit": "🔒 ĐÓNG NẠP"}

def check_opened(ev):
    cex, chain, tok, kind = ev
    try:
        if is_blacklisted(chain, tok): return
        pools = fetch_dex_pools(chain, [tok["contract"]])
        pool = (pools or {}).get(tok["dedup_key"])
        if not pool or not symbol_ok(tok["symbol"], pool["base_symbol"]): return
        res = check_candidate(chain, tok, pool)
        tag = f"{KIND_TXT[kind]} tren {cex}"
        if res: process_candidates(chain, [res], tag=tag)
        else: send_telegram(f"{KIND_TXT[kind]} <b>{esc(tok['symbol'])}</b> [{chain}] tren {cex} ({esc(tok.get('raw_network') or '')})\nPool DEX: {esc(pool['dex'])} liq ${pool['liq']:,.0f}. Hien chua co chenh lech dat nguong - dang theo doi.")
    except Exception: log.exception("check_opened loi")

def handle_status_events(events):
    closes, opens = [], []
    for cex, chain, tok, kind in events:
        DB.log_event(cex, chain, tok["symbol"], kind)
        if kind.startswith("open"): opens.append((cex, chain, tok, kind))
        elif DB.had_recent_signal(tok["symbol"], 24): closes.append((cex, chain, tok, kind))
    for cex, chain, tok, kind in closes[:10]:
        send_telegram(f"{KIND_TXT[kind]} <b>{esc(tok['symbol'])}</b> [{chain}] tren {cex} - token nay vua co signal trong 24h, khong con thuc hien duoc chieu nay.")
    for ev in opens[:15]: PRIO_EX.submit(check_opened, ev)

def process_followup(row):
    fid, sid, minutes, chain, direction, raw = row
    value, status = None, "error"
    try:
        j = json.loads(raw); tok, size = j["tok"], j["profit"]["usd_size"]
        pools = fetch_dex_pools(chain, [tok["contract"]])
        if pools is None: raise RuntimeError("dex api")
        pool = pools.get(tok["dedup_key"])
        depth = EXCHANGES[tok["cex"]].depth(tok["cex_pair"]) if (pool and tok["cex"] in EXCHANGES) else None
        if not pool or not depth: status = "gone"
        else:
            status = "gone"
            res = evaluate(chain, tok, pool, depth, size=size)
            for p in res:
                if p["direction"] == direction: value, status = p["adj_net_pct"], "ok"
            if status == "gone" and getattr(_local, "eval_error", False): status = "error"
    except Exception as e: log.warning("Follow-up loi: %s", e); status = "error"
    DB.finish_followup(fid, sid, minutes, value, status)
    if minutes == 5 and status in ("ok", "gone"):
        try:
            tok = json.loads(raw)["tok"]; key = (chain, tok["cex"], tok["dedup_key"], direction)
            win = status == "ok" and (value or 0) > 0
            BAD_STREAK[key] = 0 if win else BAD_STREAK.get(key, 0) + 1
            if BAD_STREAK[key] >= BAD_STREAK_BLACKLIST:
                until = time.time() + BLACKLIST_HOURS * 3600; bk = f"TOK:{chain}:{tok['dedup_key']}"
                with BL_LOCK: BL[bk] = until
                DB.save_blacklist(bk, until, "bad streak"); BAD_STREAK[key] = 0
                send_telegram(f"🚫 Tu dong blacklist {esc(tok['symbol'])} [{chain}] {BLACKLIST_HOURS:.0f}h (bao ao {BAD_STREAK_BLACKLIST} lan lien tiep)")
        except Exception: log.exception("blacklist loi")

def followup_loop():
    ex = ThreadPoolExecutor(max_workers=3, thread_name_prefix="FU")
    while True:
        time.sleep(10)
        try:
            rows = DB.due_followups()
            if rows: list(ex.map(process_followup, rows))
        except Exception: log.exception("followup_loop loi")

def handle_command(text):
    parts = text.strip().split(); cmd = parts[0].lower().split("@")[0]; arg = parts[1].upper() if len(parts) > 1 else ""
    if cmd in ("/start", "/help"):
        return ("/status - tinh trang bot\n/stats - thong ke win-rate\n/top - 10 signal gan nhat\n/deposit SYMBOL - nap/rut theo tung mang, tung san (chinh xac 100% voi API key)\n/ai - AI score vs ket qua that\n/pause CHAIN  /resume CHAIN\n/blacklist SYMBOL  /unblacklist SYMBOL")
    if cmd == "/status":
        lines = [f"<b>STATUS V10</b> {datetime.now().strftime('%H:%M %d/%m')} | CEX: {', '.join(EXCHANGES)}"]
        for k, st in STATS.items():
            s = st.snapshot()
            lines.append(f"{CHAINS[k]['emoji']} {k}{' (PAUSED)' if k in PAUSED else ''}: sig={s['signals']} opp={s['opportunity']} unconf={s['unconfirmed']} sus={s['suspect']} realOK={s['real_price_ok']} fail={s['real_price_fail']} feeUnk={s['fee_unknown']} thr={adaptive_threshold(k):.2f}")
        lines.append("CEX: " + ", ".join(f"{n}={c} token-mang" for n, c in MULTI.loaded.items()) + f" | cap nhat {int(time.time() - MULTI.ts)}s truoc")
        lines.append("API: " + esc(API.line()))
        return "\n".join(lines)
    if cmd == "/stats": return summary_text()
    if cmd == "/ai":
        d = DB.ai_vs_outcome()
        return "AI score vs win-rate 5m:\n" + ("\n".join(f"{k}: n={v['n']} win={v['winrate']*100:.0f}%" for k, v in d.items()) or "chua du du lieu")
    if cmd == "/top":
        rows = DB.recent(10)
        if not rows: return "Chua co signal."
        out = ["<b>10 signal gan nhat</b>"]
        for r in rows:
            f5 = f"{r['net_pct_5m']:+.2f}%" if r["net_pct_5m"] is not None else (r["st_5m"] or "...")
            out.append(f"#{r['id']} {esc(r['symbol'])} {r['cex']}/{r['chain']} adj {r['adj_net_pct'] or 0:.2f}% ${r['order_usd'] or 0:.0f} → 5m: {f5}")
        return "\n".join(out)
    if cmd == "/deposit":
        if not arg: return "Dung: /deposit SYMBOL"
        lines = [f"<b>NẠP/RÚT {esc(arg)}</b> (du lieu cache, theo mang)"]
        found = False
        for name, toks in MULTI.ex_tokens.items():
            for t in toks:
                if t["symbol"].upper() == arg:
                    found = True
                    fee = "?" if t["withdraw_fee"] is None else fmt_tok(t["withdraw_fee"])
                    lines.append(f"{name} [{t['chain']}] {esc(t.get('raw_network') or '')}: Nạp {'✅' if t['deposit_enable'] else '❌'} Rút {'✅' if t['withdraw_enable'] else '❌'} | phí rút {fee}")
        return "\n".join(lines) if found else f"Khong thay {esc(arg)} tren cac mang dang quet."
    if cmd == "/pause":
        if arg in CHAINS: PAUSED.add(arg); return f"Da pause {arg}"
        return "Chain khong hop le: " + ", ".join(CHAINS)
    if cmd == "/resume": PAUSED.discard(arg); return f"Da resume {arg}"
    if cmd == "/blacklist" and arg:
        until = time.time() + 7 * 86400
        with BL_LOCK: BL[f"SYM:{arg}"] = until
        DB.save_blacklist(f"SYM:{arg}", until, "manual"); return f"Da blacklist {esc(arg)} 7 ngay"
    if cmd == "/unblacklist" and arg:
        with BL_LOCK: BL.pop(f"SYM:{arg}", None)
        DB.del_blacklist(f"SYM:{arg}"); return f"Da go blacklist {esc(arg)}"
    return "Lenh khong hop le. /help"

def telegram_poll_loop():
    offset = None
    while True:
        try:
            r = session().get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates", params={"timeout": 25, "offset": offset}, timeout=35)
            if r.status_code == 409: time.sleep(30); continue
            if r.status_code != 200: time.sleep(10); continue
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if str((m.get("chat") or {}).get("id")) != CHAT_ID or not m.get("text", "").startswith("/"): continue
                try: send_telegram(handle_command(m["text"]))
                except Exception: log.exception("Lenh loi")
        except Exception as e: log.warning("Poll Telegram loi: %s", e); time.sleep(10)

def heartbeat_loop():
    last_daily = None; last_hb = time.time()
    while True:
        time.sleep(30)
        try:
            now_vn = datetime.utcnow() + timedelta(hours=7)
            if now_vn.hour == 21 and now_vn.minute < 2 and last_daily != now_vn.date():
                last_daily = now_vn.date()
                msg = f"📊 <b>BAO CAO NGAY {now_vn.strftime('%d/%m/%Y')} V10</b>\n"
                for k, st in STATS.items():
                    s = st.snapshot()
                    msg += (f"{CHAINS[k]['emoji']} {k}: quet {s['attempted']} | opp {s['opportunity']} | bao {s['signals']} | khong xac nhan {s['unconfirmed']} | nghi van {s['suspect']}\n")
                send_telegram(msg + "\n" + summary_text(10))
            if time.time() - last_hb >= HEARTBEAT_MIN * 60:
                last_hb = time.time()
                msg = f"💓 <b>HEARTBEAT V10 {datetime.now().strftime('%H:%M %d/%m')}</b> | CEX: {', '.join(EXCHANGES)}\n"
                for k, st in STATS.items():
                    s = st.snapshot()
                    msg += (f"{CHAINS[k]['emoji']} {k}: try={s['attempted']} fetch={s['fetched']} sig={s['signals']} unconf={s['unconfirmed']} sus={s['suspect']} realOK={s['real_price_ok']} fail={s['real_price_fail']} err={s['errors']} thr={adaptive_threshold(k):.2f}\n")
                send_telegram(msg + "\nAPI: " + esc(API.line()))
        except Exception: log.exception("Heartbeat loi")

def cleanup():
    now = time.time()
    with CD_LOCK:
        for k in [k for k, (t, _) in COOLDOWN.items() if (datetime.now() - t).total_seconds() > COOLDOWN_MIN * 240]: COOLDOWN.pop(k, None)
    with SEC_LOCK:
        for k in [k for k, (ts, _) in SEC_CACHE.items() if now - ts > 21600]: SEC_CACHE.pop(k, None)
    with SELL_LOCK:
        for k in [k for k, (ts, _) in SELL_CACHE.items() if now - ts > 600]: SELL_CACHE.pop(k, None)
    with BL_LOCK:
        for k in [k for k, u in BL.items() if u < now]: BL.pop(k, None)

def start_web():
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    def authed(): return (not ADMIN_TOKEN) or request.args.get("token") == ADMIN_TOKEN
    @app.route("/")
    def home(): return f"V10.0 MERGED - signals: {sum(s.snapshot()['signals'] for s in STATS.values())} | CEX: {', '.join(EXCHANGES)}"
    @app.route("/health")
    def health(): return "OK", 200
    @app.route("/stats")
    def stats():
        if not authed(): return "forbidden", 403
        return jsonify({k: s.snapshot() for k, s in STATS.items()})
    @app.route("/signals")
    def signals():
        if not authed(): return "forbidden", 403
        return jsonify(DB.recent(50))
    @app.route("/summary")
    def summary():
        if not authed(): return "forbidden", 403
        return jsonify({"by_group": DB.summary(), "ai_vs_outcome": DB.ai_vs_outcome()})
    log.info("Web tren 0.0.0.0:%s", PORT)
    try:
        from waitress import serve; serve(app, host="0.0.0.0", port=PORT)
    except ImportError: app.run(host="0.0.0.0", port=PORT)

def validate_config():
    w = []
    if MIN_LIQ_USD * SIZE_LIQ_FRAC < ORDER_SIZE_MIN: w.append(f"MIN_LIQ_USD*SIZE_LIQ_FRAC = ${MIN_LIQ_USD*SIZE_LIQ_FRAC:.0f} < ORDER_SIZE_MIN")
    if "GATE" in ENABLED_CEXS and not (GATE_API_KEY and GATE_SECRET): w.append("Thieu GATE_API_KEY/SECRET: khong biet phi rut theo mang -> chieu GATE->DEX bi tat")
    if "BYBIT" in ENABLED_CEXS and not (BYBIT_API_KEY and BYBIT_SECRET): w.append("Thieu BYBIT_API_KEY/SECRET: Bybit se dung public, check nap/rut kem chinh xac")
    if "BINANCE" in ENABLED_CEXS and not (BINANCE_API_KEY and BINANCE_SECRET): w.append("Thieu BINANCE_API_KEY/SECRET: Binance se dung public")
    if THRESHOLD_NET < 0.5: w.append("THRESHOLD_NET < 0.5% rat mong so voi rui ro chuyen token")
    for x in w: log.warning("CONFIG: %s", x)
    return w

def main():
    log.info("BOT V10.0 MERGED start | chains=%s | CEX=%s", list(CHAINS), list(EXCHANGES))
    warns = validate_config()
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    threading.Thread(target=followup_loop, daemon=True, name="Followup").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HB").start()
    threading.Thread(target=telegram_poll_loop, daemon=True, name="TGPoll").start()
    send_telegram(f"🚀 <b>BOT V10.0 MERGED FINAL</b> (paper)\nChains: {', '.join(CHAINS)} | CEX: {', '.join(EXCHANGES)}\nNet sau buffer ≥ {THRESHOLD_NET}% & ≥ ${MIN_NET_USD:.0f} | Size ${ORDER_SIZE_MIN:.0f}-${ORDER_SIZE_MAX:.0f} dong | Taker-only, per-chain fee\nFilter: Liq≥${MIN_LIQ_USD:.0f} Vol≥${MIN_VOL_24H:.0f} Txns≥{MIN_TXNS_H1} Impact≤{MAX_IMPACT_PCT}% Gross≤{MAX_GROSS}%\nLenh: /help" + "".join("\n⚠ " + esc(x) for x in warns))
    workers = {}
    for k in CHAINS:
        w = Worker(k); w.start(); workers[k] = w; time.sleep(1.2)
    while True:
        time.sleep(300); cleanup()
        now = datetime.now()
        for k, w in workers.items():
            idle = (now - STATS[k].last_beat).total_seconds()
            if not w.thread.is_alive() or idle > STUCK_SEC: log.warning("[SUPERVISOR] %s -> restart", k); w.restart()

if __name__ == "__main__":
    main()
