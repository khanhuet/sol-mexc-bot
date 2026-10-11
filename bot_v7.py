#!/usr/bin/env python3
"""
BOT V7 - MEXC <-> DEX arbitrage scanner (PAPER TRADING ONLY)

Biến môi trường BẮT BUỘC:
    TELEGRAM_TOKEN, CHAT_ID, MEXC_API_KEY, MEXC_SECRET
    (MEXC API key chỉ cấp quyền ĐỌC, tắt quyền rút/giao dịch)

Biến tùy chọn (có mặc định): xem phần CONFIG bên dưới.
Cài đặt: pip install requests flask waitress
"""

import hashlib
import hmac
import html
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

# ============================== CONFIG ==============================
def require_env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Thiếu biến môi trường bắt buộc: {name}")
    return v


def env_float(name, default):
    return float(os.getenv(name, default))


TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
CHAT_ID = require_env("CHAT_ID")
MEXC_API_KEY = require_env("MEXC_API_KEY")
MEXC_SECRET = require_env("MEXC_SECRET")

PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "paper_trades.db")

ORDER_SIZE_USD = env_float("ORDER_SIZE_USD", 500)
THRESHOLD_NET = env_float("THRESHOLD_NET", 0.8)
PREFILTER_GROSS = env_float("PREFILTER_GROSS", 0.3)
MAX_GROSS = env_float("MAX_GROSS", 15.0)
MAX_IMPACT_PCT = env_float("MAX_IMPACT_PCT", 1.0)
MIN_LIQ_USD = env_float("MIN_LIQ_USD", 30000)
MIN_VOL_24H = env_float("MIN_VOL_24H", 20000)
MIN_TXNS_H1 = int(env_float("MIN_TXNS_H1", 10))
MEXC_TAKER_FEE = env_float("MEXC_TAKER_FEE", 0.002)
COOLDOWN_MIN = env_float("COOLDOWN_MIN", 25)
SCAN_PAUSE_SEC = env_float("SCAN_PAUSE_SEC", 30)
STUCK_SEC = env_float("STUCK_SEC", 900)
STATIC_TTL = 1800
BOOK_TTL = 10
DEX_BATCH = 30
FOLLOWUP_MIN = (5, 10)
DEBUG_NETWORKS = os.getenv("DEBUG_NETWORKS", "0") == "1"

MEXC_BASE = "https://api.mexc.com"
WSOL_MINT = "So11111111111111111111111111111111111111112"
WNEAR = "wrap.near"

CHAINS = {
    "SOL":      {"emoji": "🟣", "dex_chain": "solana",   "evm": False, "gas_usd": 0.01,
                 "explorer": "https://solscan.io/token/",
                 "aliases": {"SOL", "SOLANA", "SOLANASOL"}},
    "BNB":      {"emoji": "🟡", "dex_chain": "bsc",      "evm": True,  "gas_usd": 0.15,
                 "explorer": "https://bscscan.com/token/",
                 "aliases": {"BSC", "BEP20", "BSCBEP20", "BEP20BSC", "BNBSMARTCHAIN", "BNBSMARTCHAINBEP20"}},
    "BASE":     {"emoji": "🔵", "dex_chain": "base",     "evm": True,  "gas_usd": 0.05,
                 "explorer": "https://basescan.org/token/",
                 "aliases": {"BASE", "BASECHAIN", "BASEBASE"}},
    "ETH":      {"emoji": "⚫", "dex_chain": "ethereum", "evm": True,  "gas_usd": 2.5,
                 "explorer": "https://etherscan.io/token/",
                 "aliases": {"ERC20", "ETH", "ETHEREUM", "ETHEREUMERC20"}},
    "POLYGON":  {"emoji": "💜", "dex_chain": "polygon",  "evm": True,  "gas_usd": 0.02,
                 "explorer": "https://polygonscan.com/token/",
                 "aliases": {"MATIC", "POLYGON", "POLYGONMATIC", "POLYGONPOLYGON"}},
    "ARBITRUM": {"emoji": "🔷", "dex_chain": "arbitrum", "evm": True,  "gas_usd": 0.15,
                 "explorer": "https://arbiscan.io/token/",
                 "aliases": {"ARB", "ARBITRUM", "ARBITRUMONE", "ARBITRUMONEARB"}},
    "ROBINHOOD": {"emoji": "🔶", "dex_chain": "robinhood", "evm": True, "gas_usd": 0.01,
                  "explorer": "https://robinhoodchain.blockscout.com/token/",
                  "aliases": {"ROBINHOOD", "ROBINHOODCHAIN", "HOODCHAIN", "RHCHAIN"}},
    "ARC":       {"emoji": "🟪", "dex_chain": "arc",       "evm": True, "gas_usd": 0.02,
                  "explorer": "https://explorer.arc.io/token/",
                  "aliases": {"ARC", "ARCCHAIN", "ARCNETWORK", "CIRCLEARC", "ARCARC"}},
    "NEAR":      {"emoji": "🟢", "dex_chain": "near",      "evm": False, "near": True, "gas_usd": 0.01,
                  "explorer": "https://nearblocks.io/token/",
                  "aliases": {"NEAR", "NEARPROTOCOL", "NEARNEAR"}},
}
for _k, _c in CHAINS.items():
    _c["dex_chain"] = os.getenv(f"DEX_CHAIN_{_k}", _c["dex_chain"])
ENABLED = [c.strip().upper() for c in os.getenv("ENABLED_CHAINS", ",".join(CHAINS)).split(",") if c.strip()]
CHAINS = {k: v for k, v in CHAINS.items() if k in ENABLED}

DEX_FEES = {"uniswap": 0.003, "pancakeswap": 0.0025, "sushiswap": 0.003, "quickswap": 0.003,
            "aerodrome": 0.003, "camelot": 0.003, "raydium": 0.0025, "orca": 0.003, "meteora": 0.003}
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
    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.next = 0.0
        self.lock = threading.Lock()
    def wait(self):
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next)
            self.next = slot + self.min_interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

MEXC_LIMITER = RateLimiter(0.05)
DEX_LIMITER = RateLimiter(0.25)

def http_get(url, params=None, headers=None, limiter=None, timeout=15, retries=2):
    for attempt in range(retries + 1):
        try:
            if limiter:
                limiter.wait()
            with GLOBAL_SEM:
                r = session().get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", 2 * (attempt + 1)))
                except ValueError:
                    wait = 2 * (attempt + 1)
                log.warning("429 từ %s, ngủ %.0fs", url.split("?")[0], wait)
                time.sleep(min(wait, 30))
                continue
            return r
        except requests.RequestException as e:
            log.warning("HTTP lỗi %s: %s", url.split("?")[0], e)
            time.sleep(1 + attempt)
    return None

def mexc_signed_get(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p.setdefault("recvWindow", 10000)
    qs = urlencode(p)
    sig = hmac.new(MEXC_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return http_get(f"{MEXC_BASE}{path}?{qs}&signature={sig}",
                    headers={"X-MEXC-APIKEY": MEXC_API_KEY}, limiter=MEXC_LIMITER, timeout=30)

def fnum(x, default=0.0):
    try:
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else default
    except (TypeError, ValueError):
        return default

def esc(x):
    return html.escape(str(x), quote=False)

class Stats:
    FIELDS = ("attempted", "fetched", "opportunity", "signals", "suspect", "errors", "dex_pairs")
    def __init__(self):
        self.lock = threading.Lock()
        self.c = dict.fromkeys(self.FIELDS, 0)
        self.last_success = None
        self.last_scan = None
        self.last_beat = datetime.now()
    def inc(self, k, n=1):
        with self.lock:
            self.c[k] += n
    def success(self):
        with self.lock:
            self.last_success = datetime.now()
    def scanned(self):
        with self.lock:
            self.last_scan = datetime.now()
    def beat(self):
        with self.lock:
            self.last_beat = datetime.now()
    def snapshot(self):
        with self.lock:
            d = dict(self.c)
            for k in ("last_success", "last_scan", "last_beat"):
                v = getattr(self, k)
                d[k] = v.isoformat() if v else None
            return d

STATS = {k: Stats() for k in CHAINS}

def send_telegram(msg, retries=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(retries):
        try:
            r = requests.post(url, data=data, timeout=15)
            if r.status_code == 200 and r.json().get("ok"):
                return True
            log.warning("Telegram thất bại (%s): %s", r.status_code, r.text[:200])
            if r.status_code == 429:
                time.sleep(5)
        except Exception as e:
            log.warning("Telegram lỗi: %s", e)
        time.sleep(2 ** attempt)
    return False

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
NEAR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

def norm_net(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

def norm_addr(chain_key, addr):
    addr = (addr or "").strip()
    cfg = CHAINS[chain_key]
    return addr.lower() if (cfg["evm"] or cfg.get("near")) else addr

class MexcData:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens = {}
        self.pairs = {}
        self.ts = 0.0
        self.retry_after = 0.0
        self.book = {}
        self.book_ts = 0.0
        self.book_lock = threading.Lock()
    def _fetch_pairs(self):
        r = http_get(f"{MEXC_BASE}/api/v3/exchangeInfo", limiter=MEXC_LIMITER, timeout=40)
        if not r or r.status_code != 200:
            raise RuntimeError(f"exchangeInfo lỗi: {r.status_code if r else 'no response'}")
        pairs = {}
        for s in r.json().get("symbols", []):
            if str(s.get("status")) not in ("1", "ENABLED"):
                continue
            if s.get("isSpotTradingAllowed") is False:
                continue
            base, quote = s.get("baseAsset"), s.get("quoteAsset")
            if quote not in ("USDT", "USDC") or not base:
                continue
            if base not in pairs or quote == "USDT":
                pairs[base] = s["symbol"]
        return pairs
    def _fetch_tokens(self, pairs):
        r = mexc_signed_get("/api/v3/capital/config/getall")
        if not r or r.status_code != 200:
            raise RuntimeError(f"capital/config lỗi: {(r.status_code, r.text[:200]) if r else 'no response'}")
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"capital/config trả về không phải list: {str(data)[:200]}")
        result = {k: {} for k in CHAINS}
        unmatched = set()
        for coin in data:
            sym = coin.get("coin")
            if not sym or sym not in pairs:
                continue
            for net in coin.get("networkList", []):
                if not (net.get("depositEnable") and net.get("withdrawEnable")):
                    continue
                names = {norm_net(net.get("network")), norm_net(net.get("netWork"))} - {""}
                chain_key = next((k for k, c in CHAINS.items() if names & c["aliases"]), None)
                if not chain_key:
                    unmatched.add(net.get("network"))
                    continue
                contract = (net.get("contract") or "").strip()
                cfg = CHAINS[chain_key]
                if cfg["evm"]:
                    if not EVM_RE.match(contract):
                        continue
                elif cfg.get("near"):
                    if not contract and sym == "NEAR":
                        contract = WNEAR
                    contract = contract.lower()
                    if not NEAR_RE.match(contract):
                        continue
                else:
                    if not contract and sym == "SOL":
                        contract = WSOL_MINT
                    if not SOL_RE.match(contract):
                        continue
                dedup = norm_addr(chain_key, contract)
                if dedup in result[chain_key]:
                    continue
                result[chain_key][dedup] = {
                    "symbol": sym, "contract": contract, "dedup_key": dedup, "chain": chain_key,
                    "pair": pairs[sym], "raw_network": net.get("network"),
                    "withdraw_fee": fnum(net.get("withdrawFee")),
                    "withdraw_min": fnum(net.get("withdrawMin")),
                }
        if DEBUG_NETWORKS:
            log.info("Tên mạng MEXC KHÔNG khớp alias: %s", sorted(str(x) for x in unmatched))
        final = {k: list(v.values()) for k, v in result.items()}
        for k, v in final.items():
            log.info("MEXC %s: %d token hợp lệ", k, len(v))
        return final
    def ensure_static(self):
        with self.lock:
            now = time.time()
            if self.tokens and now - self.ts < STATIC_TTL:
                return
            if now < self.retry_after:
                return
            try:
                pairs = self._fetch_pairs()
                tokens = self._fetch_tokens(pairs)
                self.pairs, self.tokens, self.ts = pairs, tokens, now
            except Exception as e:
                log.error("Làm mới dữ liệu MEXC lỗi: %s", e)
                self.retry_after = now + 60
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
                    log.warning("bookTicker parse lỗi: %s", e)
            return self.book

MEXC = MexcData()

def mexc_depth(pair):
    r = http_get(f"{MEXC_BASE}/api/v3/depth", params={"symbol": pair, "limit": 100},
                 limiter=MEXC_LIMITER, timeout=12)
    if not r or r.status_code != 200:
        return None
    try:
        j = r.json()
        bids = [(float(p), float(q)) for p, q in j.get("bids", []) if float(p) > 0 and float(q) > 0]
        asks = [(float(p), float(q)) for p, q in j.get("asks", []) if float(p) > 0 and float(q) > 0]
    except Exception:
        return None
    if len(bids) < 5 or len(asks) < 5:
        return None
    if asks[0][0] <= bids[0][0] or (asks[0][0] - bids[0][0]) / asks[0][0] > 0.1:
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
    if not r or r.status_code != 200:
        return None
    try:
        pairs = r.json()
    except Exception:
        return None
    if not isinstance(pairs, list):
        return None
    STATS[chain_key].inc("dex_pairs", sum(1 for p in pairs if p.get("chainId") == cid))
    best = {}
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
        if price <= 0 or liq < MIN_LIQ_USD or vol < MIN_VOL_24H or txns < MIN_TXNS_H1:
            continue
        if key in best and best[key]["liq"] >= liq:
            continue
        dex_id = (p.get("dexId") or "").lower()
        best[key] = {
            "price": price, "liq": liq, "vol24": vol, "txns_h1": txns, "dex": dex_id,
            "fee": DEX_FEES.get(dex_id, DEFAULT_DEX_FEE),
            "url": p.get("url") or "",
            "pair_name": f"{base.get('symbol')}/{(p.get('quoteToken') or {}).get('symbol')}",
            "base_symbol": base.get("symbol") or "",
        }
    return best

def symbol_ok(a, b):
    a, b = re.sub(r"[^A-Z0-9]", "", a.upper()), re.sub(r"[^A-Z0-9]", "", b.upper())
    return bool(a) and (a == b or (len(a) >= 3 and (a in b or b in a)))

def dex_prices(pool):
    impact_pct = 2 * ORDER_SIZE_USD / max(pool["liq"], 1) * 100
    f, i = pool["fee"], impact_pct / 100
    return impact_pct, pool["price"] * (1 + f + i), pool["price"] * (1 - f - i)

def evaluate(chain_key, tok, pool, depth):
    usd = ORDER_SIZE_USD
    gas = CHAINS[chain_key]["gas_usd"]
    impact_pct, buy_px, sell_px = dex_prices(pool)
    if impact_pct > MAX_IMPACT_PCT:
        return []
    bids, asks = depth["bids"], depth["asks"]
    out = []
    w = walk_usd(asks, usd)
    if w:
        avg, tokens = w
        tokens_net = tokens - tok["withdraw_fee"]
        if tokens_net > 0 and tokens >= tok["withdraw_min"]:
            cost = usd * (1 + MEXC_TAKER_FEE)
            revenue = tokens_net * sell_px - gas
            net = revenue - cost
            out.append({
                "direction": "MEXC_TO_DEX", "token_amount": tokens, "mexc_avg": avg, "dex_exec": sell_px,
                "cost": cost, "net_usd": net, "net_pct": net / cost * 100,
                "gross_pct": (sell_px / avg - 1) * 100, "gas": gas,
                "withdraw_fee_usd": tok["withdraw_fee"] * avg, "impact_dex": impact_pct,
                "mexc_best": (bids[0][0], asks[0][0]),
            })
    tokens = usd / buy_px
    proceeds = walk_tokens(bids, tokens)
    if proceeds:
        cost = usd + gas * 2
        revenue = proceeds * (1 - MEXC_TAKER_FEE)
        net = revenue - cost
        avg = proceeds / tokens
        out.append({
            "direction": "DEX_TO_MEXC", "token_amount": tokens, "mexc_avg": avg, "dex_exec": buy_px,
            "cost": cost, "net_usd": net, "net_pct": net / cost * 100,
            "gross_pct": (avg / buy_px - 1) * 100, "gas": gas * 2,
            "withdraw_fee_usd": 0.0, "impact_dex": impact_pct,
            "mexc_best": (bids[0][0], asks[0][0]),
        })
    return out

COOLDOWN = {}
CD_LOCK = threading.Lock()

def should_alert(key, net_pct):
    with CD_LOCK:
        last = COOLDOWN.get(key)
    if not last:
        return True
    t, p = last
    age = (datetime.now() - t).total_seconds()
    if age >= COOLDOWN_MIN * 60:
        return True
    return age >= 300 and net_pct > p * 1.3

def mark_alerted(key, net_pct):
    with CD_LOCK:
        COOLDOWN[key] = (datetime.now(), net_pct)

class PaperDB:
    COLS = ("net_pct_5m", "net_pct_10m")
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, chain TEXT, symbol TEXT, contract TEXT,
                direction TEXT, pair TEXT, dex TEXT, order_usd REAL, token_amount REAL, mexc_avg REAL,
                dex_exec REAL, dex_price REAL, liq REAL, gross_pct REAL, net_pct REAL, net_usd REAL,
                net_pct_5m REAL, net_pct_10m REAL, raw TEXT)""")
            self.conn.commit()
    def insert(self, chain, tok, pool, p):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO signals(ts,chain,symbol,contract,direction,pair,dex,order_usd,token_amount,"
                "mexc_avg,dex_exec,dex_price,liq,gross_pct,net_pct,net_usd,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(), chain, tok["symbol"], tok["contract"], p["direction"], tok["pair"],
                 pool["dex"], ORDER_SIZE_USD, p["token_amount"], p["mexc_avg"], p["dex_exec"], pool["price"],
                 pool["liq"], p["gross_pct"], p["net_pct"], p["net_usd"], json.dumps({"profit": p, "pool": pool})))
            self.conn.commit()
            return cur.lastrowid
    def set_followup(self, row_id, minutes, value):
        col = f"net_pct_{minutes}m"
        if col not in self.COLS:
            return
        with self.lock:
            self.conn.execute(f"UPDATE signals SET {col}=? WHERE id=?", (value, row_id))
            self.conn.commit()
    def recent(self, n=50):
        with self.lock:
            cur = self.conn.execute(
                "SELECT id,ts,chain,symbol,direction,gross_pct,net_pct,net_pct_5m,net_pct_10m FROM signals "
                "ORDER BY id DESC LIMIT ?", (n,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

DB = PaperDB(DB_PATH)
FOLLOWUPS = []
FU_LOCK = threading.Lock()

def schedule_followups(row_id, chain, tok, direction):
    with FU_LOCK:
        for m in FOLLOWUP_MIN:
            FOLLOWUPS.append({"due": time.time() + m * 60, "minutes": m, "id": row_id,
                              "chain": chain, "tok": tok, "direction": direction})

def followup_loop():
    while True:
        time.sleep(10)
        with FU_LOCK:
            now = time.time()
            due = [f for f in FOLLOWUPS if f["due"] <= now]
            FOLLOWUPS[:] = [f for f in FOLLOWUPS if f["due"] > now]
        for f in due:
            value = None
            try:
                tok, chain = f["tok"], f["chain"]
                pools = fetch_dex_pools(chain, [tok["contract"]])
                pool = (pools or {}).get(tok["dedup_key"])
                depth = mexc_depth(tok["pair"]) if pool else None
                if pool and depth:
                    for p in evaluate(chain, tok, pool, depth):
                        if p["direction"] == f["direction"]:
                            value = p["net_pct"]
            except Exception as e:
                log.warning("Follow-up lỗi: %s", e)
            DB.set_followup(f["id"], f["minutes"], value)

def send_alert(chain_key, tok, pool, p):
    cfg = CHAINS[chain_key]
    if p["direction"] == "MEXC_TO_DEX":
        action = f"MUA MEXC {tok['pair']} → rút về {chain_key} → BÁN trên DEX"
    else:
        action = f"MUA trên {chain_key} DEX → nạp lên MEXC → BÁN {tok['pair']}"
    bid, ask = p["mexc_best"]
    msg = (
        f"{cfg['emoji']} <b>ARB {esc(tok['symbol'])} [{chain_key}]</b> (paper)\n\n"
        f"Contract: <code>{esc(tok['contract'])}</code>\n"
        f"DEX: {esc(pool['dex'])} | {esc(pool['pair_name'])} | fee ~{pool['fee']*100:.2f}%\n"
        f"Liq: ${pool['liq']:,.0f} | Vol24h: ${pool['vol24']:,.0f} | Txns1h: {pool['txns_h1']}\n\n"
        f"MEXC {esc(tok['pair'])}: bid/ask {bid:.8g}/{ask:.8g} | giá TB ${p['mexc_avg']:.8g}\n"
        f"DEX: giá ${pool['price']:.8g} | giá thực thi ${p['dex_exec']:.8g} | impact ~{p['impact_dex']:.2f}%\n\n"
        f"💰 <b>NET {p['net_pct']:.3f}% (${p['net_usd']:.2f})</b> | Gross {p['gross_pct']:.3f}%\n"
        f"Vốn ${ORDER_SIZE_USD:.0f} | Token {p['token_amount']:.6g}\n"
        f"Gas ${p['gas']:.2f} | Phí rút ${p['withdraw_fee_usd']:.2f} | Phí MEXC {MEXC_TAKER_FEE*100:.2f}%\n"
        f"🎯 {esc(action)}\n\n"
        f"🔗 MEXC: https://www.mexc.com/exchange/{esc(tok['pair'])}\n"
        f"🔗 DEX: {esc(pool['url'])}\n"
        f"🔗 Explorer: {esc(cfg['explorer'] + tok['contract'])}\n\n"
        f"⚠ Chỉ là mô phỏng. Cần thời gian chuyển tài sản, giá có thể đã đổi.\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')}"
    )
    return send_telegram(msg)

def check_candidate(chain_key, tok, pool):
    st = STATS[chain_key]
    try:
        depth = mexc_depth(tok["pair"])
        if not depth:
            return None
        st.inc("fetched")
        st.success()
        best = None
        for p in evaluate(chain_key, tok, pool, depth):
            if p["gross_pct"] >= PREFILTER_GROSS:
                st.inc("opportunity")
            if p["net_pct"] >= THRESHOLD_NET:
                if p["gross_pct"] > MAX_GROSS:
                    st.inc("suspect")
                    log.info("NGHI VẤN %s %s gross %.1f%% - bỏ qua", chain_key, tok["symbol"], p["gross_pct"])
                    continue
                if not best or p["net_pct"] > best["net_pct"]:
                    best = p
        return (tok, pool, best) if best else None
    except Exception:
        st.inc("errors")
        log.exception("Lỗi xử lý %s %s", chain_key, tok["symbol"])
        return None

def prefilter(chain_key, tok, pool, book):
    bt = book.get(tok["pair"])
    if not bt:
        return False
    bid, ask = bt
    _, buy_px, sell_px = dex_prices(pool)
    g1 = (sell_px / ask - 1) * 100
    g2 = (bid / buy_px - 1) * 100
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
        st.beat()
        chunk = tokens[i:i + DEX_BATCH]
        pools = fetch_dex_pools(chain_key, [t["contract"] for t in chunk])
        if pools is None:
            st.inc("errors")
            continue
        candidates = []
        for tok in chunk:
            pool = pools.get(tok["dedup_key"])
            if not pool or not symbol_ok(tok["symbol"], pool["base_symbol"]):
                continue
            st.inc("attempted")
            if prefilter(chain_key, tok, pool, book):
                candidates.append((tok, pool))
        if candidates:
            with ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(lambda c: check_candidate(chain_key, c[0], c[1]), candidates))
            for res in results:
                if not res:
                    continue
                tok, pool, p = res
                key = (chain_key, tok["dedup_key"], p["direction"])
                if not should_alert(key, p["net_pct"]):
                    continue
                if send_alert(chain_key, tok, pool, p):
                    mark_alerted(key, p["net_pct"])
                    st.inc("signals")
                    row_id = DB.insert(chain_key, tok, pool, p)
                    schedule_followups(row_id, chain_key, tok, p["direction"])
        stop.wait(1)
    return True

def run_chain_loop(chain_key, stop):
    log.info("[%s] start", chain_key)
    empty = 0
    warned = False
    while not stop.is_set():
        try:
            STATS[chain_key].beat()
            if not scan_chain_once(chain_key, stop):
                empty += 1
                log.info("[%s] chưa có dữ liệu (%d), thử lại sau", chain_key, empty)
                stop.wait(60 if empty <= 10 else 300)
                continue
            empty = 0
            STATS[chain_key].scanned()
            if STATS[chain_key].snapshot()["dex_pairs"] == 0:
                log.warning("[%s] Dexscreener không trả pair nào (slug '%s' sai/chưa hỗ trợ?)",
                            chain_key, CHAINS[chain_key]["dex_chain"])
                if not warned:
                    warned = True
                    send_telegram(f"⚠ {esc(chain_key)}: Dexscreener không trả pair nào cho slug "
                                  f"<code>{esc(CHAINS[chain_key]['dex_chain'])}</code>. "
                                  f"Kiểm tra slug (biến DEX_CHAIN_{esc(chain_key)}). Chain này sẽ quét thưa 1 giờ/lần.")
                stop.wait(3600)
                continue
            stop.wait(SCAN_PAUSE_SEC)
        except Exception:
            STATS[chain_key].inc("errors")
            log.exception("[%s] lỗi vòng chính", chain_key)
            stop.wait(20)
    log.info("[%s] dừng (generation cũ thoát)", chain_key)

class Worker:
    def __init__(self, chain_key):
        self.chain = chain_key
        self.stop = None
        self.thread = None
    def start(self):
        self.stop = threading.Event()
        STATS[self.chain].beat()
        self.thread = threading.Thread(target=run_chain_loop, args=(self.chain, self.stop),
                                       daemon=True, name=f"Bot-{self.chain}")
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
            msg = (f"💓 <b>HEARTBEAT {datetime.now().strftime('%H:%M %d/%m')}</b>\n"
                   f"Net ≥ {THRESHOLD_NET}% | Order ${ORDER_SIZE_USD:.0f}\n\n")
            for k, st in STATS.items():
                s = st.snapshot()
                ls = STATS[k].last_success
                if not ls:
                    status = "❌ chưa có success"
                elif datetime.now() - ls > timedelta(minutes=15):
                    status = f"⚠ {int((datetime.now() - ls).total_seconds() / 60)}m không success"
                else:
                    status = f"✅ {ls.strftime('%H:%M')}"
                msg += (f"{CHAINS[k]['emoji']} {k}: try={s['attempted']} fetch={s['fetched']} "
                        f"opp={s['opportunity']} sig={s['signals']} sus={s['suspect']} err={s['errors']} | {status}\n")
            send_telegram(msg)
        except Exception:
            log.exception("Heartbeat lỗi")

def start_web():
    from flask import Flask, jsonify
    app = Flask(__name__)
    @app.route("/")
    def home():
        return f"V7 - signals: {sum(s.snapshot()['signals'] for s in STATS.values())}"
    @app.route("/health")
    def health():
        return "OK", 200
    @app.route("/stats")
    def stats():
        return jsonify({k: s.snapshot() for k, s in STATS.items()})
    @app.route("/signals")
    def signals():
        return jsonify(DB.recent(50))
    log.info("Web trên 0.0.0.0:%s", PORT)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT)
    except ImportError:
        app.run(host="0.0.0.0", port=PORT)

def main():
    log.info("BOT V7 start | chains=%s", list(CHAINS))
    threading.Thread(target=start_web, daemon=True, name="Web").start()
    threading.Thread(target=followup_loop, daemon=True, name="Followup").start()
    threading.Thread(target=heartbeat_loop, daemon=True, name="HB").start()
    send_telegram(f"🚀 <b>BOT V7 START</b> (paper trading)\nChains: {', '.join(CHAINS)}\n"
                  f"Net ≥ {THRESHOLD_NET}% | Order ${ORDER_SIZE_USD:.0f}")
    workers = {}
    for k in CHAINS:
        w = Worker(k)
        w.start()
        workers[k] = w
        time.sleep(1.2)
    while True:
        time.sleep(300)
        now = datetime.now()
        for k, w in workers.items():
            st = STATS[k]
            idle = (now - st.last_beat).total_seconds()
            if not w.thread.is_alive() or idle > STUCK_SEC:
                log.warning("[SUPERVISOR] %s %s -> restart", k,
                            "chết" if not w.thread.is_alive() else f"đứng {int(idle)}s")
                w.restart()
            else:
                log.info("[SUPERVISOR] %s OK, beat %ds trước", k, int(idle))

if __name__ == "__main__":
    main()
