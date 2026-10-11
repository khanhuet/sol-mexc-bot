#!/usr/bin/env python3
"""
BOT V8.1 FINAL - MERGED - MEXC <-> DEX arbitrage (PAPER)
Base: V8.0 user (decimals via RPC, risk buffer, adaptive, blacklist, telegram commands)
+ Fixes: WSOL for SOL native, LRU cache, strict symbol_ok, circuit breaker
"""
import hashlib, hmac, html, json, logging, math, os, re, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlencode
import requests

def require_env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Thieu bien moi truong: {name}")
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
ORDER_SIZE_USD = env_float("ORDER_SIZE_USD", 500)
THRESHOLD_NET = env_float("THRESHOLD_NET", 0.8)
PREFILTER_GROSS = env_float("PREFILTER_GROSS", 0.8)
MAX_GROSS = env_float("MAX_GROSS", 7.0)
MAX_IMPACT_PCT = env_float("MAX_IMPACT_PCT", 0.8)
MIN_LIQ_USD = env_float("MIN_LIQ_USD", 80000)
MIN_VOL_24H = env_float("MIN_VOL_24H", 40000)
MIN_TXNS_H1 = int(env_float("MIN_TXNS_H1", 15))
MIN_POOL_AGE_H = env_float("MIN_POOL_AGE_H", 24)
MAX_MEXC_SPREAD = env_float("MAX_MEXC_SPREAD", 2.0)
MEXC_TAKER_FEE = env_float("MEXC_TAKER_FEE", 0.002)
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
DEX_LIMITER = RateLimiter(env_float("DEX_LIMIT_SEC", 1.5))
JUP_LIMITER = RateLimiter(0.25)
PARA_LIMITER = RateLimiter(0.3)
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
                if s["consec"] >= 15 and time.time() - self.alerted.get(name, 0) > 1800:
                    self.alerted[name] = time.time(); need = True
        if need:
            send_telegram(f"🚨 API <b>{esc(name)}</b> loi lien tuc {s['consec']} lan.")
    def line(self):
        with self.lock:
            return " | ".join(f"{k}: {v['ok']}ok/{v['fail']}fail" for k, v in self.d.items()) or "chua co"
API = ApiHealth()

class MexcData:
    def __init__(self):
        self.lock = threading.Lock()
        self.book_lock = threading.Lock()
        self.pairs = {}
        self.tokens = {}
        self.status = {}
        self.ts = 0
        self.book = {}
        self.book_ts = 0
        self.retry_after = 0
    def _fetch_pairs(self):
        r = http_get(f"{MEXC_BASE}/api/v3/exchangeInfo", limiter=MEXC_LIMITER, timeout=20)
        if not r:
            raise RuntimeError("exchangeInfo no response")
        if r.status_code != 200:
            raise RuntimeError(f"exchangeInfo fail {r.status_code} {r.text[:200]}")
        out = {}
        for s in r.json().get("symbols", []):
            if s.get("quoteAsset") == "USDT" and str(s.get("status")) == "1":
                out[s["symbol"]] = s["baseAsset"]
        log.info("Lay duoc %d pairs", len(out))
        return out
    def _fetch_tokens(self, pairs):
        r = mexc_signed_get("/api/v3/capital/config/getall")
        if not r:
            raise RuntimeError("capital/config no response")
        if r.status_code != 200:
            raise RuntimeError(f"capital/config fail {r.status_code} {r.text[:300]}")
        base_set = set(pairs.values())
        sym2pair = {}
        for p, s in pairs.items():
            sym2pair.setdefault(s, p)
        result = {k: {} for k in CHAINS}
        unmatched = set()
        for coin in r.json():
            sym = coin.get("coin")
            if sym not in base_set:
                continue
            for net in coin.get("networkList", []) or []:
                net_name = re.sub(r"[^A-Z0-9]", "", (net.get("network") or "").upper())
                contract = (net.get("contract") or "").strip()
                # FIX V8.1: SOL native
                if net_name in {"SOL","SOLANA"} and (not contract or contract.upper()=="SOL"):
                    contract = WSOL
                found = next((ch for ch, cfg in CHAINS.items() if net_name in cfg["aliases"]), None)
                if not found:
                    unmatched.add(net_name); continue
                if not valid_contract(found, contract):
                    continue
                dedup = norm_addr(found, contract)
                if dedup in result[found]:
                    continue
                result[found][dedup] = {
                    "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": found,
                    "pair": sym2pair.get(sym, sym + "USDT"), "raw_network": net.get("network"),
                    "withdraw_fee": fnum(net.get("withdrawFee")), "withdraw_min": fnum(net.get("withdrawMin")),
                    "withdraw_enable": bool(net.get("withdrawEnable")),
                    "deposit_enable": bool(net.get("depositEnable")),
                }
        final = {k: list(v.values()) for k, v in result.items()}
        for k, v in final.items():
            log.info("MEXC %s: %d token hop le", k, len(v))
        if unmatched:
            log.info("MEXC unmatched sample: %s", list(unmatched)[:20])
        return final
    def ensure_static(self):
        changes = []
        with self.lock:
            now = time.time()
            if self.tokens and now - self.ts < STATIC_TTL:
                return
            if now < self.retry_after:
                return
            try:
                pairs = self._fetch_pairs()
                tokens = self._fetch_tokens(pairs)
                new_status = {}
                for ch, lst in tokens.items():
                    for t in lst:
                        new_status[(ch, t["dedup_key"])] = (t["withdraw_enable"], t["deposit_enable"], t["symbol"])
                if self.status:
                    for k, (w, d, sym) in new_status.items():
                        old = self.status.get(k)
                        if old and ((not old[0] and w) or (not old[1] and d)):
                            changes.append(f"{sym} [{k[0]}] nap {'✅' if d else '❌'} rut {'✅' if w else '❌'}")
                self.status = new_status
                self.pairs, self.tokens, self.ts = pairs, tokens, now
            except Exception as e:
                log.error("Lam moi MEXC loi: %s", e)
                self.retry_after = now + 60
        if changes:
            send_telegram("🔓 <b>MEXC vua MO LAI nap/rut</b>:\n" + "\n".join(esc(c) for c in changes[:20]))
    def get_book(self):
        with self.book_lock:
            if self.book and time.time() - self.book_ts < BOOK_TTL:
                return self.book
            r = http_get(f"{MEXC_BASE}/api/v3/ticker/bookTicker", limiter=MEXC_LIMITER, timeout=20)
            if r and r.status_code == 200:
                try:
                    book = {}
                    for t in r.json():
                        bid, ask = fnum(t.get("bidPrice")), fnum(t.get("askPrice"))
                        if 0 < bid < ask:
                            book[t["symbol"]] = (bid, ask)
                    self.book, self.book_ts = book, time.time()
                except Exception as e:
                    log.warning("bookTicker parse loi: %s", e)
            return self.book

MEXC = MexcData()

def mexc_depth(pair):
    r = http_get(f"{MEXC_BASE}/api/v3/depth", params={"symbol": pair, "limit": 100}, limiter=MEXC_LIMITER, timeout=12)
    if not r or r.status_code != 200:
        return None
    try:
        j = r.json()
        bids = [(float(p), float(q)) for p, q in j.get("bids", []) if float(p) > 0 and float(q) > 0]
        asks = [(float(p), float(q)) for p, q in j.get("asks", []) if float(p) > 0 and float(q) > 0]
    except:
        return None
    if len(bids) < 5 or len(asks) < 5:
        return None
    if asks[0][0] <= bids[0][0] or (asks[0][0] - bids[0][0]) / asks[0][0] > 0.05:
        return None
    return {"bids": bids, "asks": asks}

def walk_usd(levels, usd):
    left, tok = usd, 0.0
    for p, q in levels:
        take = min(left, p * q)
        tok += take / p
        left -= take
        if left <= 1e-9:
            break
    return (usd / tok, tok) if left <= 0.01 and tok > 0 else None

def walk_tokens(levels, tokens):
    left, usd = tokens, 0.0
    for p, q in levels:
        take = min(left, q)
        usd += take * p
        left -= take
        if left <= 1e-12:
            break
    return usd if left <= tokens * 1e-6 else None

def fetch_dex_pools(chain_key, addrs):
    cid = CHAINS[chain_key]["dex_chain"]
    r = http_get(f"https://api.dexscreener.com/tokens/v1/{cid}/{','.join(addrs)}", limiter=DEX_LIMITER)
    API.report("dexscreener", bool(r and r.status_code == 200))
    if not r or r.status_code != 200:
        return None
    try:
        pairs = r.json()
    except:
        return None
    if not isinstance(pairs, list):
        return None
    STATS[chain_key].inc("dex_pairs", sum(1 for p in pairs if p.get("chainId") == cid))
    best = {}
    now_ms = time.time() * 1000
    for p in pairs:
        if p.get("chainId") != cid:
            continue
        base = p.get("baseToken") or {}
        key = norm_addr(chain_key, base.get("address"))
        price = fnum(p.get("priceUsd"))
        liq = fnum((p.get("liquidity") or {}).get("usd"))
        vol = fnum((p.get("volume") or {}).get("h24"))
        h1 = (p.get("txns") or {}).get("h1") or {}
        txns = int(fnum(h1.get("buys")) + fnum(h1.get("sells")))
        fdv = fnum(p.get("fdv"))
        created = fnum(p.get("pairCreatedAt"))
        age_h = (now_ms - created) / 3.6e6 if created > 0 else 9999
        if price <= 0 or liq < MIN_LIQ_USD or vol < MIN_VOL_24H or txns < MIN_TXNS_H1:
            continue
        if age_h < MIN_POOL_AGE_H:
            continue
        if vol / liq < 0.05:
            continue
        if fdv > 0 and fdv / liq > 100:
            continue
        score = liq
        if key in best and best[key].get("score",0) >= score:
            continue
        dex_id = (p.get("dexId") or "").lower()
        pc = p.get("priceChange") or {}
        best[key] = {"price": price, "liq": liq, "vol24": vol, "txns_h1": txns, "dex": dex_id,
                     "fee": DEX_FEES.get(dex_id, DEFAULT_DEX_FEE), "url": p.get("url") or "",
                     "pair_name": f"{base.get('symbol')}/{(p.get('quoteToken') or {}).get('symbol')}",
                     "base_symbol": base.get("symbol") or "", "quote_addr": (p.get("quoteToken") or {}).get("address") or "",
                     "fdv": fdv, "score": score, "chg_m5": fnum(pc.get("m5")), "chg_h1": fnum(pc.get("h1")), "age_h": age_h}
    return best

def symbol_ok(a, b):
    if not a or not b:
        return False
    ca = re.sub(r"[^A-Z0-9]","",a.upper())
    cb = re.sub(r"[^A-Z0-9]","",b.upper())
    if not ca or not cb:
        return False
    if ca==cb:
        return True
    if len(ca)<4 or len(cb)<4:
        return False
    if (ca.startswith(cb) or ca.endswith(cb) or cb.startswith(ca) or cb.endswith(ca)) and abs(len(ca)-len(cb))<=3:
        return True
    return False

DEC_CACHE = {}
DEC_LOCK = threading.Lock()
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
    return None

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
    tm = CHAINS[chain_key].get("transfer_min", 5)
    move = max(abs(pool.get("chg_m5", 0)) * math.sqrt(tm / 5.0), abs(pool.get("chg_h1", 0)) * math.sqrt(tm / 60.0))
    buf = RISK_FLOOR + RISK_K * move
    buf += SOFT_FLAG_BUFFER * len(sec.get("soft", []))
    if not sec.get("known") and CHAINS[chain_key].get("evm"):
        buf += UNKNOWN_SEC_BUFFER
    return buf

def evaluate(chain_key, tok, pool, depth):
    cfg = CHAINS[chain_key]
    st = STATS[chain_key]
    usd, gas = ORDER_SIZE_USD, cfg["gas_usd"]
    bids, asks = depth["bids"], depth["asks"]
    if (asks[0][0] - bids[0][0]) / asks[0][0] * 100 > MAX_MEXC_SPREAD:
        return []
    sec = get_security(chain_key, tok["contract"])
    if sec["bad"] or sec["buy_tax"] > TAX_MAX or sec["sell_tax"] > TAX_MAX:
        st.inc("suspect")
        log.info("LOAI SECURITY %s %s bad=%s buy %.1f%% sell %.1f%%", chain_key, tok["symbol"], sec["bad"],
                 sec["buy_tax"] * 100, sec["sell_tax"] * 100)
        return []
    buy_tax, sell_tax = sec["buy_tax"], sec["sell_tax"]
    dec = get_decimals(chain_key, tok["contract"]) if (REAL_PRICE_CHECK and chain_key in REAL_CHAINS) else None
    can_real = dec is not None
    if not can_real and REQUIRE_REAL_PRICE:
        st.inc("real_price_fail")
        return []
    impact_est, buy_est, sell_est = dex_prices_estimate(pool)
    if not can_real and impact_est > MAX_IMPACT_PCT * 1.5:
        return []
    buf = risk_buffer(chain_key, pool, sec)
    common = {"mexc_best": (bids[0][0], asks[0][0]), "buy_tax": buy_tax, "sell_tax": sell_tax,
              "withdraw_enable": tok.get("withdraw_enable"), "deposit_enable": tok.get("deposit_enable"),
              "risk_buf": buf, "sec_known": sec["known"], "sec_soft": sec["soft"], "decimals": dec}
    out = []
    if tok.get("withdraw_enable", True):
        w = walk_usd(asks, usd)
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
                elif REQUIRE_REAL_PRICE:
                    st.inc("real_price_fail")
                    usd_out = None
                else:
                    usd_out, impact, src = tokens_net * sell_est, impact_est, "estimate"
                if usd_out:
                    sell_px = usd_out / tokens_net
                    gross = (sell_px / avg - 1) * 100
                    cost = usd * (1 + MEXC_TAKER_FEE)
                    revenue = usd_out * (1 - EXTRA_SLIPPAGE) * (1 - sell_tax) - gas
                    net = revenue - cost
                    net_pct = net / cost * 100
                    if -5 < gross <= MAX_GROSS and impact <= MAX_IMPACT_PCT:
                        if abs(sell_px - pool["price"]) / pool["price"] > 0.15:
                            st.inc("suspect")
                        else:
                            out.append(dict(common, direction="MEXC_TO_DEX", token_amount=tokens,
                                            token_after_tax=tokens_net * (1 - sell_tax), mexc_avg=avg,
                                            dex_exec=sell_px, cost=cost, net_usd=net, net_pct=net_pct,
                                            adj_net_pct=net_pct - buf, gross_pct=gross,
                                            gross_after_tax=gross - sell_tax * 100, gas=gas,
                                            withdraw_fee_usd=tok["withdraw_fee"] * avg, impact_dex=impact,
                                            real_source=src))
    if tok.get("deposit_enable", True):
        q = dex_buy_quote(chain_key, tok["contract"], dec, usd) if can_real else None
        if q:
            st.inc("real_price_ok")
            tokens_out, buy_px, impact, src = q["tokens"], q["price"], q["impact"], q["source"]
            if impact is None:
                impact = impact_est
        elif REQUIRE_REAL_PRICE:
            st.inc("real_price_fail")
            tokens_out = None
        else:
            buy_px, impact, src = buy_est, impact_est, "estimate"
            tokens_out = usd / buy_est
        if tokens_out:
            tokens_dep = tokens_out * (1 - EXTRA_SLIPPAGE) * (1 - buy_tax)
            proceeds = walk_tokens(bids, tokens_dep)
            if proceeds:
                cost = usd + gas * 2
                revenue = proceeds * (1 - MEXC_TAKER_FEE)
                net = revenue - cost
                avg = proceeds / tokens_dep
                gross = (avg / buy_px - 1) * 100
                net_pct = net / cost * 100
                if -5 < gross <= MAX_GROSS and impact <= MAX_IMPACT_PCT:
                    if abs(buy_px - pool["price"]) / pool["price"] > 0.15:
                        st.inc("suspect")
                    else:
                        out.append(dict(common, direction="DEX_TO_MEXC", token_amount=tokens_dep,
                                        token_raw=tokens_out, mexc_avg=avg, dex_exec=buy_px, cost=cost,
                                        net_usd=net, net_pct=net_pct, adj_net_pct=net_pct - buf,
                                        gross_pct=gross, gross_after_tax=gross - buy_tax * 100, gas=gas * 2,
                                        withdraw_fee_usd=0.0, impact_dex=impact, real_source=src))
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
        log.warning("Follow-up loi: %s", e)
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
            log.warning("Telegram that bai %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram loi: %s", e)
        return False

def send_alert(chain_key, tok, pool, p):
    cfg = CHAINS[chain_key]
    if p["direction"] == "MEXC_TO_DEX":
        action = f"MUA MEXC {tok['pair']} -> rut ve {chain_key} -> BAN tren DEX"
    else:
        action = f"MUA tren {chain_key} DEX -> nap len MEXC -> BAN {tok['pair']}"
    bid, ask = p["mexc_best"]
    tax_str = f"Tax: buy {p['buy_tax']*100:.1f}% / sell {p['sell_tax']*100:.1f}% | " if (p["buy_tax"] or p["sell_tax"]) else ""
    en = ""
    if p.get("withdraw_enable") is not None and p.get("deposit_enable") is not None:
        en = f"Nap: {'✅' if p['deposit_enable'] else '❌'} Rut: {'✅' if p['withdraw_enable'] else '❌'} | "
    warn = []
    if not p.get("sec_known") and cfg.get("evm"):
        warn.append("chua co du lieu security")
    if p.get("sec_soft"):
        warn.append("co: " + ",".join(p["sec_soft"]))
    warn_line = f"⚠ {esc('; '.join(warn))}\n" if warn else ""
    thr = adaptive_threshold(chain_key)
    msg = (f"{cfg['emoji']} <b>Arbitrage {esc(tok['symbol'])} [{chain_key}]</b> (paper) [{esc(p.get('real_source','?'))}]\n\n"
           f"Contract: <code>{esc(tok['contract'])}</code>\n"
           f"DEX: {esc(pool['dex'])} | {esc(pool['pair_name'])} | fee ~{pool['fee']*100:.2f}% | tuoi pool {pool.get('age_h',0):.0f}h\n"
           f"Liq: ${pool['liq']:,.0f} | Vol24h: ${pool['vol24']:,.0f} | Txns1h: {pool['txns_h1']} | FDV ${pool.get('fdv',0):,.0f}\n"
           f"Bien dong: 5m {pool.get('chg_m5',0):+.1f}% | 1h {pool.get('chg_h1',0):+.1f}%\n\n"
           f"MEXC {esc(tok['pair'])}: bid/ask {fmt_price(bid)}/{fmt_price(ask)} | gia TB ${fmt_price(p['mexc_avg'])}\n"
           f"DEX: gia DS ${fmt_price(pool['price'])} | gia thuc thi ${fmt_price(p['dex_exec'])} | impact ~{p['impact_dex']:.2f}%\n"
           f"{tax_str}{en}Gross after tax {p['gross_after_tax']:.2f}%\n\n"
           f"💰 <b>NET {p['net_pct']:.3f}% (${p['net_usd']:.2f})</b> | Gross {p['gross_pct']:.3f}%\n"
           f"🛡 Net sau risk buffer: <b>{p['adj_net_pct']:.3f}%</b> (buffer {p['risk_buf']:.2f}%, nguong {thr:.2f}%)\n"
           f"Von ${ORDER_SIZE_USD:.0f} | Token {fmt_token_amount(p['token_amount'])}"
           f"{' -> '+fmt_token_amount(p['token_after_tax'])+' after tax' if 'token_after_tax' in p else ''}\n"
           f"Gas ${p['gas']:.2f} | Phi rut ${p['withdraw_fee_usd']:.2f} | Phi MEXC {MEXC_TAKER_FEE*100:.2f}%\n"
           f"{warn_line}🎯 {esc(action)}\n\n"
           f"🔗 MEXC: https://www.mexc.com/exchange/{esc(tok['pair'])}\n"
           f"🔗 DEX: {esc(pool['url'])}\n"
           f"🔗 Explorer: {esc(cfg['explorer'] + tok['contract'])}\n\n"
           f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')} | Gia co the doi, chuyen token mat ~{cfg['transfer_min']}+ phut.")
    return send_telegram(msg)

def summary_text(limit=12):
    rows = DB.summary()
    if not rows:
        return "Chua co du lieu."
    lines = ["<b>Thong ke (500 signal gan nhat)</b>"]
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
        return "/status /stats /top /pause CHAIN /resume CHAIN /blacklist SYMBOL /unblacklist SYMBOL"
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
            return "Chua co signal."
        out = ["<b>10 signal gan nhat</b>"]
        for r in rows:
            f5 = f"{r['net_pct_5m']:+.2f}%" if r["net_pct_5m"] is not None else (r["st_5m"] or "...")
            out.append(f"#{r['id']} {esc(r['symbol'])}[{r['chain']}] adj {r['adj_net_pct'] or 0:.2f}% → 5m: {f5}")
        return "\n".join(out)
    if cmd == "/pause":
        if arg in CHAINS:
            PAUSED.add(arg); return f"Da pause {arg}"
        return "Chain khong hop le: " + ", ".join(CHAINS)
    if cmd == "/resume":
        PAUSED.discard(arg); return f"Da resume {arg}"
    if cmd == "/blacklist" and arg:
        until = time.time() + 7 * 86400
        with BL_LOCK:
            BL[f"SYM:{arg}"] = until
        DB.save_blacklist(f"SYM:{arg}", until, "manual")
        return f"Da blacklist {esc(arg)} 7 ngay"
    if cmd == "/unblacklist" and arg:
        with BL_LOCK:
            BL.pop(f"SYM:{arg}", None)
        DB.del_blacklist(f"SYM:{arg}")
        return f"Da go blacklist {esc(arg)}"
    return "Lenh khong hop le. /help"

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
                    log.exception("Lenh loi")
        except Exception as e:
            log.warning("Poll Telegram loi: %s", e)
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
    while True:
        time.sleep(1800)
        try:
            msg = (f"💓 <b>HEARTBEAT {datetime.now().strftime('%H:%M %d/%m')}</b>\nOrder ${ORDER_SIZE_USD:.0f} | "
                   f"RealCheck={REAL_PRICE_CHECK} | RequireReal={REQUIRE_REAL_PRICE}\n\n")
            for k, st in STATS.items():
                s = st.snapshot()
                msg += (f"{CHAINS[k]['emoji']} {k}: try={s['attempted']} fetch={s['fetched']} opp={s['opportunity']} "
                        f"sig={s['signals']} sus={s['suspect']} realOK={s['real_price_ok']} realFail={s['real_price_fail']} "
                        f"bl={s['blacklisted_skip']} err={s['errors']} thr={adaptive_threshold(k):.2f}\n")
            msg += "\nAPI: " + esc(API.line()) + "\n\n" + summary_text(8)
            send_telegram(msg)
        except:
            log.exception("Heartbeat loi")

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
    log.info("Web tren 0.0.0.0:%s", PORT)
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
            warns.append(f"{k} chua ho tro quote that -> bi chan boi REQUIRE_REAL_PRICE")
    for w in warns:
        log.warning("CONFIG: %s", w)
    return warns

def main():
    log.info("BOT V8.1 FINAL MERGED start | chains=%s | REAL_CHECK=%s", list(CHAINS), REAL_PRICE_CHECK)
    warns = validate_config()
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    threading.Thread(target=followup_loop, daemon=True, name="Followup").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HB").start()
    threading.Thread(target=telegram_poll_loop, daemon=True, name="TGPoll").start()
    send_telegram(f"🚀 <b>BOT V8.1 FINAL MERGED START</b> (paper)\nChains: {', '.join(CHAINS)}\nNet(sau buffer) ≥ {THRESHOLD_NET}% | "
                  f"Order ${ORDER_SIZE_USD:.0f} | RealCheck={REAL_PRICE_CHECK} | Adaptive={ADAPTIVE}\n"
                  f"Filter: Liq≥${MIN_LIQ_USD:.0f} Vol≥${MIN_VOL_24H:.0f} Txns≥{MIN_TXNS_H1}\nLenh: /help" + ("".join("\n⚠ " + esc(w) for w in warns)))
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
