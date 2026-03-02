import hashlib
import json
import re
import shelve
import time
import logging
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
PROXY_URL = "http://codify-dc-any:58ADAB79s03h8TJ@gw.netnut.net:5959"
APP_KEY = "34839810"
API_HOST = "https://h5api.m.goofish.com"
API_PATH = "/h5/mtop.taobao.idle.pc.detail/1.0/"

BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "content-type": "application/json",
    "origin": "https://www.goofish.com",
    "referer": "https://www.goofish.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_memory_cache: dict[str, list] = {}
DISK_CACHE_PATH = "goofish_cache"

def _make_client(use_proxy: bool = True) -> httpx.Client:
    if use_proxy:
        transport = httpx.HTTPTransport(proxy=PROXY_URL)
        return httpx.Client(transport=transport, timeout=25, follow_redirects=True)
    return httpx.Client(timeout=25, follow_redirects=True)


def _extract_item_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if "id" in qs:
        return qs["id"][0]

    match = re.search(r"/item[s]?/(\d+)", parsed.path)
    if match:
        return match.group(1)

    match = re.search(r"(\d{10,})", url)
    if match:
        return match.group(1)

    return None


def _build_sign(token: str, timestamp: str, app_key: str, payload: str) -> str:
    raw = f"{token}&{timestamp}&{app_key}&{payload}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _get_session_cookies() -> dict:
    return {
        "_m_h5_tk": "4533a95c718cee2bd33f46d19d5aca3a_1772488778725",
        "_m_h5_tk_enc": "907f9eef324805274a82fe8b42585cbc",
        "cna": "/bgsItPJSxUCAbMhcDJdVjDR",
    }


# =================================================================
# SCRAPING: INTENTO 1 — HTML
# =================================================================

def _fetch_via_html(item_id: str, url: str) -> Optional[dict]:
    try:
        with _make_client(use_proxy=True) as client:
            response = client.get(
                url,
                headers={
                    "user-agent": BASE_HEADERS["user-agent"],
                    "accept-language": BASE_HEADERS["accept-language"],
                    "referer": "https://www.goofish.com/",
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )

        if response.status_code == 404:
            logger.warning(f"[HTML] 404 para item_id={item_id}")
            return {"_not_found": True}

        if response.status_code != 200:
            logger.warning(f"[HTML] Status {response.status_code} para item_id={item_id}")
            return None

        html = response.text

        patterns = [
            r'window\.__INIT_DATA__\s*=\s*(\{.+?\})\s*;?\s*(?:window\.|</script>)',
            r'var\s+initData\s*=\s*(\{.+?\})\s*;',
            r'"itemDO"\s*:\s*(\{.+?\})\s*[,}]',
            r'<script[^>]+type=["\']application/json["\'][^>]*>\s*(\{.+?\})\s*</script>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    if any(k in str(data) for k in ["itemId", "title", "soldPrice", "itemDO"]):
                        logger.info(f"[HTML] Datos encontrados para item_id={item_id}")
                        return data
                except (json.JSONDecodeError, Exception):
                    continue

        logger.warning(f"[HTML] No se encontró JSON embebido para item_id={item_id}")
        return None

    except httpx.TimeoutException:
        logger.warning(f"[HTML] Timeout para item_id={item_id}")
        return None
    except Exception as e:
        logger.error(f"[HTML] Error para item_id={item_id}: {e}")
        return None

def _fetch_via_api(item_id: str) -> Optional[dict]:
    cookies = _get_session_cookies()

    raw_token_cookie = cookies.get("_m_h5_tk", "")
    parts = raw_token_cookie.rsplit("_", 1)
    token = parts[0] if len(parts) == 2 else raw_token_cookie

    timestamp = str(int(time.time() * 1000))
    payload = json.dumps({"itemId": item_id}, separators=(",", ":"))
    sign = _build_sign(token, timestamp, APP_KEY, payload)

    params = {
        "jsv": "2.7.2",
        "appKey": APP_KEY,
        "t": timestamp,
        "sign": sign,
        "v": "1.0",
        "type": "originaljson",
        "dataType": "json",
        "timeout": "20000",
        "api": "mtop.taobao.idle.pc.detail",
        "AntiCreep": "true",
        "H5Request": "true",
        "data": payload,
    }

    try:
        with _make_client(use_proxy=True) as client:
            response = client.get(
                f"{API_HOST}{API_PATH}",
                params=params,
                headers=BASE_HEADERS,
                cookies=cookies,
            )

        if response.status_code != 200:
            logger.warning(f"[API] Status {response.status_code} para item_id={item_id}")
            return None

        data = response.json()
        ret = data.get("ret", [])

        if any("SUCCESS" in r for r in ret):
            logger.info(f"[API] Éxito para item_id={item_id}")
            return data.get("data", {})

        if any("TOKEN_EXPIRIED" in r or "FAIL_SYS_TOKEN_EXIPRE" in r for r in ret):
            logger.error("[API] Token expirado — abre goofish.com, copia cookies frescas y actualiza _get_session_cookies()")
            return None

        if any("ITEM_NOT_FOUND" in r or "404" in r for r in ret):
            return {"_not_found": True}

        logger.warning(f"[API] Respuesta no exitosa para item_id={item_id}: {ret}")
        return None

    except httpx.TimeoutException:
        logger.warning(f"[API] Timeout para item_id={item_id}")
        return None
    except Exception as e:
        logger.error(f"[API] Error para item_id={item_id}: {e}")
        return None

def _parse_product_data(raw: dict, item_id: str, url: str) -> dict:
    data_root = raw.get("data") or raw
    item = (
        data_root.get("itemDO")
        or data_root.get("item")
        or raw.get("itemDO")
        or raw.get("item")
        or {}
    )
    seller = (
        data_root.get("sellerDO")
        or data_root.get("seller")
        or raw.get("sellerDO")
        or raw.get("seller")
        or {}
    )
    statistics = (
        data_root.get("statisticsDO")
        or data_root.get("statistics")
        or raw.get("statisticsDO")
        or raw.get("statistics")
        or {}
    )

    images_raw = item.get("picInfoList") or item.get("images") or item.get("pics") or []
    images = []
    for img in images_raw:
        src = (img.get("picUrl") or img.get("url") or "") if isinstance(img, dict) else str(img)
        if src:
            images.append("https:" + src if src.startswith("//") else src)

    price_raw = (
        item.get("soldPrice")
        or item.get("price")
        or (item.get("priceInfo") or {}).get("price")
        or "0"
    )
    try:
        sold_price = float(str(price_raw).replace(",", "").strip())
    except (ValueError, TypeError):
        sold_price = 0.0

    gmt_raw = item.get("gmtCreate") or item.get("publishTime") or ""
    if isinstance(gmt_raw, (int, float)) and gmt_raw > 0:
        gmt_create = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gmt_raw / 1000))
    else:
        gmt_create = str(gmt_raw)

    return {
        "ITEM_ID": str(item.get("itemId") or item.get("id") or item_id),
        "CATEGORY_ID": str(item.get("categoryId") or item.get("leafCategoryId") or ""),
        "TITLE": str(item.get("title") or item.get("name") or ""),
        "IMAGES": images,
        "SOLD_PRICE": sold_price,
        "BROWSE_COUNT": int(statistics.get("browseCount") or statistics.get("views") or 0),
        "WANT_COUNT": int(statistics.get("wantCount") or statistics.get("wants") or 0),
        "COLLECT_COUNT": int(statistics.get("collectCount") or statistics.get("collects") or 0),
        "QUANTITY": int(item.get("quantity") or item.get("stock") or 1),
        "GMT_CREATE": gmt_create,
        "SELLER_ID": str(seller.get("userId") or seller.get("sellerId") or ""),
        "_source_url": url,
        "_scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def scrape_pdp(url: str) -> list:
    """
    Extrae los datapoints de un producto de Goofish dado su URL.
    Retorna una lista con un dict del producto, o lista vacía si falla.
    """
    item_id = _extract_item_id(url)
    if not item_id:
        logger.error(f"No se pudo extraer ITEM_ID de: {url}")
        return []

    if item_id in _memory_cache:
        return _memory_cache[item_id]

    try:
        with shelve.open(DISK_CACHE_PATH) as db:
            if item_id in db:
                result = db[item_id]
                _memory_cache[item_id] = result
                return result
    except Exception as e:
        logger.warning(f"[CACHE] Error leyendo disco: {e}")

    raw_data = _fetch_via_html(item_id, url)

    if isinstance(raw_data, dict) and raw_data.get("_not_found"):
        _memory_cache[item_id] = []
        return []

    if not raw_data:
        logger.info(f"[FALLBACK] Usando API interna para item_id={item_id}")
        raw_data = _fetch_via_api(item_id)

    if isinstance(raw_data, dict) and raw_data.get("_not_found"):
        _memory_cache[item_id] = []
        return []

    if not raw_data:
        logger.error(f"No se pudo obtener datos para item_id={item_id}")
        return []

    try:
        product = _parse_product_data(raw_data, item_id, url)
    except Exception as e:
        logger.error(f"Error parseando datos para item_id={item_id}: {e}")
        return []

    result = [product]
    _memory_cache[item_id] = result

    try:
        with shelve.open(DISK_CACHE_PATH) as db:
            db[item_id] = result
    except Exception as e:
        logger.warning(f"[CACHE] Error escribiendo disco: {e}")

    logger.info(f"✅ item_id={item_id} | {product.get('TITLE', '')[:60]}")
    return result