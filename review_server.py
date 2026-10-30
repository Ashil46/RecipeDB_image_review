import argparse
import csv
import json
import os
import random
import re
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

app = Flask(__name__)

# --- SSL & HTTP Configuration ---
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Wikimedia requires a descriptive script User-Agent
WIKI_HEADERS = {
    "User-Agent": "RecipeDB-Image-Validator/3.0 (Local Curation Tool)"
}

# Third-party food blogs/CDNs require standard browser headers to avoid HTTP 403 / connection resets
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# --- Runtime Configuration Defaults ---
INPUT_CSV = "recipes_to_review.csv"
OUTPUT_CSV = "reviewed_recipes_output.csv"
IMAGES_DIR = "downloaded_recipe_images"
BATCH_SIZE = 10
MIN_IMAGE_BYTES = 5120  # 5 KB minimum file size

OUTPUT_COLUMNS = [
    "recipe_id",
    "recipe_name",
    "cuisine",
    "category",
    "source",
    "source_page_url",
    "new_image_id",
    "final_image_url",
    "local_file_path",
]

IGNORED_TAXONOMY = {
    "",
    "other",
    "none",
    "null",
    "na",
    "n/a",
    "unknown",
    "miscellaneous",
    "general",
}

# --- In-Memory State ---
DDG_LOCK = threading.Lock()
LAST_DDG_TS = 0.0

RECIPES = OrderedDict()  # {recipe_id: unified_recipe_dict}
SEARCH_CACHE = {}        # {query_key: {"candidates": [...], "query": str, "fallback": bool}}
LAST_ERRORS = []         # Download error alerts from the previous batch submission


# --- Text Normalization & Query Helpers ---
def strip_latin_diacritics(text):
    """Strip only Latin combining marks (U+0300-U+036F) so Indic/Japanese scripts stay intact."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    stripped = re.sub(r"[\u0300-\u036f]", "", decomposed)
    return unicodedata.normalize("NFC", stripped)


def slugify_filename(text, max_len=80):
    """POSIX-safe ASCII filename slug for local image storage."""
    clean = strip_latin_diacritics(text)
    ascii_only = clean.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_only.lower()).strip("_")
    return slug[:max_len] if slug else "recipe"


def clean_query_token(text, is_taxonomy=False):
    """Clean text for web search while preserving Indic/Japanese scripts, ampersands, and contractions."""
    if not text:
        return ""
    raw = strip_latin_diacritics(text).strip()
    if not raw or raw.lower() in IGNORED_TAXONOMY:
        return ""

    # Remove parenthetical remarks like "(or Stew)"
    cleaned = re.sub(r"\([^)]*\)", " ", raw)
    # Map '&' to ' and '
    cleaned = cleaned.replace("&", " and ")
    # Delete apostrophes directly so "Let's" -> "Lets" and "Barb's" -> "Barbs"
    cleaned = re.sub(r"['’`]", "", cleaned)

    if is_taxonomy and ("," in cleaned or "/" in cleaned):
        parts = [
            p.strip()
            for p in re.split(r"[,/]", cleaned)
            if p.strip() and p.strip().lower() not in IGNORED_TAXONOMY
        ]
        cleaned = parts[-1] if parts else ""

    # Keep word characters across Unicode scripts plus whitespace
    cleaned = re.sub(r"[^\w\s]", " ", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if is_taxonomy and cleaned.lower() in IGNORED_TAXONOMY:
        return ""
    return cleaned


def build_queries(recipe_name, cuisine, category):
    """Build primary composite query and fallback query."""
    name_clean = clean_query_token(recipe_name, is_taxonomy=False)
    cuisine_clean = clean_query_token(cuisine, is_taxonomy=True)
    cat_clean = clean_query_token(category, is_taxonomy=True)

    primary_parts = [p for p in (name_clean, cuisine_clean, cat_clean, "dish") if p]
    primary_q = " ".join(primary_parts)
    fallback_q = f"{name_clean or recipe_name.strip()} recipe food".strip()
    return primary_q, fallback_q


def summarize_context(row_map):
    """Extract a concise 1-line context string from ingredients or description if available."""
    raw_ing = str(row_map.get("ingredients") or "").strip()
    raw_desc = str(row_map.get("description") or "").strip()
    source_text = raw_ing or raw_desc
    if not source_text:
        return ""
    cleaned = re.sub(r'[\[\]"\']+', "", source_text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned[:140] + "...") if len(cleaned) > 140 else cleaned


# --- CSV Persistence (Unified In-Memory Store + Full Output Mirror) ---
def is_completed_decision(row):
    final_url = str(row.get("final_image_url") or "").strip()
    local_path = str(row.get("local_file_path") or "").strip()
    return bool(final_url and not local_path.startswith("ERROR_"))


def sync_output_csv():
    """Write all records in RECIPES to OUTPUT_CSV matching the 9-column output contract."""
    tmp_path = f"{OUTPUT_CSV}.tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for r in RECIPES.values():
            writer.writerow({col: r.get(col, "") for col in OUTPUT_COLUMNS})
    os.replace(tmp_path, OUTPUT_CSV)


def load_datasets():
    """Load pre-flagged recipes from INPUT_CSV and merge completed decisions from OUTPUT_CSV."""
    RECIPES.clear()
    if not os.path.exists(INPUT_CSV):
        print(f"[ERROR] Input CSV not found: {INPUT_CSV}")
        return

    with open(INPUT_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lmap = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            rid = str(lmap.get("recipe_id") or lmap.get("id") or "").strip()
            if not rid:
                continue

            rname = str(lmap.get("recipe_name") or lmap.get("recipe name") or "").strip()
            cuisine = str(lmap.get("cuisine") or "").strip()
            category = str(lmap.get("category") or lmap.get("course") or "").strip()
            image_url = str(lmap.get("image_url") or "").strip()
            image_id = str(lmap.get("image_id") or "").strip()
            raw_na = lmap.get("na_image")
            na_image = "" if raw_na is None else str(raw_na).strip()
            reason = str(lmap.get("reason") or "").strip()
            if not reason:
                reason = "Missing image_url" if not image_url else (f"na_image = {na_image}" if na_image else "Flagged image")

            primary_q, fallback_q = build_queries(rname, cuisine, category)

            RECIPES[rid] = {
                "recipe_id": rid,
                "recipe_name": rname,
                "cuisine": cuisine,
                "category": category,
                "image_url": image_url,
                "image_id": image_id,
                "na_image": na_image,
                "reason": reason,
                "recipe_page_url": str(lmap.get("url") or "").strip(),
                "context_snippet": summarize_context(lmap),
                "primary_query": primary_q,
                "fallback_query": fallback_q,
                "source": "",
                "source_page_url": "",
                "new_image_id": "",
                "final_image_url": "",
                "local_file_path": "",
                "reviewed": False,
                "skip_count": 0,
            }

    if os.path.exists(OUTPUT_CSV):
        try:
            with open(OUTPUT_CSV, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    rid = str(row.get("recipe_id") or "").strip()
                    if rid in RECIPES and is_completed_decision(row):
                        for col in OUTPUT_COLUMNS:
                            if col in row and row[col] is not None:
                                RECIPES[rid][col] = str(row[col]).strip()
                        RECIPES[rid]["reviewed"] = True
        except Exception as e:
            print(f"[WARN] Could not read existing {OUTPUT_CSV}: {e}")

    if RECIPES:
        sync_output_csv()
    done_cnt = sum(1 for r in RECIPES.values() if r["reviewed"])
    print(f"[INFO] Loaded {len(RECIPES)} flagged recipes ({done_cnt} already reviewed).")


# --- Web Search Providers ---
def parse_domain(url):
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def search_wikimedia(query, limit=5, retries=3):
    """Query Wikimedia Commons Search API directly for bitmap images."""
    results = []
    if not query:
        return results

    params = urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|dimensions|mime",
        "iiurlwidth": 800,
        "format": "json",
    })
    commons_url = f"https://commons.wikimedia.org/w/api.php?{params}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(commons_url, headers=WIKI_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
                data = json.loads(r.read())
            for page in data.get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                thumb = info.get("thumburl") or info.get("url")
                orig = info.get("url")
                if thumb and orig:
                    results.append({
                        "thumb_url": thumb,
                        "image_url": orig,
                        "page_url": info.get("descriptionurl", ""),
                        "source": "Wikimedia Commons",
                        "domain": "commons.wikimedia.org",
                        "width": info.get("width") or 0,
                        "height": info.get("height") or 0,
                        "title": page.get("title", "").replace("File:", ""),
                    })
            break
        except urllib.error.HTTPError as e:
            sleep_s = int(e.headers.get("Retry-After", 5)) if e.code == 429 else 2
            time.sleep(sleep_s)
        except Exception:
            time.sleep(1.5)

    seen, unique = set(), []
    for item in results:
        if item["image_url"] not in seen:
            seen.add(item["image_url"])
            unique.append(item)
    return unique[:limit]


def search_ddg(query, limit=5, retries=3):
    global LAST_DDG_TS
    if not query:
        return []

    for attempt in range(retries):
        try:
            with DDG_LOCK:
                wait = random.uniform(0.55, 1.0) - (time.time() - LAST_DDG_TS)
                if wait > 0:
                    time.sleep(wait)
                LAST_DDG_TS = time.time()

            with DDGS() as ddgs:
                raw = list(ddgs.images(query, max_results=limit))

            seen, out = set(), []
            for r in raw:
                orig = r.get("image") or ""
                if not orig or orig in seen:
                    continue
                seen.add(orig)
                page_url = r.get("url") or ""
                out.append({
                    "thumb_url": r.get("thumbnail") or orig,
                    "image_url": orig,
                    "page_url": page_url,
                    "source": "DuckDuckGo",
                    "domain": parse_domain(page_url) or parse_domain(orig) or "DuckDuckGo",
                    "width": r.get("width") or 0,
                    "height": r.get("height") or 0,
                    "title": r.get("title") or "",
                })
            return out[:limit]
        except Exception as e:
            print(f"[WARN] DDG search retry {attempt + 1} for '{query}': {e}")
            time.sleep(2 ** attempt)
    return []


def fetch_candidates(recipe, custom_query=None):
    """Query Wikimedia + DuckDuckGo with in-memory caching (only when non-empty) and fallback."""
    initial_q = (custom_query or "").strip() or recipe["primary_query"]
    cache_key = initial_q.lower()

    if cache_key in SEARCH_CACHE:
        entry = SEARCH_CACHE[cache_key]
        return entry["candidates"], entry["query"], entry["fallback"]

    wiki = search_wikimedia(initial_q, limit=5)
    ddg = search_ddg(initial_q, limit=8 if len(wiki) < 2 else 5)
    combined = wiki + ddg
    executed_q = initial_q
    used_fallback = False

    if not custom_query and len(combined) < 3:
        fb_q = recipe["fallback_query"]
        if fb_q and fb_q.lower() != cache_key:
            used_fallback = True
            executed_q = fb_q
            fb_key = fb_q.lower()
            if fb_key in SEARCH_CACHE:
                fb_items = SEARCH_CACHE[fb_key]["candidates"]
            else:
                fb_items = (search_wikimedia(fb_q, limit=5) + search_ddg(fb_q, limit=6))[:10]
                if fb_items:
                    SEARCH_CACHE[fb_key] = {"candidates": fb_items, "query": fb_q, "fallback": False}

            seen = {c["image_url"] for c in combined}
            for item in fb_items:
                if item["image_url"] not in seen:
                    seen.add(item["image_url"])
                    combined.append(item)

    combined = combined[:10]
    if combined:
        SEARCH_CACHE[cache_key] = {"candidates": combined, "query": executed_q, "fallback": used_fallback}
    return combined, executed_q, used_fallback


# --- Image Downloader (Browser Headers + Automatic Thumbnail Fallback) ---
def build_download_headers(url, page_url=""):
    """
    Use WIKI_HEADERS for Wikimedia/Wikipedia URLs and realistic BROWSER_HEADERS
    with a matching Referer for third-party food blogs to prevent HTTP 403 / connection resets.
    """
    domain = urllib.parse.urlparse(url).netloc.lower()
    if "wikimedia.org" in domain or "wikipedia.org" in domain:
        return dict(WIKI_HEADERS)

    headers = dict(BROWSER_HEADERS)
    ref_target = page_url if (page_url and page_url != "NA") else url
    parsed_ref = urllib.parse.urlparse(ref_target)
    if parsed_ref.scheme and parsed_ref.netloc:
        headers["Referer"] = f"{parsed_ref.scheme}://{parsed_ref.netloc}/"
    return headers


def fetch_single_image_bytes(target_url, page_url=""):
    """Attempt to download and validate image bytes from a single URL."""
    headers = build_download_headers(target_url, page_url)
    req = urllib.request.Request(target_url, headers=headers)
    with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read()

    if len(data) < MIN_IMAGE_BYTES:
        raise ValueError(f"ERROR_TOO_SMALL ({len(data)} bytes < 5 KB)")
    if "text/html" in ctype or data[:15].lstrip().startswith((b"<!DOCTYPE", b"<html")):
        raise ValueError(f"ERROR_HTML_RESPONSE ({ctype})")
    return data, ctype


def download_image(url, recipe_id, recipe_name, page_url="", thumb_url=""):
    """
    Try downloading the primary full-size URL first; if the origin server blocks the request
    (HTTP 403, connection reset, or timeout), automatically fall back to thumb_url.
    Returns (success_bool, relative_path_or_error_msg, final_used_url).
    """
    candidates_to_try = [url]
    if thumb_url and thumb_url != url and thumb_url != "NA":
        candidates_to_try.append(thumb_url)

    last_error = "ERROR_DOWNLOADING"
    for candidate_url in candidates_to_try:
        try:
            data, ctype = fetch_single_image_bytes(candidate_url, page_url)
            ext = ".jpg"
            lower_url = candidate_url.lower().split("?")[0]
            if "png" in ctype or lower_url.endswith(".png"):
                ext = ".png"
            elif "webp" in ctype or lower_url.endswith(".webp"):
                ext = ".webp"
            elif "gif" in ctype or lower_url.endswith(".gif"):
                ext = ".gif"

            filename = f"{recipe_id}_{slugify_filename(recipe_name)}{ext}"
            rel_path = os.path.join(IMAGES_DIR, filename).replace("\\", "/")
            with open(rel_path, "wb") as f:
                f.write(data)
            return True, rel_path, candidate_url
        except Exception as e:
            last_error = f"ERROR_DOWNLOADING ({e})"
            print(f"[WARN] Download attempt failed for recipe {recipe_id} ({candidate_url}): {e}")

    return False, last_error, url


# --- HTML Template ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>RecipeDB 3.0 Image Validation Tool</title>
<style>
    * { box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background: #f3f4f8; color: #1f2937; margin: 0; padding: 18px 22px 100px 22px;
    }
    .top-header {
        background: #fff; padding: 14px 20px; border-radius: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 18px;
        display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px;
    }
    .top-header h1 { margin: 0 0 4px 0; font-size: 20px; }
    .top-header p { margin: 0; font-size: 12px; color: #4b5563; }
    .progress-panel { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .progress-bar { width: 160px; height: 9px; background: #e5e7eb; border-radius: 99px; overflow: hidden; }
    .progress-fill { height: 100%; background: #2563eb; }
    .btn {
        padding: 7px 12px; font-size: 12px; font-weight: 600; border-radius: 6px;
        border: 1px solid #d1d5db; background: #fff; color: #1f2937; cursor: pointer; text-decoration: none;
    }
    .btn:hover { background: #f3f4f6; }
    .btn-primary { background: #2563eb; color: #fff; border-color: #2563eb; }
    .btn-primary:hover { background: #1d4ed8; }
    .btn-success { background: #16a34a; color: #fff; border-color: #16a34a; font-size: 15px; padding: 10px 24px; }
    .btn-success:disabled { background: #9ca3af; border-color: #9ca3af; cursor: wait; }

    .error-banner {
        background: #fef2f2; border: 1px solid #f87171; color: #991b1b;
        padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 13px;
    }
    .recipe-row {
        background: #fff; border-radius: 8px; padding: 16px 18px; margin-bottom: 20px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.07); border: 2px solid transparent;
    }
    .recipe-row.active-row { border-color: #2563eb; }

    .recipe-banner {
        display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start;
        gap: 10px; padding-bottom: 10px; border-bottom: 1px solid #e5e7eb; margin-bottom: 12px;
    }
    .recipe-banner h2 { margin: 0 0 6px 0; font-size: 19px; }
    .context-line { font-size: 12px; color: #4b5563; margin-top: 6px; font-style: italic; }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .badge {
        font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
        background: #f3f4f6; border: 1px solid #d1d5db; color: #374151;
    }
    .badge-cuisine { background: #eff6ff; color: #1e40af; border-color: #bfdbfe; }
    .badge-category { background: #f0fdf4; color: #166534; border-color: #bbf7d0; }
    .badge-reason { background: #fef2f2; color: #991b1b; border-color: #fecaca; }
    .badge-done { background: #ecfdf5; color: #065f46; border-color: #6ee7b7; }

    .controls-bar {
        display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
        background: #f9fafb; padding: 9px 12px; border-radius: 6px; border: 1px solid #e5e7eb; margin-bottom: 12px;
    }
    .control-group { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 220px; }
    .control-group input[type="text"] {
        flex: 1; padding: 6px 9px; font-size: 12px; border: 1px solid #d1d5db; border-radius: 5px;
    }

    .workspace { display: grid; grid-template-columns: 220px 1fr; gap: 14px; align-items: start; }
    @media (max-width: 900px) { .workspace { grid-template-columns: 1fr; } }

    .current-panel {
        background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 10px; text-align: center;
    }
    .current-status-banner {
        font-size: 11px; font-weight: 700; padding: 4px 6px; border-radius: 4px;
        margin-bottom: 6px; text-transform: uppercase; background: #fee2e2; color: #991b1b;
    }
    .current-box {
        height: 140px; display: flex; align-items: center; justify-content: center;
        background: #fff; border: 1px dashed #cbd5e1; border-radius: 4px; margin: 6px 0; overflow: hidden;
    }
    .current-box img { max-width: 100%; max-height: 134px; object-fit: contain; }
    .missing-img-alert {
        padding: 12px; color: #991b1b; font-weight: 700; font-size: 12px; text-align: center;
    }

    .candidate-grid { display: flex; flex-wrap: wrap; gap: 12px; }
    .card {
        width: 210px; background: #fff; border: 2px solid #e5e7eb; border-radius: 8px;
        padding: 8px; text-align: center; cursor: pointer; position: relative;
        display: flex; flex-direction: column; justify-content: space-between;
    }
    .current-panel .card {
        width: 100%;
        margin-top: 8px;
        padding: 6px 8px;
    }
    .current-panel .thumb-box {
        height: 52px;
        margin-bottom: 4px;
    }
    .current-panel .na-box {
        padding: 6px 4px;
        font-size: 12px;
    }
    .current-panel .skip-box {
        font-size: 11px;
    }
    .current-panel .keep-box {
        font-size: 11px;
        padding: 4px;
    }
    .card:hover { border-color: #9ca3af; }
    .card.selected { border-color: #2563eb; background: #eff6ff; box-shadow: 0 0 0 2px rgba(37,99,235,0.18); }
    .card input[type="radio"] { display: none; }
    .hotkey {
        position: absolute; top: 5px; left: 5px; background: rgba(17,24,39,0.78);
        color: #fff; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
    }
    .thumb-box {
        height: 170px; display: flex; align-items: center; justify-content: center;
        background: #f9fafb; border-radius: 4px; margin-bottom: 6px; overflow: hidden; border: 1px solid #f3f4f6;
    }
    .thumb-box img { max-width: 100%; max-height: 166px; object-fit: contain; }
    .na-box {
        width: 100%; height: 100%; background: #fef2f2; color: #991b1b;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        padding: 10px; font-weight: 700; font-size: 13px; border: 1px dashed #f87171;
    }
    .skip-box {
        width: 100%; height: 100%; background: #f3f4f6; color: #4b5563;
        display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px;
    }
    .keep-box {
        width: 100%; height: 100%; background: #f0fdf4; color: #166534;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        padding: 6px 4px; font-weight: 700; font-size: 12px; border: 1px dashed #4ade80;
    }
    .card-title {
        font-size: 11px; color: #374151; white-space: nowrap; overflow: hidden;
        text-overflow: ellipsis; margin-bottom: 3px; font-weight: 600;
    }
    .source-tag {
        display: inline-block; font-size: 10px; font-weight: 700; padding: 2px 6px;
        border-radius: 3px; background: #e0e7ff; color: #1e3a8a; max-width: 100%;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .source-tag.wiki { background: #dcfce7; color: #14532d; }
    .dims { font-size: 11px; color: #6b7280; margin-top: 2px; }
    .dims.low-res { color: #b45309; font-weight: 700; }
    .card-links { font-size: 11px; display: flex; justify-content: center; gap: 8px; margin-top: 4px; }
    .card-links a { color: #2563eb; text-decoration: none; }
    .status-box {
        padding: 18px; background: #f9fafb; border: 1px dashed #d1d5db;
        border-radius: 6px; color: #4b5563; font-size: 13px; text-align: center; min-width: 260px;
    }
    #submitBar {
        position: fixed; bottom: 0; left: 0; right: 0; background: #fff;
        padding: 12px 24px; box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
        display: flex; justify-content: space-between; align-items: center; z-index: 100; border-top: 1px solid #e5e7eb;
    }
</style>
</head>
<body>

<div class="top-header">
    <div>
        <h1>RecipeDB 3.0 Image Validation & Mapping Tool</h1>
        <p>Input: <strong>{{ input_csv }}</strong> | Output: <strong>{{ output_csv }}</strong> | View: <strong>{{ mode_label }}</strong></p>
    </div>
    <div class="progress-panel">
        <span style="font-size:13px; font-weight:600;">Reviewed {{ reviewed_count }} of {{ total_count }} ({{ progress_pct }}%)</span>
        <div class="progress-bar"><div class="progress-fill" style="width: {{ progress_pct }}%;"></div></div>
        {% if page > 1 %}
        <a class="btn" href="{{ url_for('index', page=page-1, filter=filter_mode) }}">Previous Batch</a>
        {% endif %}
        <span class="badge">Batch {{ page }} / {{ total_pages }}</span>
        {% if page < total_pages %}
        <a class="btn" href="{{ url_for('index', page=page+1, filter=filter_mode) }}">Next Batch</a>
        {% endif %}
        {% if filter_mode == 'unreviewed' %}
        <a class="btn" href="{{ url_for('index', page=1, filter='all') }}">Show All Recipes</a>
        {% else %}
        <a class="btn" href="{{ url_for('index', page=1, filter='unreviewed') }}">Show Unreviewed Only</a>
        {% endif %}
    </div>
</div>

{% if errors %}
<div class="error-banner">
    <strong>Download validation failed for the following selections (kept in unreviewed queue):</strong>
    <ul style="margin:6px 0 0 18px; padding:0;">
        {% for e in errors %}<li>{{ e }}</li>{% endfor %}
    </ul>
</div>
{% endif %}

{% if not items %}
<div class="recipe-row" style="text-align:center; padding:40px;">
    <h2>No recipes remaining in this view.</h2>
    <p>Reviewed {{ reviewed_count }} of {{ total_count }} total recipes.</p>
    {% if filter_mode == 'unreviewed' and total_count > 0 %}
    <a class="btn btn-primary" href="{{ url_for('index', page=1, filter='all') }}">View All Recipes</a>
    {% endif %}
</div>
{% else %}

<form id="batchForm" method="POST" action="{{ url_for('submit') }}">
    <input type="hidden" name="current_page" value="{{ page }}">
    <input type="hidden" name="filter_mode" value="{{ filter_mode }}">

    {% for item in items %}
    <div class="recipe-row" id="row_{{ item.recipe_id }}" data-recipe-id="{{ item.recipe_id }}" onclick="setActiveRow('{{ item.recipe_id }}')">
        <input type="hidden" name="recipe_ids" value="{{ item.recipe_id }}">

        <div class="recipe-banner">
            <div>
                <h2>{{ loop.index + ((page - 1) * batch_size) }}. {{ item.recipe_name }}</h2>
                <div class="badges">
                    <span class="badge">ID: {{ item.recipe_id }}</span>
                    <span class="badge badge-cuisine">Cuisine: {{ item.cuisine or 'N/A' }}</span>
                    <span class="badge badge-category">Category: {{ item.category or 'N/A' }}</span>
                    <span class="badge badge-reason">{{ item.reason }}</span>
                    <span class="badge" id="qbadge_{{ item.recipe_id }}">Query: {{ item.primary_query }}</span>
                    {% if item.reviewed %}
                    <span class="badge badge-done">Reviewed: {{ item.source }}</span>
                    {% endif %}
                    {% if item.recipe_page_url %}
                    <a class="badge" href="{{ item.recipe_page_url }}" target="_blank" style="color:#2563eb; text-decoration:none;">Recipe URL</a>
                    {% endif %}
                </div>
                {% if item.context_snippet %}
                <div class="context-line">Context: {{ item.context_snippet }}</div>
                {% endif %}
            </div>
        </div>

        <div class="controls-bar">
            <div class="control-group">
                <input type="text" id="qinput_{{ item.recipe_id }}" value="{{ item.primary_query }}"
                       onkeydown="if(event.key==='Enter'){event.preventDefault(); searchRow('{{ item.recipe_id }}');}">
                <button type="button" class="btn btn-primary" onclick="searchRow('{{ item.recipe_id }}')">Search</button>
            </div>
            <div class="control-group">
                <input type="text" id="murl_{{ item.recipe_id }}" placeholder="Paste direct image URL..."
                       onkeydown="if(event.key==='Enter'){event.preventDefault(); addManualCard('{{ item.recipe_id }}');}">
                <button type="button" class="btn" onclick="addManualCard('{{ item.recipe_id }}')">Use URL</button>
            </div>
        </div>

        <div class="workspace">
            <div class="current-panel">
                <div class="current-status-banner">Flagged DB Image</div>
                <div class="current-box">
                    {% if item.image_url %}
                    <img src="{{ item.image_url }}" alt="Flagged DB Image"
                         onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                    <div class="missing-img-alert" style="display:none;">Broken External Link</div>
                    {% else %}
                    <div class="missing-img-alert">No Image in DB</div>
                    {% endif %}
                </div>
                <div style="font-size:11px; color:#475569; margin-bottom:6px; font-weight:600;">
                    image_id: {{ item.image_id or 'None' }} | na_image: {{ item.na_image or '1' }}
                </div>

                <div class="current-options" style="margin-top: 8px; border-top: 1px solid #e2e8f0; padding-top: 8px;">
                    <div style="font-size: 10px; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 4px; letter-spacing: 0.05em;">
                        Quick Actions
                    </div>
                    <!-- Skip / Leave Unreviewed Card -->
                    <div class="card {% if not item.reviewed %}selected{% endif %}"
                         id="skip_{{ item.recipe_id }}" onclick="selectCard(this)">
                        <span class="hotkey">S</span>
                        <div class="thumb-box"><div class="skip-box">Skip / Decide Later</div></div>
                        <div class="card-title">Leave Unreviewed</div>
                        <input type="radio" name="decision_{{ item.recipe_id }}"
                               value='{"action": "skip"}' {% if not item.reviewed %}checked{% endif %}>
                    </div>

                    <!-- No Suitable Image (NA) Card -->
                    <div class="card {% if item.reviewed and item.final_image_url == 'NA' %}selected{% endif %}"
                         id="na_{{ item.recipe_id }}" onclick="selectCard(this)">
                        <span class="hotkey">N</span>
                        <div class="thumb-box">
                            <div class="na-box">
                                <div>No Image (NA)</div>
                                <small style="font-weight:400; margin-top:2px;">Mark as NA</small>
                            </div>
                        </div>
                        <div class="card-title">No Suitable Image</div>
                        <input type="radio" name="decision_{{ item.recipe_id }}"
                               value='{"action": "na", "url": "NA", "source": "NA", "page_url": "NA"}'
                               {% if item.reviewed and item.final_image_url == 'NA' %}checked{% endif %}>
                    </div>

                    {% if item.image_url %}
                    <!-- Keep Current DB Image Card -->
                    <div class="card {% if item.reviewed and item.source == 'DB Original' %}selected{% endif %}"
                         id="keep_{{ item.recipe_id }}" onclick="selectCard(this)">
                        <span class="hotkey">C</span>
                        <div class="thumb-box">
                            <div class="keep-box">
                                <div>Keep Current</div>
                                <small style="font-weight:400; margin-top:2px;">Use DB image</small>
                            </div>
                        </div>
                        <div class="card-title">Keep Current Image</div>
                        <input type="radio" name="decision_{{ item.recipe_id }}"
                               value='{"action": "keep_current", "url": {{ item.image_url|tojson }}, "thumb_url": {{ item.image_url|tojson }}, "source": "DB Original", "page_url": {{ (item.recipe_page_url or item.image_url)|tojson }}}'
                               {% if item.reviewed and item.source == 'DB Original' %}checked{% endif %}>
                    </div>
                    {% endif %}
                </div>
            </div>

            <div class="candidate-grid" id="grid_{{ item.recipe_id }}">
                {% if item.reviewed and item.final_image_url != 'NA' %}
                <div class="card selected" onclick="selectCard(this)">
                    <div class="thumb-box"><img src="{{ item.final_image_url }}" alt="Saved Selection"></div>
                    <div class="card-title">Saved ({{ item.source }})</div>
                    <input type="radio" name="decision_{{ item.recipe_id }}"
                           value='{{ {"action": "candidate", "url": item.final_image_url, "thumb_url": item.final_image_url, "source": item.source, "page_url": item.source_page_url}|tojson }}' checked>
                </div>
                {% endif %}

                <div class="status-box" id="status_{{ item.recipe_id }}">Loading candidates...</div>
            </div>
        </div>
    </div>
    {% endfor %}

    <div id="submitBar">
        <div style="font-size:12px; color:#4b5563;">
            <strong>Hotkeys:</strong> <strong>J / K</strong> Next/Prev Row | <strong>1-9, 0</strong> Select Candidate 1-10 | <strong>N</strong> Mark NA | <strong>S</strong> Skip | <strong>C</strong> Keep Current Image
        </div>
        <button type="submit" id="submitBtn" class="btn btn-success">Save Selections & Load Next Batch</button>
    </div>
</form>
{% endif %}

<script>
    let activeRecipeId = null;
    let isDirty = false;

    window.addEventListener('beforeunload', function(e) {
        if (isDirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    const formEl = document.getElementById('batchForm');
    if (formEl) {
        formEl.addEventListener('submit', function() {
            isDirty = false;
            const btn = document.getElementById('submitBtn');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Downloading & Saving...';
            }
        });
    }

    function handleCandidateImgError(imgEl, fallbackUrl) {
        if (fallbackUrl && imgEl.src !== fallbackUrl && !imgEl.dataset.triedFallback) {
            imgEl.dataset.triedFallback = '1';
            imgEl.src = fallbackUrl;
        } else {
            const card = imgEl.closest('.card');
            if (!card) return;
            const row = card.closest('.recipe-row');
            const grid = card.closest('.candidate-grid');
            const wasSelected = card.classList.contains('selected');
            card.remove();

            if (wasSelected && row) {
                const skipCard = document.getElementById('skip_' + row.dataset.recipeId);
                if (skipCard) selectCard(skipCard);
            }
            if (grid && grid.querySelectorAll('.remote-card').length === 0 && row) {
                const status = document.getElementById('status_' + row.dataset.recipeId);
                if (status) {
                    status.style.display = 'block';
                    status.textContent = 'All candidate previews failed to load. Try another search or mark as No Image (NA).';
                }
            }
        }
    }

    function setActiveRow(recipeId, scroll = false) {
        activeRecipeId = recipeId;
        document.querySelectorAll('.recipe-row').forEach(r => r.classList.remove('active-row'));
        const row = document.getElementById('row_' + recipeId);
        if (row) {
            row.classList.add('active-row');
            if (scroll) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    function selectCard(card) {
        const row = card.closest('.recipe-row');
        if (!row) return;
        const rid = row.dataset.recipeId;
        setActiveRow(rid);

        const radio = card.querySelector('input[type="radio"]');
        if (radio) radio.checked = true;

        row.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        isDirty = true;
    }

    function triggerCard(cardId) {
        const card = document.getElementById(cardId);
        if (card) card.click();
    }

    function addCustomCard(rid, actionType, url, source, pageUrl) {
        const grid = document.getElementById('grid_' + rid);
        const existing = document.getElementById('custom_' + rid);
        if (existing) existing.remove();

        const card = document.createElement('div');
        card.className = 'card';
        card.id = 'custom_' + rid;
        card.onclick = function() { selectCard(card); };

        const payload = JSON.stringify({ action: actionType, url: url, thumb_url: url, source: source, page_url: pageUrl });
        card.innerHTML = `
            <div class="thumb-box">
                <img src="${url}" alt="Custom URL" referrerpolicy="no-referrer" onerror="this.alt='Preview failed to load'">
            </div>
            <div class="card-title">${source}</div>
            <input type="radio" name="decision_${rid}">
        `;
        card.querySelector('input').value = payload;
        grid.insertBefore(card, grid.firstChild);
        selectCard(card);
    }

    function addManualCard(rid) {
        const input = document.getElementById('murl_' + rid);
        const url = (input.value || '').trim();
        if (!url) return;
        addCustomCard(rid, 'manual', url, 'Manual URL', url);
    }

    function searchRow(rid) {
        const input = document.getElementById('qinput_' + rid);
        const q = (input.value || '').trim();
        if (q) loadCandidates(rid, q);
    }

    async function loadCandidates(rid, customQuery = null) {
        const grid = document.getElementById('grid_' + rid);
        const status = document.getElementById('status_' + rid);
        const qbadge = document.getElementById('qbadge_' + rid);
        if (!grid) return;

        grid.querySelectorAll('.remote-card').forEach(c => c.remove());
        if (status) {
            status.style.display = 'block';
            status.textContent = customQuery ? `Searching "${customQuery}"...` : 'Searching Wikimedia & DuckDuckGo...';
        }

        let endpoint = `/api/candidates?recipe_id=${encodeURIComponent(rid)}`;
        if (customQuery) endpoint += `&q=${encodeURIComponent(customQuery)}`;

        try {
            const resp = await fetch(endpoint);
            const data = await resp.json();
            if (qbadge && data.query) {
                qbadge.textContent = (data.used_fallback ? 'Fallback: ' : 'Query: ') + data.query;
            }
            if (!data.candidates || data.candidates.length === 0) {
                if (status) status.textContent = 'No candidates found. Try a simpler search query or mark as No Image (NA).';
                return;
            }
            if (status) status.style.display = 'none';

            data.candidates.forEach((c, idx) => {
                const num = idx + 1;
                const hotkey = num === 10 ? '0' : String(num);
                const lowRes = (c.width > 0 && c.width < 400) || (c.height > 0 && c.height < 400);
                const dimsText = (c.width && c.height) ? `${c.width}x${c.height}${lowRes ? ' (Low Res)' : ''}` : c.source;
                const thumbUrl = c.thumb_url || c.image_url;
                const payload = JSON.stringify({
                    action: 'candidate',
                    url: c.image_url,
                    thumb_url: thumbUrl,
                    source: c.source,
                    page_url: c.page_url || c.image_url
                });

                const card = document.createElement('div');
                card.className = 'card remote-card';
                card.dataset.hotkey = hotkey;
                card.onclick = function() { selectCard(card); };
                card.innerHTML = `
                    <span class="hotkey">${hotkey}</span>
                    <div class="thumb-box">
                        <img src="${thumbUrl}" alt="Candidate ${num}" loading="lazy" referrerpolicy="no-referrer">
                    </div>
                    <div class="card-title" title="${c.title || ''}">${c.title || c.domain}</div>
                    <div><span class="source-tag ${c.source === 'Wikimedia Commons' ? 'wiki' : ''}">${c.domain}</span></div>
                    <div class="dims ${lowRes ? 'low-res' : ''}">${dimsText}</div>
                    <div class="card-links">
                        <a href="${c.image_url}" target="_blank" onclick="event.stopPropagation()">Full Size</a>
                        <a href="${c.page_url || c.image_url}" target="_blank" onclick="event.stopPropagation()">Source</a>
                    </div>
                    <input type="radio" name="decision_${rid}">
                `;
                const imgEl = card.querySelector('img');
                imgEl.onerror = function() { handleCandidateImgError(imgEl, c.image_url); };
                card.querySelector('input').value = payload;
                grid.appendChild(card);
            });
        } catch (err) {
            if (status) {
                status.style.display = 'block';
                status.innerHTML = `Search failed. <button type="button" class="btn" onclick="loadCandidates('${rid}')">Retry</button>`;
            }
        }
    }

    document.addEventListener('keydown', function(e) {
        if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
        const rows = Array.from(document.querySelectorAll('.recipe-row[data-recipe-id]'));
        if (!rows.length) return;
        if (!activeRecipeId) setActiveRow(rows[0].dataset.recipeId);

        const idx = rows.findIndex(r => r.dataset.recipeId === activeRecipeId);
        if (e.key === 'j' || e.key === 'ArrowDown') {
            e.preventDefault();
            const next = rows[Math.min(rows.length - 1, idx + 1)];
            if (next) setActiveRow(next.dataset.recipeId, true);
        } else if (e.key === 'k' || e.key === 'ArrowUp') {
            e.preventDefault();
            const prev = rows[Math.max(0, idx - 1)];
            if (prev) setActiveRow(prev.dataset.recipeId, true);
        } else if (e.key.toLowerCase() === 'n') {
            e.preventDefault();
            triggerCard('na_' + activeRecipeId);
        } else if (e.key.toLowerCase() === 's') {
            e.preventDefault();
            triggerCard('skip_' + activeRecipeId);
        } else if (e.key.toLowerCase() === 'c') {
            e.preventDefault();
            triggerCard('keep_' + activeRecipeId);
        } else if (e.key >= '0' && e.key <= '9') {
            e.preventDefault();
            const grid = document.getElementById('grid_' + activeRecipeId);
            const target = grid ? grid.querySelector(`.remote-card[data-hotkey="${e.key}"]`) : null;
            if (target) target.click();
        }
    });

    document.addEventListener('DOMContentLoaded', async function() {
        const rows = Array.from(document.querySelectorAll('.recipe-row[data-recipe-id]'));
        if (rows.length > 0) setActiveRow(rows[0].dataset.recipeId);
        for (const r of rows) {
            await loadCandidates(r.dataset.recipeId);
        }
    });
</script>
</body>
</html>
"""


# --- Flask Routes ---
@app.route("/api/candidates")
def api_candidates():
    rid = str(request.args.get("recipe_id") or "").strip()
    custom_q = str(request.args.get("q") or "").strip()
    recipe = RECIPES.get(rid)
    if not recipe:
        return jsonify({"error": "Not found", "candidates": []}), 404

    candidates, executed_q, used_fb = fetch_candidates(recipe, custom_q or None)
    return jsonify({
        "recipe_id": rid,
        "query": executed_q,
        "used_fallback": used_fb,
        "candidates": candidates,
    })


@app.route("/")
def index():
    global LAST_ERRORS
    filter_mode = request.args.get("filter", "unreviewed").strip().lower()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    errors = list(LAST_ERRORS)
    LAST_ERRORS.clear()

    total_count = len(RECIPES)
    reviewed_count = sum(1 for r in RECIPES.values() if r["reviewed"])
    progress_pct = round((reviewed_count / total_count) * 100, 1) if total_count else 0.0

    if filter_mode == "all":
        pool = list(RECIPES.values())
        mode_label = "All Recipes (Review & Edit)"
    else:
        pool = sorted(
            (r for r in RECIPES.values() if not r["reviewed"]),
            key=lambda r: r["skip_count"],
        )
        mode_label = "Pending Unreviewed Queue"

    total_pages = max(1, (len(pool) + BATCH_SIZE - 1) // BATCH_SIZE)
    page = min(page, total_pages)
    start = (page - 1) * BATCH_SIZE
    batch_items = pool[start : start + BATCH_SIZE]

    return render_template_string(
        HTML_TEMPLATE,
        items=batch_items,
        input_csv=INPUT_CSV,
        output_csv=OUTPUT_CSV,
        reviewed_count=reviewed_count,
        total_count=total_count,
        progress_pct=progress_pct,
        page=page,
        total_pages=total_pages,
        batch_size=BATCH_SIZE,
        filter_mode=filter_mode,
        mode_label=mode_label,
        errors=errors,
    )


@app.route("/submit", methods=["POST"])
def submit():
    global LAST_ERRORS
    recipe_ids = request.form.getlist("recipe_ids")
    filter_mode = request.form.get("filter_mode", "unreviewed")
    try:
        current_page = max(1, int(request.form.get("current_page", 1)))
    except ValueError:
        current_page = 1

    saved_any = False
    skipped_ids = []
    batch_errors = []

    for rid in recipe_ids:
        rid = str(rid).strip()
        recipe = RECIPES.get(rid)
        if not recipe:
            continue

        raw_decision = request.form.get(f"decision_{rid}", "")
        try:
            payload = json.loads(raw_decision) if raw_decision else {"action": "skip"}
        except Exception:
            payload = {"action": "skip"}

        action = payload.get("action", "skip")
        if action == "skip":
            if not recipe["reviewed"]:
                skipped_ids.append(rid)
            continue

        url = str(payload.get("url") or "").strip()
        thumb_url = str(payload.get("thumb_url") or "").strip()
        source = str(payload.get("source") or "").strip()
        page_url = str(payload.get("page_url") or url).strip()
        if not url:
            continue

        if action == "na" or url == "NA":
            recipe.update({
                "source": "NA",
                "source_page_url": "NA",
                "new_image_id": "NA",
                "final_image_url": "NA",
                "local_file_path": "NA",
                "reviewed": True,
            })
            saved_any = True

        elif action == "keep_current":
            ok, local_path_or_err, used_url = download_image(
                url, rid, recipe["recipe_name"], page_url=page_url, thumb_url=thumb_url
            )
            if not ok:
                batch_errors.append(f"Recipe {rid} ({recipe['recipe_name']}): {local_path_or_err}")
                continue
            recipe.update({
                "source": "DB Original",
                "source_page_url": page_url,
                "new_image_id": f"rdb3_img_{rid}",
                "final_image_url": used_url,
                "local_file_path": local_path_or_err,
                "reviewed": True,
            })
            saved_any = True

        elif (
            recipe["reviewed"]
            and url == recipe["final_image_url"]
            and recipe["local_file_path"]
            and os.path.exists(recipe["local_file_path"])
        ):
            # Unchanged selection in 'Show All Recipes' view; skip redundant re-download
            continue

        else:
            ok, local_path_or_err, used_url = download_image(
                url, rid, recipe["recipe_name"], page_url=page_url, thumb_url=thumb_url
            )
            if not ok:
                batch_errors.append(f"Recipe {rid} ({recipe['recipe_name']}): {local_path_or_err}")
                continue
            recipe.update({
                "source": source or "DuckDuckGo",
                "source_page_url": page_url,
                "new_image_id": f"rdb3_img_{rid}",
                "final_image_url": used_url,
                "local_file_path": local_path_or_err,
                "reviewed": True,
            })
            saved_any = True

    for sid in skipped_ids:
        RECIPES[sid]["skip_count"] += 1

    if saved_any:
        sync_output_csv()

    LAST_ERRORS = batch_errors
    next_page = current_page + 1 if filter_mode == "all" else current_page
    return redirect(url_for("index", page=next_page, filter=filter_mode))


def main():
    global INPUT_CSV, OUTPUT_CSV, IMAGES_DIR
    parser = argparse.ArgumentParser(description="RecipeDB 3.0 Image Validation Server")
    parser.add_argument("input_csv", nargs="?", default=None, help="Path to input CSV file")
    parser.add_argument("--output", default=OUTPUT_CSV, help="Path to output CSV file")
    parser.add_argument("--images-dir", default=IMAGES_DIR, help="Directory for downloaded images")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    args = parser.parse_args()

    if args.input_csv:
        INPUT_CSV = args.input_csv
    elif not os.path.exists(INPUT_CSV) and os.path.exists("sample_records.csv"):
        INPUT_CSV = "sample_records.csv"

    OUTPUT_CSV = args.output
    IMAGES_DIR = args.images_dir
    os.makedirs(IMAGES_DIR, exist_ok=True)

    load_datasets()
    print("=" * 56)
    print("RecipeDB 3.0 Image Validation & Mapping Server")
    print(f"Input CSV  : {INPUT_CSV}")
    print(f"Output CSV : {OUTPUT_CSV}")
    print(f"Images Dir : {IMAGES_DIR}/")
    print(f"Server URL : http://127.0.0.1:{args.port}")
    print("=" * 56)
    app.run(debug=True, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()