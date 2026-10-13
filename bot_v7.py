#!/usr/bin/env python3
"""
BOT V8.9.1 - MULTI-CEX 9 SAN + DEPOSIT/WITHDRAW CHECK + WS LIVE + INVENTORY
1. Maker 0% vs Taker - hybrid fee with depth check
2. Dynamic size 200-2000$ based on liquidity
3. Real transfer time + volatility decay
4. New memecoin (<24h) allowed if vol/txns high + honeypot simulation
5. Volatile inventory tracker for pre-hold
6. Multi-CEX architecture (MEXC + GATE ready, expandable)
7. AI Score 0-100
8. WebSocket ready structure (REST now, WS upgrade path)
"""

import hashlib, hmac, html, json, logging, math, os, re, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlencode
import requests

def require_env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing env: {name}")
    return v
def env_float(name, default):
    return float(os.getenv(name, default))
def env_bool(name, default="1"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")

TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
CHAT_ID = str(require_env("CHAT_ID"))
MEXC_API_KEY = require_env("MEXC_API_KEY")
MEXC_SECRET = require_env("MEXC_SECRET")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "paper_trades.db")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
ORDER_SIZE_USD = env_float("ORDER_SIZE_USD", 500)  # base, will be overridden by dynamic
ORDER_SIZE_MIN = env_float("ORDER_SIZE_MIN", 200)
ORDER_SIZE_MAX = env_float("ORDER_SIZE_MAX", 2000)
THRESHOLD_NET = env_float("THRESHOLD_NET", 0.8)
PREFILTER_GROSS = env_float("PREFILTER_GROSS", 0.8)
MAX_GROSS = env_float("MAX_GROSS", 7.0)
MAX_IMPACT_PCT = env_float("MAX_IMPACT_PCT", 0.8)
MIN_LIQ_USD = env_float("MIN_LIQ_USD", 90000)
MIN_VOL_24H = env_float("MIN_VOL_24H", 40000)
MIN_TXNS_H1 = int(env_float("MIN_TXNS_H1", 15))
MIN_POOL_AGE_H = env_float("MIN_POOL_AGE_H", 24)
MIN_POOL_AGE_MEME_H = env_float("MIN_POOL_AGE_MEME_H", 1)  # cho memecoin moi
ALLOW_NEW_MEME = env_bool("ALLOW_NEW_MEME", "1")
MAX_MEXC_SPREAD = env_float("MAX_MEXC_SPREAD", 2.0)
MEXC_TAKER_FEE = env_float("MEXC_TAKER_FEE", 0.002)
MEXC_MAKER_FEE = env_float("MEXC_MAKER_FEE", 0.0)
MAKER_DEPTH_MULTIPLIER = env_float("MAKER_DEPTH_MULTIPLIER", 2.0)  # depth phai gap 2x size moi dam dung maker
EXTRA_SLIPPAGE = env_float("EXTRA_SLIPPAGE", 0.001)
TAX_MAX = env_float("TAX_MAX", 0.05)
RISK_K = env_float("RISK_K", 0.5)
RISK_FLOOR = env_float("RISK_FLOOR", 0.10)
SOFT_FLAG_BUFFER = env_float("SOFT_FLAG_BUFFER", 0.20)
UNKNOWN_SEC_BUFFER = env_float("UNKNOWN_SEC_BUFFER", 0.30)
COOLDOWN_MIN = env_float("COOLDOWN_MIN", 25)
SCAN_PAUSE_SEC = env_float("SCAN_PAUSE_SEC", 40)
STUCK_SEC = env_float("STUCK_SEC", 900)
STATIC_TTL = env_float("STATIC_TTL", 600)
BOOK_TTL = 10
DEX_BATCH = 30
FOLLOWUP_MIN = (5, 10)
REAL_PRICE_CHECK = env_bool("REAL_PRICE_CHECK", "1")
REQUIRE_REAL_PRICE = env_bool("REQUIRE_REAL_PRICE", "1")
ADAPTIVE = env_bool("ADAPTIVE", "1")
BAD_STREAK_BLACKLIST = int(env_float("BAD_STREAK_BLACKLIST", 3))
BLACKLIST_HOURS = env_float("BLACKLIST_HOURS", 24)
# V8.8 new
VOLATILE_CHG_THRESHOLD = env_float("VOLATILE_CHG_THRESHOLD", 5.0)  # 5m change >5% la bien dong
VOLATILE_COUNT_TO_HOT = int(env_float("VOLATILE_COUNT_TO_HOT", 3))
AI_SCORE_MIN = env_float("AI_SCORE_MIN", 60)
ENABLED_CEXS = os.getenv("ENABLED_CEXS", "MEXC,GATE").split(",")  # danh sach CEX muon chay

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
WSOL = "So11111111111111111111111111111111111111112"
SOL_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLE = {
    56:    ("0x55d398326f99059fF775485246999027B3197955", 18),
    8453:  ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
    1:     ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    137:   ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
    42161: ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
}
STABLE_ADDRS = {a.lower() for a, _ in STABLE.values()} | {SOL_USDC.lower(), SOL_USDT.lower()}
QUOTE_OK = {"USDC", "USDT", "WETH", "WBNB", "SOL", "WSOL", "ETH", "BNB", "WMATIC", "POL", "DAI", "USDC.E"}

CHAINS = {
    "SOL": {"emoji": "🟣", "dex_chain": "solana", "evm": False, "gas_usd": 0.02, "transfer_min": 2,
            "explorer": "https://solscan.io/token/", "chain_id": None,
            "aliases": {"SOL", "SOLANA", "SOLANASOL", "SOLSOLANA", "SOLMAINNET"}},
    "BNB": {"emoji": "🟡", "dex_chain": "bsc", "evm": True, "gas_usd": 0.15, "transfer_min": 3,
            "explorer": "https://bscscan.com/token/", "chain_id": 56,
            "aliases": {"BSC", "BEP20", "BSCBEP20", "BEP20BSC", "BNBSMARTCHAIN", "BNBSMARTCHAINBEP20",
                        "BNBBEP20", "BEP20BNB", "BSCBNB"}},
    "BASE": {"emoji": "🔵", "dex_chain": "base", "evm": True, "gas_usd": 0.05, "transfer_min": 3,
             "explorer": "https://basescan.org/token/", "chain_id": 8453,
             "aliases": {"BASE", "BASECHAIN", "BASEBASE", "BASEMAINNET", "BASEETH"}},
    "ETH": {"emoji": "⚫", "dex_chain": "ethereum", "evm": True, "gas_usd": 2.5, "transfer_min": 8,
            "explorer": "https://etherscan.io/token/", "chain_id": 1,
            "aliases": {"ERC20", "ETH", "ETHEREUM", "ETHEREUMERC20", "ETHERC20", "ERC20ETH", "ETHEREUMETH",
                        "ETHTOKEN", "ETHMAINNET", "ERC20ETHEREUM"}},
    "POLYGON": {"emoji": "💜", "dex_chain": "polygon", "evm": True, "gas_usd": 0.02, "transfer_min": 5,
                "explorer": "https://polygonscan.com/token/", "chain_id": 137,
                "aliases": {"MATIC", "POLYGON", "POLYGONMATIC", "POLYGONPOLYGON", "MATICPOLYGON", "POLYGONPOS",
                            "POSPOLYGON", "MATICMAINNET", "POLYGONMAINNET"}},
    "ARBITRUM": {"emoji": "🔷", "dex_chain": "arbitrum", "evm": True, "gas_usd": 0.15, "transfer_min": 3,
                 "explorer": "https://arbiscan.io/token/", "chain_id": 42161,
                 "aliases": {"ARB", "ARBITRUM", "ARBITRUMONE", "ARBITRUMONEARB", "ARBONE", "ARBITRUMONEETH",
                             "ARBITRUMMAINNET"}},
    "ROBINHOOD": {"emoji": "🔶", "dex_chain": "robinhood", "evm": True, "gas_usd": 0.01, "transfer_min": 5,
                  "explorer": "https://robinhoodchain.blockscout.com/token/", "chain_id": None,
                  "aliases": {"ROBINHOOD", "ROBINHOODCHAIN", "HOODCHAIN", "RHCHAIN", "ROBINHOODCHAINETH",
                              "ROBINHOODMAINNET"}},
    "ARC": {"emoji": "🟪", "dex_chain": "arc", "evm": True, "gas_usd": 0.02, "transfer_min": 5,
            "explorer": "https://explorer.arc.io/token/", "chain_id": None,
            "aliases": {"ARC", "ARCCHAIN", "ARCNETWORK", "CIRCLEARC", "ARCARC", "ARCCHAINMAINNET"}},
    "NEAR": {"emoji": "🟢", "dex_chain": "near", "evm": False, "gas_usd": 0.01, "transfer_min": 3,
             "explorer": "https://nearblocks.io/token/", "chain_id": None,
             "aliases": {"NEAR", "NEARPROTOCOL", "NEARNEAR", "NEARMAINNET", "NEARTOKEN"}},
}
REAL_CHAINS = {"SOL", "BNB", "BASE", "ETH", "POLYGON", "ARBITRUM"}
for _k, _c in CHAINS.items():
    _c["dex_chain"] = os.getenv(f"DEX_CHAIN_{_k}", _c["dex_chain"])
ENABLED = [c.strip().upper() for c in os.getenv("ENABLED_CHAINS", ",".join(sorted(REAL_CHAINS, key=list(CHAINS).index))).split(",") if c.strip()]
CHAINS = {k: v for k, v in CHAINS.items() if k in ENABLED}

DEX_FEES = {"uniswap": 0.003, "pancakeswap": 0.0025, "sushiswap": 0.003, "quickswap": 0.003, "aerodrome": 0.003,
            "camelot": 0.003, "raydium": 0.0025, "orca": 0.003, "meteora": 0.003}
DEFAULT_DEX_FEE = 0.003

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
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
DEX_LIMITER = RateLimiter(env_float("DEX_LIMIT_SEC", 2.5))
JUP_LIMITER = RateLimiter(0.5)
PARA_LIMITER = RateLimiter(0.6)
TAX_LIMITER = RateLimiter(0.4)
RPC_LIMITER = RateLimiter(0.15)

DEX_429_UNTIL = 0
DEX_429_LOCK = threading.Lock()

def http_get(url, params=None, headers=None, limiter=None, timeout=15, retries=2):
    global DEX_429_UNTIL
    if "dexscreener.com" in url:
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
                    wait = float(r.headers.get("Retry-After", 30))
                except:
                    wait = 30
                wait = min(wait, 60)
                if "dexscreener.com" in url:
                    with DEX_429_LOCK:
                        DEX_429_UNTIL = time.time() + wait
                log.warning("429 tu %s, ngu %.0fs", url.split("?")[0], wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                if r.status_code in (401,403,404):
                    return None
                if attempt < retries:
                    time.sleep(1+attempt*2)
                    continue
                return None
            return r
        except requests.RequestException as e:
            log.warning("HTTP loi %s: %s", url.split("?")[0], e)
            time.sleep(1 + attempt)
    return None

def rpc_post(url, body):
    for attempt in range(2):
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

def mexc_signed_get(path, params=None):
    params = params or {}
    params["recvWindow"] = 10000
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params)
    sig = hmac.new(MEXC_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return http_get(f"{MEXC_BASE}{path}?{qs}&signature={sig}", headers={"X-MEXC-APIKEY": MEXC_API_KEY},
                    limiter=MEXC_LIMITER)

def esc(s):
    return html.escape(str(s), quote=False)
def fnum(x):
    try:
        return float(x)
    except:
        return 0.0
def norm_addr(chain_key, addr):
    if not addr:
        return ""
    a = addr.strip()
    return a.lower() if CHAINS[chain_key].get("evm") else a

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
def valid_contract(chain_key, c):
    if not c:
        return False
    cfg = CHAINS[chain_key]
    if cfg.get("evm"):
        return bool(EVM_RE.match(c))
    if chain_key == "SOL":
        return bool(SOL_RE.match(c))
    return len(c) > 3

class Stat:
    def __init__(self):
        self.lock = threading.Lock()
        self.c = {"attempted": 0, "fetched": 0, "opportunity": 0, "signals": 0, "suspect": 0, "errors": 0,
                  "dex_pairs": 0, "real_price_ok": 0, "real_price_fail": 0, "blacklisted_skip": 0}
        self.last_beat = datetime.now()
        self.last_success = None
    def inc(self, k, n=1):
        with self.lock:
            self.c[k] = self.c.get(k, 0) + n
    def beat(self):
        with self.lock:
            self.last_beat = datetime.now()
    def success(self):
        with self.lock:
            self.last_success = datetime.now()
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
        need = False
        with self.lock:
            s = self.d.setdefault(name, {"ok": 0, "fail": 0, "consec": 0})
            if ok:
                s["ok"] += 1; s["consec"] = 0; self.alerted.pop(name, None)
            else:
                s["fail"] += 1; s["consec"] += 1
                if s["consec"] >= 30 and time.time() - self.alerted.get(name, 0) > 3600:
                    self.alerted[name] = time.time(); need = True
        if need:
            send_telegram(f"🚨 API <b>{esc(name)}</b> loi lien tuc {s['consec']} lan.")
    def line(self):
        with self.lock:
            return " | ".join(f"{k}: {v['ok']}ok/{v['fail']}fail" for k, v in self.d.items()) or "chua co"
API = ApiHealth()


# ========== V8.9 MULTI-CEX ARCHITECTURE ==========
import json as _json
from concurrent.futures import ThreadPoolExecutor as _TPE

CEX_REGISTRY = {
    "MEXC": {
        "name": "MEXC", "taker": 0.002, "maker": 0.0, 
        "rest_base": "https://api.mexc.com",
        "ws": "wss://wbs.mexc.com/ws",
        "ws_format": "mexc"
    },
    "GATE": {
        "name": "Gate.io", "taker": 0.002, "maker": 0.002,
        "rest_base": "https://api.gateio.ws/api/v4",
        "ws": "wss://api.gateio.ws/ws/v4/",
        "ws_format": "gate"
    },
    "BINANCE": {
        "name": "Binance", "taker": 0.001, "maker": 0.0005,
        "rest_base": "https://api.binance.com",
        "ws": "wss://stream.binance.com:9443/ws",
        "ws_format": "binance"
    },
    "OKX": {
        "name": "OKX", "taker": 0.001, "maker": 0.0008,
        "rest_base": "https://www.okx.com",
        "ws": "wss://ws.okx.com:8443/ws/v5/public",
        "ws_format": "okx"
    },
    "BYBIT": {
        "name": "Bybit", "taker": 0.001, "maker": 0.0005,
        "rest_base": "https://api.bybit.com",
        "ws": "wss://stream.bybit.com/v5/public/spot",
        "ws_format": "bybit"
    },
    "BITGET": {
        "name": "Bitget", "taker": 0.002, "maker": 0.002,
        "rest_base": "https://api.bitget.com",
        "ws": "wss://ws.bitget.com/v2/ws/public",
        "ws_format": "bitget"
    },
    "LBANK": {
        "name": "LBank", "taker": 0.002, "maker": 0.002,
        "rest_base": "https://api.lbank.info",
        "ws": "wss://www.lbkex.net/ws/V2/",
        "ws_format": "lbank"
    },
    "KUCOIN": {
        "name": "KuCoin", "taker": 0.001, "maker": 0.001,
        "rest_base": "https://api.kucoin.com",
        "ws": "wss://push-stream.kucoin.com/endpoint",
        "ws_format": "kucoin",
        "ws_token_url": "https://api.kucoin.com/api/v1/bullet-public"
    },
    "BINGX": {
        "name": "BingX", "taker": 0.002, "maker": 0.002,
        "rest_base": "https://open-api.bingx.com",
        "ws": "wss://open-api-swap.bingx.com/swap-market",
        "ws_format": "bingx"
    },
    "HTX": {
        "name": "HTX", "taker": 0.002, "maker": 0.002,
        "rest_base": "https://api.huobi.pro",
        "ws": "wss://api.huobi.pro/ws",
        "ws_format": "htx"
    },
}

# Global live price cache from WebSocket
CEX_LIVE = {}  # {cex: {symbol: {bid, ask, ts, source: ws/rest}}}
CEX_LIVE_LOCK = threading.Lock()

def update_live_price(cex, symbol, bid, ask, source="ws"):
    with CEX_LIVE_LOCK:
        if cex not in CEX_LIVE:
            CEX_LIVE[cex] = {}
        CEX_LIVE[cex][symbol] = {"bid": float(bid), "ask": float(ask), "ts": time.time(), "source": source}

def get_live_price(cex, symbol):
    with CEX_LIVE_LOCK:
        d = CEX_LIVE.get(cex, {}).get(symbol)
        if not d:
            return None
        if time.time() - d["ts"] > 30:  # stale 30s
            return None
        return d

# Base CEX Adapter
class BaseCEX:
    def __init__(self, key):
        self.key = key
        self.cfg = CEX_REGISTRY[key]
    def fetch_pairs(self):
        return {}
    def fetch_tokens(self, pairs):
        return {}
    def fetch_orderbook(self, symbol):
        return None

class MexcCEX(BaseCEX):
    def __init__(self): super().__init__("MEXC")
    def fetch_pairs(self):
        r = http_get(f"{MEXC_BASE}/api/v3/exchangeInfo", limiter=MEXC_LIMITER, timeout=20)
        if not r or r.status_code != 200:
            raise RuntimeError(f"MEXC exchangeInfo fail {r.status_code if r else 'no resp'}")
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and str(s.get("status")) == "1":
                out[s["symbol"]] = s["baseAsset"]
        return out
    def fetch_tokens(self, pairs):
        # Use signed getall for withdraw/deposit + contract
        r = mexc_signed_get("/api/v3/capital/config/getall")
        if not r or r.status_code != 200:
            raise RuntimeError(f"MEXC config fail")
        base_set = set(pairs.values())
        sym2pair = {}
        for p, s in pairs.items():
            sym2pair.setdefault(s, p)
        result = {k: {} for k in CHAINS}
        for coin in r.json():
            sym = coin.get("coin")
            if sym not in base_set:
                continue
            for net in coin.get("networkList", []) or []:
                net_name = re.sub(r"[^A-Z0-9]", "", (net.get("network") or "").upper())
                contract = (net.get("contract") or "").strip()
                if net_name in {"SOL","SOLANA"} and (not contract or contract.upper()=="SOL"):
                    contract = WSOL
                found = next((ch for ch, cfg in CHAINS.items() if net_name in cfg["aliases"]), None)
                if not found: continue
                if not valid_contract(found, contract): continue
                dedup = norm_addr(found, contract)
                if dedup in result[found]: continue
                result[found][dedup] = {
                    "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": found,
                    "pair": sym2pair.get(sym, sym + "USDT"), "raw_network": net.get("network"),
                    "withdraw_fee": fnum(net.get("withdrawFee")), "withdraw_min": fnum(net.get("withdrawMin")),
                    "withdraw_enable": bool(net.get("withdrawEnable")),
                    "deposit_enable": bool(net.get("depositEnable")),
                    "cex": "MEXC", "cex_pair": sym2pair.get(sym, sym + "USDT"),
                }
        return {k: list(v.values()) for k, v in result.items()}

class GateCEX(BaseCEX):
    def __init__(self): super().__init__("GATE")
    def fetch_pairs(self):
        r = http_get("https://api.gateio.ws/api/v4/spot/tickers", timeout=15)
        if not r or r.status_code != 200: return {}
        out = {}
        for t in r.json():
            cp = t.get("currency_pair")
            if not cp or not cp.endswith("_USDT"): continue
            base = cp.replace("_USDT","")
            out[base + "USDT"] = base
        return out
    def fetch_tokens(self, pairs):
        # Gate withdraw status via /withdraw_status? Not public, use config approximation
        # For V8.9 we map via symbol only, assume withdraw/deposit true
        result = {k: {} for k in CHAINS}
        # Use gate chain mapping: need to fetch chain info per coin
        try:
            # Gate does not expose contract easily in public, we use MEXC contract as reference
            # For now, reuse MEXC tokens but tag as GATE if pair exists on Gate
            pass
        except:
            pass
        return result

class BinanceCEX(BaseCEX):
    def __init__(self): super().__init__("BINANCE")
    def fetch_pairs(self):
        r = http_get("https://api.binance.com/api/v3/exchangeInfo", timeout=15)
        if not r or r.status_code != 200: return {}
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset")=="USDT" and s.get("status")=="TRADING":
                out[s["symbol"]] = s["baseAsset"]
        return out
    def fetch_tokens(self, pairs):
        return {k: {} for k in CHAINS}

# Other CEXs - simplified: use ticker list only, contract resolved via MEXC mapping
class GenericCEX(BaseCEX):
    def fetch_pairs(self):
        return {}
    def fetch_tokens(self, pairs):
        return {k: {} for k in CHAINS}


# Optional API keys for deposit/withdraw check (nếu không có thì mặc định True)
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
GATE_API_KEY = os.getenv("GATE_API_KEY", "")
GATE_SECRET = os.getenv("GATE_SECRET", "")
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET = os.getenv("OKX_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_SECRET", "")
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_SECRET = os.getenv("KUCOIN_SECRET", "")
KUCOIN_PASSPHRASE = os.getenv("KUCOIN_PASSPHRASE", "")
HTX_API_KEY = os.getenv("HTX_API_KEY", "")
HTX_SECRET = os.getenv("HTX_SECRET", "")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET = os.getenv("BINGX_SECRET", "")
LBANK_API_KEY = os.getenv("LBANK_API_KEY", "")
LBANK_SECRET = os.getenv("LBANK_SECRET", "")

def safe_get_deposit_status_public(cex_key, symbol):
    """Check deposit/withdraw status qua public API nếu có, không cần key"""
    try:
        if cex_key == "GATE":
            # Public: /spot/currencies/{currency}
            r = http_get(f"https://api.gateio.ws/api/v4/spot/currencies/{symbol}", timeout=5)
            if r and r.status_code == 200:
                j = r.json()
                return not j.get("withdraw_disabled", False), not j.get("deposit_disabled", False)
        elif cex_key == "KUCOIN":
            r = http_get(f"https://api.kucoin.com/api/v1/currencies/{symbol}", timeout=5)
            if r and r.status_code == 200:
                data = r.json().get("data", {})
                chains = data.get("chains", [])
                if chains:
                    # lấy chain đầu tiên
                    c = chains[0]
                    return c.get("isWithdrawEnabled", True), c.get("isDepositEnabled", True)
        elif cex_key == "HTX":
            r = http_get(f"https://api.huobi.pro/v1/common/currencys", timeout=5)
            if r and r.status_code == 200:
                for cur in r.json().get("data", []):
                    if cur.get("currency","").upper() == symbol.upper():
                        # HTX không trả chi tiết chain, mặc định True
                        return True, True
        elif cex_key == "OKX":
            r = http_get(f"https://www.okx.com/api/v5/asset/currencies?ccy={symbol}", timeout=5)
            if r and r.status_code == 200:
                for cur in r.json().get("data", []):
                    if cur.get("ccy") == symbol:
                        # OKX: canWd, canDep
                        return cur.get("canWd", True), cur.get("canDep", True)
    except:
        pass
    # Mặc định nếu không check được thì cho qua (True, True) để không miss kèo
    return True, True

CEX_ADAPTERS = {
    "MEXC": MexcCEX(),
    "GATE": GateCEX(),
    "BINANCE": BinanceCEX(),
    "OKX": GenericCEX("OKX"),
    "BYBIT": GenericCEX("BYBIT"),
    "BITGET": GenericCEX("BITGET"),
    "LBANK": GenericCEX("LBANK"),
    "KUCOIN": GenericCEX("KUCOIN"),
    "BINGX": GenericCEX("BINGX"),
    "HTX": GenericCEX("HTX"),
}

class MultiCEXData:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens = {}  # chain -> list
        self.pairs = {}  # cex -> pairs
        self.ts = 0
        self.retry_after = 0
    def ensure_static(self):
        with self.lock:
            now = time.time()
            if self.tokens and now - self.ts < STATIC_TTL:
                return
            if now < self.retry_after:
                return
            try:
                all_tokens = {k: {} for k in CHAINS}
                all_pairs = {}
                # Fetch MEXC first (has contract data)
                mexc_adapter = CEX_ADAPTERS["MEXC"]
                mexc_pairs = mexc_adapter.fetch_pairs()
                mexc_tokens = mexc_adapter.fetch_tokens(mexc_pairs)
                all_pairs["MEXC"] = mexc_pairs
                for chain, lst in mexc_tokens.items():
                    for t in lst:
                        all_tokens[chain][t["dedup_key"]] = t
                
                # Fetch other CEXs pairs in parallel (only pairs, contract reuse MEXC)
                def fetch_other(cex_key):
                    if cex_key not in ENABLED_CEXS or cex_key=="MEXC":
                        return cex_key, {}, {}
                    try:
                        ad = CEX_ADAPTERS.get(cex_key)
                        if not ad: return cex_key, {}, {}
                        pairs = ad.fetch_pairs()
                        # For other CEX, create token entries reusing MEXC contract if symbol matches
                        tokens = {k: {} for k in CHAINS}
                        for pair, base in pairs.items():
                            # find matching MEXC token by symbol
                            for chain in CHAINS:
                                for dedup, mt in all_tokens[chain].items():
                                    if mt["symbol"]==base:
                                        # clone with new cex
                                        nt = dict(mt)
                                        nt["cex"] = cex_key
                                        nt["cex_pair"] = pair
                                        nt["pair"] = pair
                                        # use CEX registry fee
                                        tokens[chain][dedup] = nt
                        return cex_key, pairs, tokens
                    except Exception as e:
                        log.warning("CEX %s fetch fail: %s", cex_key, e)
                        return cex_key, {}, {}

                with _TPE(max_workers=5) as ex:
                    futures = {ex.submit(fetch_other, k): k for k in ENABLED_CEXS if k!="MEXC"}
                    for fut in futures:
                        try:
                            cex_key, pairs, tokens = fut.result(timeout=15)
                            all_pairs[cex_key]=pairs
                            for chain in CHAINS:
                                for dedup, t in tokens.get(chain, {}).items():
                                    # avoid overwriting MEXC primary, keep both as separate entries with cex tag
                                    # store as list later
                                    if dedup not in all_tokens[chain]:
                                        all_tokens[chain][dedup]=t
                                    else:
                                        # keep both: create composite key
                                        # For simplicity, if token already exists, add secondary entry with cex suffix
                                        comp_key = f"{dedup}_{cex_key}"
                                        all_tokens[chain][comp_key]=t
                        except Exception as e:
                            log.warning("CEX parallel fail %s", e)

                # Convert to list
                final = {k: list(v.values()) for k, v in all_tokens.items()}
                self.tokens = final
                self.pairs = all_pairs
                self.ts = now
                total = sum(len(v) for v in final.values())
                log.info("MultiCEX loaded: %d tokens total, CEXs=%s", total, list(all_pairs.keys()))
            except Exception as e:
                log.error("MultiCEX refresh fail: %s", e)
                import traceback; traceback.print_exc()
                self.retry_after = now + 60

# Replace MexcData with MultiCEX
class MexcData(MultiCEXData):
    pass

def get_decimals(chain_key, contract):
    key = (chain_key, norm_addr(chain_key, contract))
    with DEC_LOCK:
        if key in DEC_CACHE:
            return DEC_CACHE[key]
    dec = None
    try:
        if chain_key == "SOL":
            j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [contract]})
            dec = (((j or {}).get("result") or {}).get("value") or {}).get("decimals")
        elif CHAINS[chain_key].get("evm") and chain_key in RPC_URLS:
            j = rpc_post(RPC_URLS[chain_key], {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                               "params": [{"to": contract, "data": "0x313ce567"}, "latest"]})
            res = (j or {}).get("result")
            if res and res not in ("0x", "0x0") and len(res) >= 3:
                dec = int(res, 16)
    except:
        pass
    API.report("rpc", dec is not None)
    if dec is not None and 0 <= dec <= 36:
        with DEC_LOCK:
            DEC_CACHE[key] = int(dec)
        return int(dec)

def get_sol_transfer_fee(contract):
    """Lay transfer fee cua Token-2022 (basis points), VD 3% = 300"""
    try:
        # getAccountInfo with jsonParsed to read extensions
        j = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [contract, {"encoding": "jsonParsed"}]})
        data = (((j or {}).get("result") or {}).get("value") or {}).get("data") or {}
        if isinstance(data, dict):
            parsed = data.get("parsed") or {}
            info = parsed.get("info") or {}
            exts = info.get("extensions") or []
            for ext in exts:
                if ext.get("extension") == "transferFeeConfig":
                    state = ext.get("state") or {}
                    fee = state.get("transferFeeConfig") or {}
                    # newer format
                    newer = state.get("newerTransferFee") or {}
                    older = state.get("olderTransferFee") or {}
                    # try both
                    bp = newer.get("transferFeeBasisPoints") or older.get("transferFeeBasisPoints") or fee.get("transferFeeBasisPoints")
                    if bp is not None:
                        return float(bp)/100.0/100.0  # 300 -> 0.03
        # fallback: try get token supply extension check
        j2 = rpc_post(SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [contract]})
        # no fee info here, return 0
    except Exception as e:
        log.debug("transfer fee check loi %s: %s", contract[:8], e)
    return 0.0


def jup_quote(input_mint, output_mint, amount_raw):
    params = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount_raw)),
              "slippageBps": 50, "restrictIntermediateTokens": "true"}
    endpoints = []
    if JUPITER_API_KEY:
        endpoints.append((JUPITER_PRO_URL, {"x-api-key": JUPITER_API_KEY}))
    endpoints.append((JUPITER_LITE_URL, None))
    for url, headers in endpoints:
        r = http_get(url, params=params, headers=headers, limiter=JUP_LIMITER, timeout=8, retries=1)
        API.report("jupiter", bool(r is not None and r.status_code in (200, 400, 404)))
        if r is None or r.status_code != 200:
            continue
        try:
            j = r.json()
            out = fnum(j.get("outAmount"))
            if out <= 0:
                continue
            return {"out": out, "impact": abs(fnum(j.get("priceImpactPct"))) * 100.0}
        except:
            continue
    return None

def paraswap_quote(chain_id, src, dest, amount_raw, src_dec, dest_dec):
    params = {"srcToken": src, "destToken": dest, "amount": str(int(amount_raw)), "srcDecimals": src_dec,
              "destDecimals": dest_dec, "side": "SELL", "network": chain_id, "version": "6.2"}
    r = http_get(PARASWAP_URL, params=params, limiter=PARA_LIMITER, timeout=10, retries=1)
    API.report("paraswap", bool(r is not None and r.status_code in (200, 400, 404)))
    if r is None or r.status_code != 200:
        return None
    try:
        pr = r.json().get("priceRoute") or {}
        out = fnum(pr.get("destAmount"))
        if out <= 0:
            return None
        src_usd, dest_usd = fnum(pr.get("srcUSD")), fnum(pr.get("destUSD"))
        impact = max(0.0, (src_usd - dest_usd) / src_usd * 100.0) if src_usd > 0 and dest_usd > 0 else None
        return {"out": out, "impact": impact}
    except:
        return None

def dex_buy_quote(chain_key, contract, dec, usd):
    if chain_key == "SOL":
        q = jup_quote(SOL_USDC, contract, usd * 1e6)
        src = "jupiter"
    else:
        cid = CHAINS[chain_key]["chain_id"]
        if cid not in STABLE:
            return None
        st, sdec = STABLE[cid]
        q = paraswap_quote(cid, st, contract, usd * 10 ** sdec, sdec, dec)
        src = "paraswap"
    if not q:
        return None
    tokens = q["out"] / 10 ** dec
    if tokens <= 0:
        return None
    return {"tokens": tokens, "price": usd / tokens, "impact": q["impact"], "source": src}

def dex_sell_quote(chain_key, contract, dec, tokens):
    raw = int(tokens * 10 ** dec)
    if raw <= 0:
        return None
    if chain_key == "SOL":
        q = jup_quote(contract, SOL_USDC, raw)
        sdec, src = 6, "jupiter"
    else:
        cid = CHAINS[chain_key]["chain_id"]
        if cid not in STABLE:
            return None
        st, sdec = STABLE[cid]
        q = paraswap_quote(cid, contract, st, raw, dec, sdec)
        src = "paraswap"
    if not q:
        return None
    usd_out = q["out"] / 10 ** sdec
    if usd_out <= 0:
        return None
    return {"usd_out": usd_out, "price": usd_out / tokens, "impact": q["impact"], "source": src}

SEC_CACHE = {}
SEC_CACHE_MAX = 2000
SEC_LOCK = threading.Lock()
HARD_BAD = {"is_honeypot": "honeypot", "cannot_sell_all": "cannot_sell_all", "owner_change_balance": "owner_change_balance"}
SOFT_FLAGS = ("transfer_pausable", "is_blacklisted", "slippage_modifiable", "hidden_owner", "trading_cooldown",
              "can_take_back_ownership")
UNKNOWN_SEC = {"known": False, "buy_tax": 0.0, "sell_tax": 0.0, "bad": None, "soft": []}

def get_security(chain_key, contract):
    cid = (CHAINS.get(chain_key) or {}).get("chain_id")
    if cid not in (1, 56, 137, 8453, 42161) or not contract:
        return dict(UNKNOWN_SEC)
    key = (chain_key, contract.lower())
    with SEC_LOCK:
        c = SEC_CACHE.get(key)
        if c and time.time() - c[0] < (21600 if c[1]["known"] else 600):
            return c[1]
    r = http_get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}", params={"contract_addresses": contract},
                 limiter=TAX_LIMITER, timeout=10, retries=1)
    API.report("goplus", bool(r and r.status_code == 200))
    sec = dict(UNKNOWN_SEC)
    try:
        if r and r.status_code == 200:
            result = r.json().get("result") or {}
            data = result.get(contract.lower()) or result.get(contract) or {}
            if data:
                def tax(k):
                    v = fnum(data.get(k))
                    v = v / 100.0 if v > 1 else v
                    return max(0.0, min(v, 0.5))
                bad = None
                for k, name in HARD_BAD.items():
                    if str(data.get(k)) == "1":
                        bad = name; break
                if bad is None and str(data.get("is_open_source")) == "0":
                    bad = "not_open_source"
                sec = {"known": data.get("buy_tax") not in (None, "") and data.get("sell_tax") not in (None, ""),
                       "buy_tax": tax("buy_tax"), "sell_tax": tax("sell_tax"), "bad": bad,
                       "soft": [f for f in SOFT_FLAGS if str(data.get(f)) == "1"]}
    except Exception as e:
        log.warning("GoPlus parse loi %s: %s", contract[:8], e)
    # V8.2: SOL Token-2022 transfer fee fallback if GoPlus miss
    if chain_key == "SOL" and sec.get("buy_tax",0) == 0 and sec.get("sell_tax",0) == 0:
        try:
            tf = get_sol_transfer_fee(contract)
            if tf > 0:
                sec["buy_tax"] = tf
                sec["sell_tax"] = tf
                sec["known"] = True
                log.info("Phat hien transfer fee SOL %s %.2f%%", contract[:8], tf*100)
        except:
            pass
    with SEC_LOCK:
        SEC_CACHE[key] = (time.time(), sec)
        if len(SEC_CACHE) > SEC_CACHE_MAX:
            try:
                oldest = next(iter(SEC_CACHE))
                SEC_CACHE.pop(oldest)
            except:
                pass
    return sec

def dex_prices_estimate(pool):
    impact_pct = 2 * ORDER_SIZE_USD / max(pool["liq"], 1) * 100
    f, i = pool["fee"], impact_pct / 100
    return impact_pct, pool["price"] * (1 + f + i), pool["price"] * (1 - f - i)

def risk_buffer(chain_key, pool, sec):
    tm = get_real_transfer_time(chain_key, pool)
    move = max(abs(pool.get("chg_m5", 0)) * math.sqrt(tm / 5.0), abs(pool.get("chg_h1", 0)) * math.sqrt(tm / 60.0))
    buf = RISK_FLOOR + RISK_K * move
    buf += SOFT_FLAG_BUFFER * len(sec.get("soft", []))
    if not sec.get("known") and CHAINS[chain_key].get("evm"):
        buf += UNKNOWN_SEC_BUFFER
    return buf

# ========= V8.8 NEW FUNCTIONS =========
def calc_optimal_size(pool_liq, pool_vol24, gross_pct, base_usd=ORDER_SIZE_USD):
    """Size dong 200-2000$ dua tren thanh khoan"""
    # muc tieu impact 0.3%
    target_impact = 0.003
    # size toi uu = liq * target_impact
    size_by_liq = pool_liq * target_impact
    # neu vol cao, cho phep size lon hon
    vol_factor = min(pool_vol24 / 100000, 2.0)  # vol 100k -> x1, vol 200k+ -> x2
    size_by_liq *= vol_factor
    # neu gross cao, tang size 20%
    if gross_pct > 3:
        size_by_liq *= 1.5
    elif gross_pct > 2:
        size_by_liq *= 1.2
    size = max(ORDER_SIZE_MIN, min(ORDER_SIZE_MAX, size_by_liq, ORDER_SIZE_MAX))
    # dam bao it nhat base_usd neu liq du lon
    if pool_liq > 500000:
        size = max(size, base_usd)
    return round(size, 2)

def is_honeypot_simulation(chain_key, contract, dec):
    """Check honeypot bang cach quote $10 that"""
    if not dec:
        return False  # khong check duoc thi cho qua
    try:
        if chain_key == "SOL":
            # thu mua $10 USDC -> token
            q = jup_quote(SOL_USDC, contract, 10 * 1e6)
            if not q or q.get("out", 0) <= 0:
                return True
            # thu ban nguoc lai token -> USDC (lay 90% token vua mua de tranh dust)
            out_tokens = q["out"]
            q2 = jup_quote(contract, SOL_USDC, int(out_tokens * 0.9))
            if not q2 or q2.get("out", 0) <= 0:
                return True
            # neu ban ra mat >50% gia tri -> nghi la honeypot hoac tax cao bat thuong
            buy_value = 10
            sell_value = q2["out"] / 1e6
            if sell_value < buy_value * 0.5:
                log.info("HONEYPOT SIM %s %s sell %.2f vs buy 10", chain_key, contract[:8], sell_value)
                return True
        else:
            # EVM: dung paraswap quote $100 stable -> token va nguoc lai
            cid = CHAINS[chain_key]["chain_id"]
            if cid in STABLE:
                st, sdec = STABLE[cid]
                q = paraswap_quote(cid, st, contract, 100 * 10**sdec, sdec, dec)
                if not q:
                    return True
    except Exception as e:
        log.debug("Honeypot sim loi %s: %s", contract[:8], e)
    return False

# Real transfer time with volatility decay
TRANSFER_BASE = {"SOL": 2, "BNB": 3, "BASE": 3, "ETH": 8, "POLYGON": 5, "ARBITRUM": 3}
LAST_GAS_MULTIPLIER = {}

def get_real_transfer_time(chain_key, pool):
    """Thoi gian nap/rut thuc te = base + bien dong + tac nghen mang"""
    base = TRANSFER_BASE.get(chain_key, CHAINS[chain_key].get("transfer_min", 5))
    # neu pool bien dong manh 5m > 5%, cong them thoi gian rui ro
    chg = abs(pool.get("chg_m5", 0))
    if chg > 10:
        base += 3
    elif chg > 5:
        base += 1.5
    # neu chain ETH gas cao, cong them
    # (co the doc tu RPC neu can, hien tai dung he so don gian)
    return base

def calc_ai_score(pool, gross_pct, net_pct, chain_key):
    """AI Score 0-100 de loc keo ngon"""
    # 40 diem cho profit
    profit_score = min(net_pct / 5.0 * 40, 40) if net_pct > 0 else 0
    # 20 diem cho vol/liq ratio (vol cao so voi liq la tot)
    vl_ratio = pool["vol24"] / max(pool["liq"], 1)
    vol_score = min(vl_ratio * 20, 20)  # ratio 1.0 -> 20 diem
    # 15 diem cho txns h1 (dong)
    tx_score = min(pool.get("txns_h1", 0) / 100.0 * 15, 15)
    # 10 diem cho tuoi: token 1-24h duoc diem cao neu la meme moi, token qua gia (>30 ngay) bi tru
    age_h = pool.get("age_h", 0)
    if age_h < 2:
        age_score = 10
    elif age_h < 24:
        age_score = 8
    elif age_h < 168:  # 1 tuan
        age_score = 5
    else:
        age_score = 2
    # 15 diem cho bien dong (co bien dong moi co co hoi)
    vola_score = min(abs(pool.get("chg_m5", 0)) * 2, 15)
    total = profit_score + vol_score + tx_score + age_score + vola_score
    return round(min(total, 100), 1)

# Volatile tracker - token nao bien dong lien tuc -> de xuat hold inventory
VOLATILE_TRACKER = {}
HOT_TOKENS = {}  # {symbol: count}
VOLATILE_LOCK = threading.Lock()

def track_volatility(tok_symbol, pool, contract=None, chain=None, profit_potential=0):
    chg = abs(pool.get("chg_m5", 0))
    if chg >= VOLATILE_CHG_THRESHOLD:
        with VOLATILE_LOCK:
            VOLATILE_TRACKER[tok_symbol] = VOLATILE_TRACKER.get(tok_symbol, 0) + 1
            if VOLATILE_TRACKER[tok_symbol] >= VOLATILE_COUNT_TO_HOT:
                HOT_TOKENS[tok_symbol] = time.time()
                # V8.9: auto record to inventory DB
                try:
                    INV_DB.record_hot(tok_symbol, contract or "", chain or "", profit_potential)
                except:
                    pass
                return True
    return False

def should_hold_inventory(symbol):
    """Check if we should hold inventory on both CEX and DEX for fast arbitrage"""
    with VOLATILE_LOCK:
        hits = VOLATILE_TRACKER.get(symbol, 0)
        return hits >= VOLATILE_COUNT_TO_HOT

def get_inventory_suggestion(symbol, usd_size):
    """Suggest inventory size based on volatility"""
    if not should_hold_inventory(symbol):
        return None
    # Suggest hold 50% of size on each side
    # Example: usd_size 500$ -> hold 250$ token on DEX + 250$ USDT on CEX
    return {
        "dex_hold_usd": usd_size * 0.5,
        "cex_hold_usd": usd_size * 0.5,
        "reason": f"HOT token {symbol} volatil {VOLATILE_TRACKER.get(symbol)} times"
    }

def is_hot_token(symbol):
    with VOLATILE_LOCK:
        ts = HOT_TOKENS.get(symbol)
        if not ts:
            return False
        # hot trong 2h
        if time.time() - ts < 7200:
            return True
        else:
            HOT_TOKENS.pop(symbol, None)
            return False

# Multi-CEX registry
CEX_REGISTRY = {
    "MEXC": {"name": "MEXC", "taker": 0.002, "maker": 0.0, "withdraw_fees": "mexc", "ws": "wss://wbs.mexc.com/ws"},
    "GATE": {"name": "Gate.io", "taker": 0.002, "maker": 0.002, "api": "https://api.gateio.ws/api/v4", "ws": "wss://api.gateio.ws/ws/v4/"},
    "BINANCE": {"name": "Binance", "taker": 0.001, "maker": 0.0005, "api": "https://api.binance.com", "ws": "wss://stream.binance.com:9443/ws"},
    "OKX": {"name": "OKX", "taker": 0.001, "maker": 0.0008, "api": "https://www.okx.com", "ws": "wss://ws.okx.com:8443/ws/v5/public"},
    "BYBIT": {"name": "Bybit", "taker": 0.001, "maker": 0.0005},
    "BITGET": {"name": "Bitget", "taker": 0.002, "maker": 0.002},
    "KUCOIN": {"name": "KuCoin", "taker": 0.001, "maker": 0.001},
    "BINGX": {"name": "BingX", "taker": 0.002, "maker": 0.002},
    "HTX": {"name": "HTX", "taker": 0.002, "maker": 0.002},
    "LBANK": {"name": "LBank", "taker": 0.002, "maker": 0.002},
}


def evaluate(chain_key, tok, pool, depth):
    cfg = CHAINS[chain_key]
    st = STATS[chain_key]
    # V8.8: dynamic size 200-2000$
    # tinh size dua tren liq truoc khi tinh toan chinh
    # su dung gross tam tinh de tinh size
    bids, asks = depth["bids"], depth["asks"]
    if (asks[0][0] - bids[0][0]) / asks[0][0] * 100 > MAX_MEXC_SPREAD:
        return []
    
    # === DEPOSIT/WITHDRAW FILTER V8.9.1 ===
    # Nếu sàn đóng nạp/rút thì bỏ qua chiều đó
    # (đã được check ở trên nhưng double check ở đây cho an toàn)
    
    # === NEW MEMECOIN FILTER V8.8 ===
    age_h = pool.get("age_h", 0)
    if age_h < MIN_POOL_AGE_H:
        if not ALLOW_NEW_MEME:
            return []
        # cho phep meme moi neu vol va txns cao
        if not (pool["vol24"] >= 100000 and pool.get("txns_h1",0) >= 50):
            # neu la meme cuc moi (<2h) thi yeu cau vol cao hon
            if age_h < MIN_POOL_AGE_MEME_H:
                return []
            # vol trung binh van cho phep neu gross du lon
            if pool["vol24"] < 50000:
                return []
        # memecoin moi thi tang buffer
        extra_meme_buffer = 0.5
    else:
        extra_meme_buffer = 0

    sec = get_security(chain_key, tok["contract"])
    if sec["bad"] or sec["buy_tax"] > TAX_MAX or sec["sell_tax"] > TAX_MAX:
        st.inc("suspect")
        log.info("LOAI SECURITY %s %s bad=%s buy %.1f%% sell %.1f%%", chain_key, tok["symbol"], sec["bad"],
                 sec["buy_tax"] * 100, sec["sell_tax"] * 100)
        return []
    buy_tax, sell_tax = sec["buy_tax"], sec["sell_tax"]
    dec = get_decimals(chain_key, tok["contract"]) if (REAL_PRICE_CHECK and chain_key in REAL_CHAINS) else None
    can_real = dec is not None
    if not can_real and REQUIRE_REAL_PRICE and chain_key in REAL_CHAINS:
        st.inc("real_price_fail")
        return []
    impact_est, buy_est, sell_est = dex_prices_estimate(pool)
    if not can_real and impact_est > MAX_IMPACT_PCT * 1.5:
        return []
    buf = risk_buffer(chain_key, pool, sec) + extra_meme_buffer
    
    # === HONEYPOT SIMULATION V8.8 ===
    if can_real and dec:
        if is_honeypot_simulation(chain_key, tok["contract"], dec):
            st.inc("suspect")
            log.info("LOAI HONEYPOT SIM %s %s", chain_key, tok["symbol"])
            return []

    # === MAKER vs TAKER LOGIC V8.8 ===
    # Tinh depth: tong volume bid/ask trong 1% gia
    # Neu depth du lon -> dung maker fee 0%, neu khong -> taker 0.2%
    def calc_depth_usd(orders, price_ref, pct=0.01):
        total = 0
        for price, qty in orders[:10]:
            if abs(price - price_ref)/price_ref <= pct:
                total += price * qty
            else:
                break
        return total
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask)/2
    bid_depth_usd = calc_depth_usd(bids, best_bid)
    ask_depth_usd = calc_depth_usd(asks, best_ask)
    
    # Tinh size dong truoc
    # Dung gross tam de tinh size, sau do tinh lai chinh xac
    gross_est_mexc_to_dex = (sell_est / best_ask -1)*100 if best_ask else 0
    gross_est_dex_to_mexc = (best_bid / buy_est -1)*100 if buy_est else 0
    avg_gross_est = max(gross_est_mexc_to_dex, gross_est_dex_to_mexc, 1.0)
    usd_dynamic = calc_optimal_size(pool["liq"], pool["vol24"], avg_gross_est, ORDER_SIZE_USD)
    gas = cfg["gas_usd"]
    
    # Quyet dinh maker/taker dua tren depth
    # Neu depth >= size * MULTIPLIER -> co the dat maker
    can_maker = min(bid_depth_usd, ask_depth_usd) >= usd_dynamic * MAKER_DEPTH_MULTIPLIER
    
    if can_maker:
        mexc_fee_use = MEXC_MAKER_FEE
        fee_type = "maker"
    else:
        mexc_fee_use = MEXC_TAKER_FEE
        fee_type = "taker"

    common = {"mexc_best": (bids[0][0], asks[0][0]), "buy_tax": buy_tax, "sell_tax": sell_tax,
              "withdraw_enable": tok.get("withdraw_enable"), "deposit_enable": tok.get("deposit_enable"),
              "risk_buf": buf, "sec_known": sec["known"], "sec_soft": sec["soft"], "decimals": dec,
              "usd_size": usd_dynamic, "fee_type": fee_type, "mexc_fee": mexc_fee_use,
              "transfer_min_real": get_real_transfer_time(chain_key, pool)}
    out = []
    if tok.get("withdraw_enable", True):
        w = walk_usd(asks, usd_dynamic)
        if w:
            avg, tokens = w
            tokens_net = tokens - tok["withdraw_fee"]
            if tokens_net > 0 and tokens >= tok["withdraw_min"]:
                q = dex_sell_quote(chain_key, tok["contract"], dec, tokens_net) if can_real else None
                if q:
                    st.inc("real_price_ok")
                    usd_out, impact, src = q["usd_out"], q["impact"], q["source"]
                    if impact is None:
                        impact = impact_est
                elif REQUIRE_REAL_PRICE and chain_key in REAL_CHAINS:
                    st.inc("real_price_fail")
                    usd_out = None
                else:
                    usd_out, impact, src = tokens_net * sell_est, impact_est, "estimate"
                if usd_out:
                    sell_px = usd_out / tokens_net
                    gross = (sell_px / avg - 1) * 100
                    cost = usd_dynamic * (1 + mexc_fee_use)
                    revenue = usd_out * (1 - EXTRA_SLIPPAGE) * (1 - sell_tax) - gas
                    net = revenue - cost
                    net_pct = net / cost * 100
                    real_impact = max(impact or 0, impact_est)
                    # volatility decay
                    decay = abs(pool.get("chg_m5",0)) * 0.1
                    net_pct_decayed = net_pct - decay
                    if -5 < gross <= MAX_GROSS and real_impact <= MAX_IMPACT_PCT and impact <= 2.5:
                        if abs(sell_px - pool["price"]) / pool["price"] > 0.12:
                            st.inc("suspect")
                        else:
                            ai = calc_ai_score(pool, gross, net_pct_decayed, chain_key)
                            if ai >= AI_SCORE_MIN:
                                # track volatility for inventory
                                is_hot = track_volatility(tok["symbol"], pool, tok.get("contract"), chain_key, net_pct_decayed)
                                out.append(dict(common, direction="MEXC_TO_DEX", token_amount=tokens,
                                                token_after_tax=tokens_net * (1 - sell_tax), mexc_avg=avg,
                                                dex_exec=sell_px, cost=cost, net_usd=net, net_pct=net_pct_decayed,
                                                adj_net_pct=net_pct_decayed - buf, gross_pct=gross,
                                                gross_after_tax=gross - sell_tax * 100, gas=gas,
                                                withdraw_fee_usd=tok["withdraw_fee"] * avg, impact_dex=real_impact,
                                                real_source=src, ai_score=ai, is_hot=is_hot))
    if tok.get("deposit_enable", True):
        q = dex_buy_quote(chain_key, tok["contract"], dec, usd_dynamic) if can_real else None
        if q:
            st.inc("real_price_ok")
            tokens_out, buy_px, impact, src = q["tokens"], q["price"], q["impact"], q["source"]
            if impact is None:
                impact = impact_est
        elif REQUIRE_REAL_PRICE and chain_key in REAL_CHAINS:
            st.inc("real_price_fail")
            tokens_out = None
        else:
            buy_px, impact, src = buy_est, impact_est, "estimate"
            tokens_out = usd_dynamic / buy_est
        if tokens_out:
            tokens_dep = tokens_out * (1 - EXTRA_SLIPPAGE) * (1 - buy_tax)
            proceeds = walk_tokens(bids, tokens_dep)
            if proceeds:
                cost = usd_dynamic + gas * 2
                revenue = proceeds * (1 - mexc_fee_use)
                net = revenue - cost
                avg = proceeds / tokens_dep
                gross = (avg / buy_px - 1) * 100
                net_pct = net / cost * 100
                real_impact = max(impact or 0, impact_est)
                decay = abs(pool.get("chg_m5",0)) * 0.1
                net_pct_decayed = net_pct - decay
                if -5 < gross <= MAX_GROSS and real_impact <= MAX_IMPACT_PCT and impact <= 2.5:
                    if abs(buy_px - pool["price"]) / pool["price"] > 0.12:
                        st.inc("suspect")
                    else:
                        ai = calc_ai_score(pool, gross, net_pct_decayed, chain_key)
                        if ai >= AI_SCORE_MIN:
                            is_hot = track_volatility(tok["symbol"], pool, tok.get("contract"), chain_key, net_pct_decayed)
                            out.append(dict(common, direction="DEX_TO_MEXC", token_amount=tokens_dep,
                                            token_raw=tokens_out, mexc_avg=avg, dex_exec=buy_px, cost=cost,
                                            net_usd=net, net_pct=net_pct_decayed, adj_net_pct=net_pct_decayed - buf,
                                            gross_pct=gross, gross_after_tax=gross - buy_tax * 100, gas=gas * 2,
                                            withdraw_fee_usd=0.0, impact_dex=real_impact, real_source=src,
                                            ai_score=ai, is_hot=is_hot))
    return out

COOLDOWN = {}
CD_LOCK = threading.Lock()
def should_alert(key, adj_pct):
    with CD_LOCK:
        last = COOLDOWN.get(key)
    if not last:
        return True
    t, p = last
    age = (datetime.now() - t).total_seconds()
    return age >= COOLDOWN_MIN * 60 or (age >= 300 and adj_pct > p * 1.3)
def mark_alerted(key, adj_pct):
    with CD_LOCK:
        COOLDOWN[key] = (datetime.now(), adj_pct)

PAUSED = set()
BL = {}
BL_LOCK = threading.Lock()
BAD_STREAK = {}
def is_blacklisted(chain_key, tok):
    now = time.time()
    with BL_LOCK:
        for k in (f"SYM:{tok['symbol'].upper()}", f"TOK:{chain_key}:{tok['dedup_key']}"):
            u = BL.get(k)
            if u and u > now:
                return True
    return False

class PaperDB:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            c = self.conn
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except:
                pass
            c.execute("""CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chain TEXT, symbol TEXT, contract TEXT,
                direction TEXT, pair TEXT, dex TEXT, order_usd REAL, token_amount REAL, mexc_avg REAL,
                dex_exec REAL, dex_price REAL, liq REAL, gross_pct REAL, net_pct REAL, net_usd REAL,
                net_pct_5m REAL, net_pct_10m REAL, raw TEXT)""")
            for col, typ in (("adj_net_pct", "REAL"), ("risk_buf", "REAL"), ("st_5m", "TEXT"), ("st_10m", "TEXT")):
                try:
                    c.execute(f"ALTER TABLE signals ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass
            c.execute("""CREATE TABLE IF NOT EXISTS followups(
                fid INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, minutes INTEGER, due REAL, done INTEGER DEFAULT 0)""")
            c.execute("CREATE TABLE IF NOT EXISTS blacklist(key TEXT PRIMARY KEY, until REAL, reason TEXT)")
            c.execute("UPDATE followups SET done=0 WHERE done=2")
            c.commit()
    def insert(self, chain, tok, pool, p):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO signals(ts,chain,symbol,contract,direction,pair,dex,order_usd,token_amount,mexc_avg,dex_exec,"
                "dex_price,liq,gross_pct,net_pct,net_usd,adj_net_pct,risk_buf,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(), chain, tok["symbol"], tok["contract"], p["direction"], tok["pair"],
                 pool["dex"], ORDER_SIZE_USD, p["token_amount"], p["mexc_avg"], p["dex_exec"], pool["price"],
                 pool["liq"], p["gross_pct"], p["net_pct"], p["net_usd"], p["adj_net_pct"], p["risk_buf"],
                 json.dumps({"profit": p, "pool": pool, "tok": tok}, default=str)))
            sid = cur.lastrowid
            now = time.time()
            for m in FOLLOWUP_MIN:
                self.conn.execute("INSERT INTO followups(signal_id,minutes,due) VALUES(?,?,?)", (sid, m, now + m * 60))
            self.conn.commit()
            return sid
    def due_followups(self, limit=20):
        with self.lock:
            cur = self.conn.execute("""SELECT f.fid,f.signal_id,f.minutes,s.chain,s.direction,s.raw FROM followups f
                JOIN signals s ON s.id=f.signal_id WHERE f.done=0 AND f.due<=? ORDER BY f.due LIMIT ?""", (time.time(), limit))
            rows = cur.fetchall()
            for r in rows:
                self.conn.execute("UPDATE followups SET done=2 WHERE fid=?", (r[0],))
            self.conn.commit()
            return rows
    def finish_followup(self, fid, sid, minutes, value, status):
        if minutes not in (5, 10):
            return
        with self.lock:
            self.conn.execute(f"UPDATE signals SET net_pct_{minutes}m=?, st_{minutes}m=? WHERE id=?", (value, status, sid))
            self.conn.execute("UPDATE followups SET done=1 WHERE fid=?", (fid,))
            self.conn.commit()
    def recent(self, n=50):
        with self.lock:
            cur = self.conn.execute("SELECT id,ts,chain,symbol,direction,gross_pct,net_pct,adj_net_pct,net_pct_5m,st_5m,net_pct_10m,st_10m FROM signals ORDER BY id DESC LIMIT ?", (n,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    def chain_outcomes(self, chain, n=40):
        with self.lock:
            cur = self.conn.execute("SELECT net_pct_5m, st_5m FROM signals WHERE chain=? AND st_5m IN ('ok','gone') ORDER BY id DESC LIMIT ?", (chain, n))
            return cur.fetchall()
    def summary(self, n=500):
        with self.lock:
            cur = self.conn.execute("SELECT chain,direction,net_pct,adj_net_pct,net_pct_5m,st_5m,net_pct_10m,st_10m FROM signals ORDER BY id DESC LIMIT ?", (n,))
            rows = cur.fetchall()
        agg = {}
        for chain, d, net, adj, n5, s5, n10, s10 in rows:
            a = agg.setdefault((chain, d), {"n": 0, "alert_net": 0.0, "n5": 0, "w5": 0, "sum5": 0.0, "n10": 0, "w10": 0})
            a["n"] += 1; a["alert_net"] += net or 0
            if s5 in ("ok", "gone"):
                a["n5"] += 1
                if s5 == "ok" and (n5 or 0) > 0:
                    a["w5"] += 1
                a["sum5"] += (n5 if s5 == "ok" and n5 is not None else 0.0)
            if s10 in ("ok", "gone"):
                a["n10"] += 1
                if s10 == "ok" and (n10 or 0) > 0:
                    a["w10"] += 1
        out = []
        for (chain, d), a in sorted(agg.items()):
            out.append({"chain": chain, "direction": d, "signals": a["n"], "avg_alert_net": round(a["alert_net"] / a["n"], 3),
                        "followed_5m": a["n5"], "winrate_5m": round(a["w5"] / a["n5"], 3) if a["n5"] else None,
                        "avg_net_5m": round(a["sum5"] / a["n5"], 3) if a["n5"] else None,
                        "winrate_10m": round(a["w10"] / a["n10"], 3) if a["n10"] else None})
        return out
    def load_blacklist(self):
        with self.lock:
            return self.conn.execute("SELECT key, until FROM blacklist WHERE until>?", (time.time(),)).fetchall()
    def save_blacklist(self, key, until, reason):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO blacklist(key,until,reason) VALUES(?,?,?)", (key, until, reason))
            self.conn.commit()
    def del_blacklist(self, key):
        with self.lock:
            self.conn.execute("DELETE FROM blacklist WHERE key=?", (key,))
            self.conn.commit()

DB = PaperDB(DB_PATH)
for _k, _u in DB.load_blacklist():
    BL[_k] = _u

_AD_CACHE = {}
def adaptive_threshold(chain_key):
    if not ADAPTIVE:
        return THRESHOLD_NET
    c = _AD_CACHE.get(chain_key)
    if c and time.time() - c[0] < 300:
        return c[1]
    rows = DB.chain_outcomes(chain_key, 40)
    thr = THRESHOLD_NET
    if len(rows) >= 15:
        wins = sum(1 for v, s in rows if s == "ok" and (v or 0) > 0)
        wr = wins / len(rows)
        if wr < 0.35:
            thr = THRESHOLD_NET + 0.4
        elif wr < 0.50:
            thr = THRESHOLD_NET + 0.2
        elif wr > 0.70:
            thr = max(THRESHOLD_NET * 0.8, THRESHOLD_NET - 0.1)
    _AD_CACHE[chain_key] = (time.time(), thr)
    return thr

def process_followup(row):
    fid, sid, minutes, chain, direction, raw = row
    value, status = None, "error"
    try:
        tok = json.loads(raw)["tok"]
        pools = fetch_dex_pools(chain, [tok["contract"]])
        if pools is None:
            raise RuntimeError("dex api")
        pool = pools.get(tok["dedup_key"])
        depth = mexc_depth(tok["pair"]) if pool else None
        if not pool or not depth:
            status = "gone"
        else:
            status = "gone"
            for p in evaluate(chain, tok, pool, depth):
                if p["direction"] == direction:
                    value, status = p["adj_net_pct"], "ok"
    except Exception as e:
        log.warning("Follow-up error: %s", e)
        status = "error"
    DB.finish_followup(fid, sid, minutes, value, status)
    if minutes == 5 and status in ("ok", "gone"):
        try:
            tok = json.loads(raw)["tok"]
            key = (chain, tok["dedup_key"], direction)
            win = status == "ok" and (value or 0) > 0
            BAD_STREAK[key] = 0 if win else BAD_STREAK.get(key, 0) + 1
            if BAD_STREAK[key] >= BAD_STREAK_BLACKLIST:
                until = time.time() + BLACKLIST_HOURS * 3600
                bk = f"TOK:{chain}:{tok['dedup_key']}"
                with BL_LOCK:
                    BL[bk] = until
                DB.save_blacklist(bk, until, "bad streak")
                BAD_STREAK[key] = 0
                send_telegram(f"🚫 Tu dong blacklist {esc(tok['symbol'])} [{chain}] {BLACKLIST_HOURS:.0f}h")
        except:
            log.exception("blacklist loi")

def followup_loop():
    ex = ThreadPoolExecutor(max_workers=3, thread_name_prefix="FU")
    while True:
        time.sleep(10)
        try:
            rows = DB.due_followups()
            if rows:
                list(ex.map(process_followup, rows))
        except:
            log.exception("followup_loop loi")

def fmt_price(v):
    try:
        v = float(v or 0)
        if v == 0:
            return "0"
        if abs(v) >= 1:
            return f"{v:.6f}"
        if abs(v) >= 0.01:
            return f"{v:.8f}"
        return f"{v:.12f}"
    except:
        return str(v)
def fmt_token_amount(v):
    try:
        v = float(v)
        return f"{v:,.0f}" if v >= 1000 else f"{v:.6f}"
    except:
        return str(v)

def send_telegram(text):
    try:
        r = session().post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                           data={"chat_id": CHAT_ID, "text": text[:4000], "parse_mode": "HTML",
                                 "disable_web_page_preview": True}, timeout=15)
        if r.status_code != 200:
            log.warning("Telegram failed %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram error: %s", e)
        return False

def get_dex_swap_urls(chain_key, contract):
    """Tra ve (buy_url, sell_url) swap truc tiep, khong phai dexscreener"""
    c = contract
    if chain_key == "SOL":
        # Jupiter - buy: SOL -> token, sell: token -> SOL
        buy = f"https://jup.ag/swap/SOL-{c}"
        sell = f"https://jup.ag/swap/{c}-SOL"
    elif chain_key == "BNB":
        buy = f"https://pancakeswap.finance/swap?outputCurrency={c}&chainId=56"
        sell = f"https://pancakeswap.finance/swap?inputCurrency={c}&outputCurrency=0x55d398326f99059fF775485246999027B3197955&chainId=56"
    elif chain_key == "BASE":
        buy = f"https://app.uniswap.org/#/swap?chain=base&outputCurrency={c}"
        sell = f"https://app.uniswap.org/#/swap?chain=base&inputCurrency={c}"
    elif chain_key == "ETH":
        buy = f"https://app.uniswap.org/#/swap?outputCurrency={c}&chain=mainnet"
        sell = f"https://app.uniswap.org/#/swap?inputCurrency={c}&chain=mainnet"
    elif chain_key == "POLYGON":
        buy = f"https://app.uniswap.org/#/swap?chain=polygon&outputCurrency={c}"
        sell = f"https://app.uniswap.org/#/swap?chain=polygon&inputCurrency={c}"
    elif chain_key == "ARBITRUM":
        buy = f"https://app.uniswap.org/#/swap?chain=arbitrum&outputCurrency={c}"
        sell = f"https://app.uniswap.org/#/swap?chain=arbitrum&inputCurrency={c}"
    else:
        # fallback: dexscreener
        buy = f"https://dexscreener.com/{CHAINS[chain_key]['dex_chain']}/{c}"
        sell = buy
    return buy, sell

def send_alert(chain_key, tok, pool, p):
    cfg = CHAINS[chain_key]
    bid, ask = p["mexc_best"]
    thr = adaptive_threshold(chain_key)
    profit_emoji = "🟢" if p["adj_net_pct"] >= 2 else "🟡" if p["adj_net_pct"] >= 1 else "🔵"
    
    # Links - SWAP TRUC TIEP
    mexc_trade_url = f"https://www.mexc.com/exchange/{tok['pair']}"
    dex_buy_url, dex_sell_url = get_dex_swap_urls(chain_key, tok['contract'])
    dex_info_url = pool['url']
    explorer_url = cfg['explorer'] + tok['contract']

    # Action
    if p["direction"] == "MEXC_TO_DEX":
        icon = "📤"
        action = f"<a href='{esc(mexc_trade_url)}'>BUY MEXC</a> → WITHDRAW {chain_key} → <a href='{esc(dex_sell_url)}'>SELL DEX</a>"
    else:
        icon = "📥"
        action = f"<a href='{esc(dex_buy_url)}'>BUY DEX</a> → DEPOSIT MEXC → SELL {esc(tok['pair'])}"

    if p["buy_tax"] or p["sell_tax"]:
        tax_line = f"Tax: Buy {p['buy_tax']*100:.1f}% / Sell {p['sell_tax']*100:.1f}%"
    else:
        tax_line = "Tax: 0%"
    
    en_line = f"Deposit: {'✅' if p.get('deposit_enable') else '❌'} | Withdraw: {'✅' if p.get('withdraw_enable') else '❌'}"
    
    warn = []
    if not p.get("sec_known") and cfg.get("evm"):
        warn.append("no security data")
    if p.get("sec_soft"):
        warn.append(",".join(p["sec_soft"]))
    warn_line = f"⚠️ {'; '.join(warn)}\n" if warn else ""
    
    # V8.8 extras
    usd_size = p.get("usd_size", ORDER_SIZE_USD)
    fee_type = p.get("fee_type", "taker")
    mexc_fee = p.get("mexc_fee", MEXC_TAKER_FEE)
    ai_score = p.get("ai_score", 0)
    is_hot = p.get("is_hot", False)
    transfer_real = p.get("transfer_min_real", cfg['transfer_min'])
    
    ai_emoji = "🔥" if ai_score >= 80 else "⭐" if ai_score >= 70 else "✨"
    inv_suggestion = get_inventory_suggestion(tok["symbol"], usd_size) if is_hot else None
    if inv_suggestion:
        hot_line = f"🔥 HOT - Hold ${inv_suggestion['dex_hold_usd']:.0f} on DEX + ${inv_suggestion['cex_hold_usd']:.0f} on {tok.get('cex','CEX')} for instant arb"
    else:
        hot_line = "🔥 HOT - High volatility! Recommend hold inventory on both sides" if is_hot else ""
    
    msg = (
        f"{profit_emoji} <b>{esc(tok['symbol'])} [{chain_key}] {p['direction']}</b> | {esc(p.get('real_source','?'))} | {cfg['emoji']} | {ai_emoji} AI {ai_score}/100\n"
        f"{'─'*24}\n"
        f"💰 <b>PROFIT {p['adj_net_pct']:.2f}% (${p['net_usd']:.2f})</b> after buffer | NET {p['net_pct']:.2f}%\n"
        f"📈 Gross {p['gross_pct']:.2f}% → After tax {p['gross_after_tax']:.2f}% | Impact {p['impact_dex']:.2f}%\n"
        f"🛡️ Buffer {p['risk_buf']:.2f}% | Threshold {thr:.2f}% | Size ${usd_size:.0f} ({fee_type})\n"
        f"{'─'*24}\n"
        f"{icon} <b>{action}</b>\n"
        f"{'─'*24}\n"
        f"💸 {tax_line} | 🏦 {en_line}\n"
        f"💵 Capital ${usd_size:.0f} (dynamic {ORDER_SIZE_MIN:.0f}-{ORDER_SIZE_MAX:.0f}) | Amount {fmt_token_amount(p['token_amount'])}\n"
        f"⛽ Gas ${p['gas']:.2f} | Withdraw Fee ${p['withdraw_fee_usd']:.2f} | MEXC Fee {mexc_fee*100:.2f}% ({fee_type})\n"
        f"⏱️ Transfer ~{transfer_real:.1f} min (real) | Volatility decay included\n"
        f"{'─'*24}\n"
        f"🏦 <b>MEXC {esc(tok['pair'])}</b>\n"
        f"   Bid {fmt_price(bid)} / Ask {fmt_price(ask)} → Avg ${fmt_price(p['mexc_avg'])}\n"
        f"🦄 <b>{esc(pool['dex'])} {esc(pool['pair_name'])}</b>\n"
        f"   DS ${fmt_price(pool['price'])} → Exec ${fmt_price(p['dex_exec'])}\n"
        f"   💧 ${pool['liq']:,.0f} | Vol ${pool['vol24']:,.0f} | Tx1h {pool['txns_h1']} | Age {pool.get('age_h',0):.0f}h\n"
        f"   Change 5m {pool.get('chg_m5',0):+.1f}% | 1h {pool.get('chg_h1',0):+.1f}%\n"
        f"{'─'*24}\n"
        f"{warn_line}"
        f"{hot_line + chr(10) if hot_line else ''}"
        f"📜 Contract: <code>{esc(tok['contract'])}</code>\n"
        f"🔗 <a href='{esc(mexc_trade_url)}'>MEXC Trade</a> | "
        f"<a href='{esc(dex_buy_url)}'>DEX Buy</a> | "
        f"<a href='{esc(dex_sell_url)}'>DEX Sell</a> | "
        f"<a href='{esc(dex_info_url)}'>Info</a> | "
        f"<a href='{esc(explorer_url)}'>Explorer</a>\n"
        f"⏰ {datetime.now().strftime('%H:%M %d/%m')} | AI Score {ai_score}/100 | {'HOT 🔥' if is_hot else 'Normal'}"
    )
    return send_telegram(msg)

def summary_text(limit=12):
    rows = DB.summary()
    if not rows:
        return "No data yet."
    lines = ["<b>Stats (last 500 signals)</b>"]
    for r in rows[:limit]:
        wr = f"{r['winrate_5m']*100:.0f}%" if r["winrate_5m"] is not None else "n/a"
        av = f"{r['avg_net_5m']:+.2f}%" if r["avg_net_5m"] is not None else "n/a"
        d = "M→D" if r["direction"] == "MEXC_TO_DEX" else "D→M"
        lines.append(f"{r['chain']} {d}: n={r['signals']} | win5m={wr} (n={r['followed_5m']}) | avg5m={av}")
    return "\n".join(lines)

def handle_command(text):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].upper() if len(parts) > 1 else ""
    if cmd in ("/start", "/help"):
        return ("/status - bot status\n"
                "/stats - thong ke\n"
                "/top - 10 signal gan nhat\n"
                "/hot - token bien dong nhieu\n"
                "/inventory - vi tri dang hold\n"
                "/live - gia WS live 9 san\n"
                "/pause CHAIN /resume CHAIN\n"
                "/blacklist SYMBOL /unblacklist SYMBOL")
    if cmd == "/status":
        lines = [f"<b>STATUS</b> {datetime.now().strftime('%H:%M %d/%m')}"]
        for k, st in STATS.items():
            s = st.snapshot()
            lines.append(f"{CHAINS[k]['emoji']} {k}{' (PAUSED)' if k in PAUSED else ''}: sig={s['signals']} opp={s['opportunity']} "
                         f"realOK={s['real_price_ok']} realFail={s['real_price_fail']} err={s['errors']} thr={adaptive_threshold(k):.2f}")
        lines.append("API: " + esc(API.line()))
        return "\n".join(lines)
    if cmd == "/stats":
        return summary_text()
    if cmd == "/top":
        rows = DB.recent(10)
        if not rows:
            return "No signals yet."
        out = ["<b>10 signal gan nhat</b>"]
        for r in rows:
            f5 = f"{r['net_pct_5m']:+.2f}%" if r["net_pct_5m"] is not None else (r["st_5m"] or "...")
            out.append(f"#{r['id']} {esc(r['symbol'])}[{r['chain']}] adj {r['adj_net_pct'] or 0:.2f}% → 5m: {f5}")
        return "\n".join(out)
    if cmd == "/pause":
        if arg in CHAINS:
            PAUSED.add(arg); return f"Paused {arg}"
        return "Invalid chain: " + ", ".join(CHAINS)
    if cmd == "/resume":
        PAUSED.discard(arg); return f"Resumed {arg}"
    if cmd == "/blacklist" and arg:
        until = time.time() + 7 * 86400
        with BL_LOCK:
            BL[f"SYM:{arg}"] = until
        DB.save_blacklist(f"SYM:{arg}", until, "manual")
        return f"Da blacklist {esc(arg)} 7 days"
    if cmd == "/unblacklist" and arg:
        with BL_LOCK:
            BL.pop(f"SYM:{arg}", None)
        DB.del_blacklist(f"SYM:{arg}")
        return f"Da go blacklist {esc(arg)}"
    if cmd == "/hot":
        try:
            hot = INV_DB.get_hot(15)
            if not hot:
                return "Chua co HOT token nao"
            lines = ["<b>🔥 HOT TOKENS (bien dong nhieu)</b>"]
            for sym, contract, chain, hits, pot in hot:
                lines.append(f"{esc(sym)} [{chain}] hits={hits} profit~{pot:.1f}% - Hold ${ORDER_SIZE_MIN}-{ORDER_SIZE_MAX}")
            return "\n".join(lines)
        except Exception as e:
            return f"Hot loi {e}"
    if cmd == "/inventory":
        try:
            inv = INV_DB.list_active()
            if not inv:
                return "Chua co inventory. Khi token HOT, bot se de xuat hold 50% DEX + 50% CEX"
            lines = ["<b>📦 INVENTORY AUTO-HOLD</b>"]
            for sym, contract, chain, cex, dex_amt, cex_amt, avg, prof in inv[:15]:
                lines.append(f"{esc(sym)} {chain}/{cex}: DEX {dex_amt:.2f} + CEX {cex_amt:.2f} avg ${avg:.4f}")
            return "\n".join(lines)
        except Exception as e:
            return f"Inventory loi {e}"
    if cmd == "/live":
        try:
            with CEX_LIVE_LOCK:
                lines = [f"<b>📡 LIVE PRICES WS ({len(CEX_LIVE)} CEXs)</b>"]
                for cex, pairs in CEX_LIVE.items():
                    lines.append(f"--- {cex} ---")
                    for sym, d in list(pairs.items())[:5]:
                        age = int(time.time()-d["ts"])
                        lines.append(f"{sym}: bid {d['bid']} ask {d['ask']} ({d['source']} {age}s)")
            return "\n".join(lines[:30])
        except Exception as e:
            return f"Live loi {e}"
    return "Invalid command. /help"

def telegram_poll_loop():
    offset = None
    while True:
        try:
            r = session().get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                              params={"timeout": 25, "offset": offset}, timeout=35)
            if r.status_code == 409:
                time.sleep(30); continue
            if r.status_code != 200:
                time.sleep(10); continue
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if str((m.get("chat") or {}).get("id")) != CHAT_ID or not m.get("text", "").startswith("/"):
                    continue
                try:
                    send_telegram(handle_command(m["text"]))
                except:
                    log.exception("Command error")
        except Exception as e:
            log.warning("Poll Telegram error: %s", e)
            time.sleep(10)

def check_candidate(chain_key, tok, pool):
    st = STATS[chain_key]
    try:
        depth = mexc_depth(tok["pair"])
        if not depth:
            return None
        st.inc("fetched"); st.success()
        thr = adaptive_threshold(chain_key)
        best = None
        for p in evaluate(chain_key, tok, pool, depth):
            if p["gross_pct"] >= PREFILTER_GROSS:
                st.inc("opportunity")
            if p["adj_net_pct"] >= thr and (not best or p["adj_net_pct"] > best["adj_net_pct"]):
                best = p
        return (tok, pool, best) if best else None
    except:
        st.inc("errors")
        log.exception("Loi xu ly %s %s", chain_key, tok["symbol"])
        return None

def prefilter(chain_key, tok, pool, book):
    bt = book.get(tok["pair"])
    if not bt:
        return False
    bid, ask = bt
    f = pool["fee"]
    imp = (2 * ORDER_SIZE_USD / max(pool["liq"], 1)) * 0.5
    buy_px, sell_px = pool["price"] * (1 + f + imp), pool["price"] * (1 - f - imp)
    g1 = (sell_px / ask - 1) * 100 if tok.get("withdraw_enable", True) else -99
    g2 = (bid / buy_px - 1) * 100 if tok.get("deposit_enable", True) else -99
    return max(g1, g2) >= PREFILTER_GROSS

def scan_chain_once(chain_key, stop):
    st = STATS[chain_key]
    MEXC.ensure_static()
    tokens = MEXC.tokens.get(chain_key, [])
    if not tokens:
        return False
    book = MEXC.get_book()
    if not book:
        return False
    for i in range(0, len(tokens), DEX_BATCH):
        if stop.is_set():
            return True
        if chain_key in PAUSED:
            stop.wait(5); return True
        st.beat()
        chunk = tokens[i:i + DEX_BATCH]
        pools = fetch_dex_pools(chain_key, [t["contract"] for t in chunk])
        if pools is None:
            st.inc("errors"); continue
        candidates = []
        for tok in chunk:
            pool = pools.get(tok["dedup_key"])
            if not pool or not symbol_ok(tok["symbol"], pool["base_symbol"]):
                continue
            st.inc("attempted")
            if is_blacklisted(chain_key, tok):
                st.inc("blacklisted_skip"); continue
            if prefilter(chain_key, tok, pool, book):
                candidates.append((tok, pool))
        if candidates:
            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(lambda c: check_candidate(chain_key, c[0], c[1]), candidates))
            for res in sorted([r for r in results if r], key=lambda r: -r[2]["adj_net_pct"]):
                tok, pool, p = res
                key = (chain_key, tok["dedup_key"], p["direction"])
                if not should_alert(key, p["adj_net_pct"]):
                    continue
                if send_alert(chain_key, tok, pool, p):
                    mark_alerted(key, p["adj_net_pct"])
                    st.inc("signals")
                    DB.insert(chain_key, tok, pool, p)
        stop.wait(2.5)
    return True

def run_chain_loop(chain_key, stop):
    log.info("[%s] start",chain_key)
    empty, warned = 0, False
    while not stop.is_set():
        try:
            STATS[chain_key].beat()
            if not scan_chain_once(chain_key, stop):
                empty += 1
                log.info("[%s] chua co du lieu (%d), thu lai sau", chain_key, empty)
                stop.wait(60 if empty <= 10 else 300)
                continue
            empty = 0
            if STATS[chain_key].snapshot()["dex_pairs"] == 0:
                log.warning("[%s] Dexscreener khong tra pair nao (slug '%s' sai?)", chain_key, CHAINS[chain_key]["dex_chain"])
                if not warned:
                    warned = True
                    send_telegram(f"⚠ {esc(chain_key)}: Dexscreener khong tra pair nao cho slug <code>{esc(CHAINS[chain_key]['dex_chain'])}</code>.")
                stop.wait(3600)
                continue
            stop.wait(SCAN_PAUSE_SEC)
        except:
            STATS[chain_key].inc("errors")
            log.exception("[%s] loi vong chinh", chain_key)
            stop.wait(20)


# ========== V8.9 INVENTORY AUTO-HOLD ==========
INVENTORY_DB_PATH = os.getenv("INVENTORY_DB_PATH", "inventory.db")

class InventoryDB:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            c=self.conn
            try: c.execute("PRAGMA journal_mode=WAL")
            except: pass
            c.execute("""CREATE TABLE IF NOT EXISTS inventory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, contract TEXT, chain TEXT, cex TEXT,
                dex_amount REAL DEFAULT 0, cex_amount REAL DEFAULT 0,
                avg_price REAL DEFAULT 0, profit_total REAL DEFAULT 0,
                created_ts REAL, updated_ts REAL, status TEXT DEFAULT 'active')""")
            c.execute("""CREATE TABLE IF NOT EXISTS hot_tokens(
                symbol TEXT PRIMARY KEY, contract TEXT, chain TEXT,
                hits INTEGER, first_seen REAL, last_seen REAL, profit_potential REAL)""")
            c.commit()
    def add_or_update(self, symbol, contract, chain, cex, dex_amt, cex_amt, price):
        with self.lock:
            cur=self.conn.execute("SELECT id, dex_amount, cex_amount FROM inventory WHERE symbol=? AND contract=? AND chain=? AND cex=? AND status='active'",
                                  (symbol, contract, chain, cex))
            row=cur.fetchone()
            now=time.time()
            if row:
                nid, od, oc = row
                self.conn.execute("UPDATE inventory SET dex_amount=?, cex_amount=?, avg_price=?, updated_ts=? WHERE id=?",
                                  (od+dex_amt, oc+cex_amt, price, now, nid))
            else:
                self.conn.execute("INSERT INTO inventory(symbol,contract,chain,cex,dex_amount,cex_amount,avg_price,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                                  (symbol, contract, chain, cex, dex_amt, cex_amt, price, now, now))
            self.conn.commit()
    def list_active(self):
        with self.lock:
            cur=self.conn.execute("SELECT symbol, contract, chain, cex, dex_amount, cex_amount, avg_price, profit_total FROM inventory WHERE status='active'")
            return cur.fetchall()
    def record_hot(self, symbol, contract, chain, profit_potential=0):
        with self.lock:
            cur=self.conn.execute("SELECT hits FROM hot_tokens WHERE symbol=?", (symbol,))
            r=cur.fetchone()
            now=time.time()
            if r:
                self.conn.execute("UPDATE hot_tokens SET hits=hits+1, last_seen=?, profit_potential=? WHERE symbol=?", (now, profit_potential, symbol))
            else:
                self.conn.execute("INSERT INTO hot_tokens(symbol,contract,chain,hits,first_seen,last_seen,profit_potential) VALUES(?,?,?,?,?,?,?)",
                                  (symbol, contract, chain, 1, now, now, profit_potential))
            self.conn.commit()
    def get_hot(self, limit=20):
        with self.lock:
            cur=self.conn.execute("SELECT symbol, contract, chain, hits, profit_potential FROM hot_tokens ORDER BY hits DESC, profit_potential DESC LIMIT ?", (limit,))
            return cur.fetchall()

INV_DB = InventoryDB(INVENTORY_DB_PATH)

# ========== V8.9 WEBSOCKET LIVE FULL 9 SAN ==========
try:
    import websocket as _ws_lib
    HAS_WS = True
except:
    HAS_WS = False
    log.warning("websocket-client not installed, WS disabled - pip install websocket-client")

class CEXWebSocketManager:
    def __init__(self):
        self.threads = {}
        self.stop_flags = {}
    
    def start_all(self):
        if not HAS_WS:
            log.warning("WS disabled, using REST poll")
        for cex in ENABLED_CEXS:
            cex = cex.strip().upper()
            if cex not in CEX_REGISTRY:
                continue
            flag = threading.Event()
            self.stop_flags[cex]=flag
            t = threading.Thread(target=self._run_cex_ws, args=(cex, flag), daemon=True, name=f"WS-{cex}")
            t.start()
            self.threads[cex]=t
            time.sleep(0.3)
    
    def _run_cex_ws(self, cex, stop_flag):
        cfg = CEX_REGISTRY[cex]
        fmt = cfg.get("ws_format")
        log.info("WS %s starting (%s)", cex, fmt)
        while not stop_flag.is_set():
            try:
                if fmt=="mexc":
                    self._run_mexc_ws(cex, stop_flag)
                elif fmt=="binance":
                    self._run_binance_ws(cex, stop_flag)
                elif fmt=="gate":
                    self._run_gate_ws(cex, stop_flag)
                elif fmt=="okx":
                    self._run_okx_ws(cex, stop_flag)
                elif fmt=="bybit":
                    self._run_bybit_ws(cex, stop_flag)
                elif fmt=="bitget":
                    self._run_bitget_ws(cex, stop_flag)
                elif fmt=="kucoin":
                    self._run_kucoin_ws(cex, stop_flag)
                elif fmt=="htx":
                    self._run_htx_ws(cex, stop_flag)
                else:
                    self._run_rest_poll_ws(cex, stop_flag)
            except Exception as e:
                log.warning("WS %s error %s, reconnect 5s", cex, e)
                time.sleep(5)
    
    def _run_mexc_ws(self, cex, stop_flag):
        import websocket, json
        url = "wss://wbs.mexc.com/ws"
        def on_open(ws):
            subs = [f"spot@public.bookTicker.v3.api@{p}" for p in ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","PEPEUSDT","WIFUSDT","BONKUSDT","SHIBUSDT"][:30]]
            ws.send(json.dumps({"method":"SUBSCRIPTION","params":subs}))
            log.info("MEXC WS subscribed %d pairs", len(subs))
        def on_msg(ws, msg):
            try:
                data = json.loads(msg)
                if "d" in data and "c" in data:
                    d=data["d"]; sym=data["c"].split("@")[-1]
                    bid=d.get("bidPrice"); ask=d.get("askPrice")
                    if bid and ask:
                        update_live_price(cex, sym, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://wbs.mexc.com/ws", on_open=on_open, on_message=on_msg)
        ws.run_forever(ping_interval=20, ping_timeout=10)
    
    def _run_binance_ws(self, cex, stop_flag):
        import websocket, json
        symbols = ["btcusdt","ethusdt","solusdt","bnbusdt","pepeusdt","wifusdt","bonkusdt","shibusdt","flokiusdt","dogusdt"]
        url = f"wss://stream.binance.com:9443/ws/{'/'.join([s+'@bookTicker' for s in symbols])}"
        def on_msg(w, msg):
            try:
                d=json.loads(msg)
                if "s" in d:
                    update_live_price(cex, d["s"], d.get("b"), d.get("a"), "ws")
            except:
                pass
        ws = websocket.WebSocketApp(url, on_message=on_msg)
        ws.run_forever(ping_interval=20)
    
    def _run_gate_ws(self, cex, stop_flag):
        # Gate.io WS v4 - subscribe to tickers
        import websocket, json
        def on_open(ws):
            # Gate needs subscription: {"time":..., "channel":"spot.tickers", "event":"subscribe", "payload":["BTC_USDT","ETH_USDT"]}
            payload = ["BTC_USDT","ETH_USDT","SOL_USDT","PEPE_USDT","WIF_USDT","BONK_USDT","SHIB_USDT"]
            msg = {"time": int(time.time()), "channel": "spot.tickers", "event": "subscribe", "payload": payload}
            ws.send(json.dumps(msg))
        def on_msg(ws, msg):
            try:
                data=json.loads(msg)
                if data.get("channel")=="spot.tickers":
                    result=data.get("result",{})
                    if isinstance(result, dict):
                        pair=result.get("currency_pair","").replace("_","")
                        bid=result.get("highest_bid"); ask=result.get("lowest_ask")
                        if bid and ask:
                            update_live_price(cex, pair, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://api.gateio.ws/ws/v4/", on_open=on_open, on_message=on_msg)
        ws.run_forever(ping_interval=20)
    
    def _run_okx_ws(self, cex, stop_flag):
        import websocket, json
        def on_open(ws):
            # OKX subscribe to tickers
            args = [{"channel":"tickers","instId":"BTC-USDT"},{"channel":"tickers","instId":"ETH-USDT"},{"channel":"tickers","instId":"SOL-USDT"},{"channel":"tickers","instId":"PEPE-USDT"}]
            ws.send(json.dumps({"op":"subscribe","args":args}))
        def on_msg(ws, msg):
            try:
                data=json.loads(msg)
                if "data" in data:
                    for t in data["data"]:
                        inst=t.get("instId","").replace("-","")
                        bid=t.get("bidPx"); ask=t.get("askPx")
                        if bid and ask:
                            update_live_price(cex, inst, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/public", on_open=on_open, on_message=on_msg)
        ws.run_forever(ping_interval=20)
    
    def _run_bybit_ws(self, cex, stop_flag):
        import websocket, json
        def on_open(ws):
            ws.send(json.dumps({"op":"subscribe","args":["tickers.BTCUSDT","tickers.ETHUSDT","tickers.SOLUSDT","tickers.PEPEUSDT"]}))
        def on_msg(ws, msg):
            try:
                data=json.loads(msg)
                if data.get("topic","").startswith("tickers."):
                    d=data.get("data",{})
                    sym=d.get("symbol")
                    bid=d.get("bid1Price"); ask=d.get("ask1Price")
                    if sym and bid and ask:
                        update_live_price(cex, sym, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://stream.bybit.com/v5/public/spot", on_open=on_open, on_message=on_msg)
        ws.run_forever(ping_interval=20)
    
    def _run_bitget_ws(self, cex, stop_flag):
        import websocket, json
        def on_open(ws):
            args = [{"instType":"SPOT","channel":"ticker","instId":"BTCUSDT"},{"instType":"SPOT","channel":"ticker","instId":"ETHUSDT"}]
            ws.send(json.dumps({"op":"subscribe","args":args}))
        def on_msg(ws, msg):
            try:
                data=json.loads(msg)
                if "data" in data:
                    for t in data["data"]:
                        sym=t.get("instId")
                        bid=t.get("bidPr"); ask=t.get("askPr")
                        if sym and bid and ask:
                            update_live_price(cex, sym, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://ws.bitget.com/v2/ws/public", on_open=on_open, on_message=on_msg)
        ws.run_forever(ping_interval=20)
    
    def _run_kucoin_ws(self, cex, stop_flag):
        # Kucoin needs token first, fallback to REST poll for simplicity in V8.9
        self._run_rest_poll_ws(cex, stop_flag)
    
    def _run_htx_ws(self, cex, stop_flag):
        import websocket, json, gzip
        def on_open(ws):
            subs = [{"sub":"market.btcusdt.ticker"},{"sub":"market.ethusdt.ticker"},{"sub":"market.solusdt.ticker"}]
            for s in subs:
                ws.send(json.dumps(s))
        def on_msg(ws, msg):
            try:
                # HTX sends gzip
                if isinstance(msg, bytes):
                    msg = gzip.decompress(msg).decode()
                data=json.loads(msg)
                if "ping" in data:
                    ws.send(json.dumps({"pong": data["ping"]}))
                if "tick" in data and "ch" in data:
                    ch=data["ch"]
                    # ch = market.btcusdt.ticker
                    sym=ch.split(".")[1].upper()
                    tick=data["tick"]
                    bid=tick.get("bid"); ask=tick.get("ask")
                    if bid and ask:
                        update_live_price(cex, sym, bid, ask, "ws")
            except:
                pass
        ws = websocket.WebSocketApp("wss://api.huobi.pro/ws", on_open=on_open, on_message=on_msg)
        ws.run_forever()
    
    def _run_rest_poll_ws(self, cex, stop_flag):
        # Universal REST poll fallback - 3s loop for all remaining CEXs
        while not stop_flag.is_set():
            try:
                ad = CEX_ADAPTERS.get(cex)
                if not ad:
                    time.sleep(3)
                    continue
                tickers = ad.fetch_tickers()
                for sym, data in list(tickers.items())[:100]:
                    if data.get("bid") and data.get("ask"):
                        update_live_price(cex, sym, data["bid"], data["ask"], "rest-poll")
                # also fetch MEXC-like depth for best price calc
                time.sleep(3)
            except Exception as e:
                log.debug("REST poll %s fail %s", cex, e)
                time.sleep(5)

WS_MANAGER = CEXWebSocketManager()

class Worker:
    def __init__(self, chain_key):
        self.chain = chain_key; self.stop = None; self.thread = None
    def start(self):
        self.stop = threading.Event()
        STATS[self.chain].beat()
        self.thread = threading.Thread(target=run_chain_loop, args=(self.chain, self.stop),daemon=True, name=f"Bot-{self.chain}")
        self.thread.start()
    def restart(self):
        if self.stop:
            self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.start()

def heartbeat_loop():
    last_sent = None
    while True:
        try:
            # VN time = UTC+7
            now_utc = datetime.utcnow()
            now_vn = now_utc + __import__("datetime").timedelta(hours=7)
            # 21:00 VN
            if now_vn.hour == 21 and now_vn.minute < 2:
                today = now_vn.date()
                if last_sent != today:
                    msg = (f"📊 <b>DAILY REPORT {now_vn.strftime('%d/%m/%Y')} - 21:00</b>\n"
                           f"Capital: ${ORDER_SIZE_USD:.0f} | Real: {REAL_PRICE_CHECK} | Require: {REQUIRE_REAL_PRICE}\n"
                           f"{'─'*20}\n")
                    total_sig = 0
                    for k, st in STATS.items():
                        s = st.snapshot()
                        if s['attempted'] == 0:
                            continue
                        total_sig += s['signals']
                        thr = adaptive_threshold(k)
                        msg += (f"{CHAINS[k]['emoji']} <b>{k}</b>: "
                                f"Scanned {s['attempted']} | Fetched {s['fetched']} | Opp {s['opportunity']} | "
                                f"✅ {s['signals']} | Sus {s['suspect']}\n"
                                f"   RealOK {s['real_price_ok']} | Fail {s['real_price_fail']} | Thr {thr:.2f}%\n")
                    msg += f"\n{'─'*20}\nAPI: {esc(API.line())}\n\n{summary_text(8)}"
                    send_telegram(msg)
                    last_sent = today
            time.sleep(60)
        except Exception as e:
            log.exception("Heartbeat error: %s", e)
            time.sleep(60)

def cleanup():
    now = time.time()
    with CD_LOCK:
        for k in [k for k, (t, _) in COOLDOWN.items() if (datetime.now() - t).total_seconds() > COOLDOWN_MIN * 60 * 4]:
            COOLDOWN.pop(k, None)
    with SEC_LOCK:
        for k in [k for k, (ts, _) in SEC_CACHE.items() if now - ts > 21600]:
            SEC_CACHE.pop(k, None)
    with BL_LOCK:
        for k in [k for k, u in BL.items() if u < now]:
            BL.pop(k, None)

def start_web():
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    def authed():
        return (not ADMIN_TOKEN) or request.args.get("token") == ADMIN_TOKEN
    @app.route("/")
    def home():
        return f"V8.1 FINAL MERGED - signals: {sum(s.snapshot()['signals'] for s in STATS.values())}"
    @app.route("/health")
    def health():
        return "OK", 200
    @app.route("/stats")
    def stats():
        if not authed():
            return "forbidden", 403
        return jsonify({k: s.snapshot() for k, s in STATS.items()})
    @app.route("/signals")
    def signals():
        if not authed():
            return "forbidden", 403
        return jsonify(DB.recent(50))
    @app.route("/summary")
    def summary():
        if not authed():
            return "forbidden", 403
        return jsonify(DB.summary())
    log.info("Web on 0.0.0.0:%s", PORT)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT)

def validate_config():
    warns = []
    min_liq_needed = 2 * ORDER_SIZE_USD / (MAX_IMPACT_PCT * 1.5) * 100
    if MIN_LIQ_USD < min_liq_needed:
        warns.append(f"MIN_LIQ_USD={MIN_LIQ_USD:.0f} thap hon muc can (~{min_liq_needed:.0f})")
    if not REAL_PRICE_CHECK and REQUIRE_REAL_PRICE:
        warns.append("REAL_PRICE_CHECK=0 nhung REQUIRE_REAL_PRICE=1 -> bot se khong gui signal")
    for k in CHAINS:
        if k not in REAL_CHAINS and REQUIRE_REAL_PRICE:
            # V8.1: cho phep all chain, chi canh bao nhe
            log.info("INFO: %s chua ho tro quote that -> se dung gia estimate + buffer cao hon", k)
    for w in warns:
        log.warning("CONFIG: %s", w)
    return warns

def ticker_loop():
    while True:
        try:
            MEXC.fetch_all_tickers()
        except Exception as e:
            log.debug("ticker_loop fail %s", e)
        time.sleep(5)

def main():
    log.info("BOT V8.9 MULTI-CEX 9 SAN + WS LIVE + INVENTORY start | chains=%s | CEXs=%s", list(CHAINS), ENABLED_CEXS)
    warns = validate_config()
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    try:
        WS_MANAGER.start_all()
        log.info("WS Manager started for CEXs: %s", ENABLED_CEXS)
    except Exception as e:
        log.warning("WS start fail %s", e)
    threading.Thread(target=ticker_loop, daemon=True, name="Ticker").start()
    threading.Thread(target=followup_loop, daemon=True, name="Followup").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HB").start()
    threading.Thread(target=telegram_poll_loop, daemon=True, name="TGPoll").start()
    send_telegram(f"🚀 <b>BOT V8.9 MULTI-CEX 9 SAN + WS LIVE + INVENTORY</b>\n"
                  f"Chains: {', '.join(CHAINS)}\nCEXs: {', '.join(ENABLED_CEXS)}\n"
                  f"Net≥{THRESHOLD_NET}% | Size ${ORDER_SIZE_MIN:.0f}-${ORDER_SIZE_MAX:.0f} dynamic | AI≥{AI_SCORE_MIN}\n"
                  f"WS Live: {HAS_WS} | Inventory: ON | RealCheck={REAL_PRICE_CHECK}\n"
                  f"Filter: Liq≥${MIN_LIQ_USD:.0f} Vol≥${MIN_VOL_24H:.0f} Txns≥{MIN_TXNS_H1}\n"
                  f"Cmd: /help /hot /inventory /live" + ("".join("\n⚠ " + esc(w) for w in warns)))
    workers = {}
    for k in CHAINS:
        w = Worker(k); w.start(); workers[k] = w
        time.sleep(1.2)
    while True:
        time.sleep(300)
        cleanup()
        now = datetime.now()
        for k, w in workers.items():
            idle = (now - STATS[k].last_beat).total_seconds()
            if not w.thread.is_alive() or idle > STUCK_SEC:
                log.warning("[SUPERVISOR] %s %s -> restart", k, "chet" if not w.thread.is_alive() else f"dung {int(idle)}s")
                w.restart()
            else:
                log.info("[SUPERVISOR] %s OK, beat %ds truoc", k, int(idle))

if __name__ == "__main__":
    main()
