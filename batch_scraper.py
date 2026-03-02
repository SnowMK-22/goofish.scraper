import argparse
import csv
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from scraping import scrape_pdp, _extract_item_id, _memory_cache, DISK_CACHE_PATH
import shelve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("batch_scraper.log"),
    ],
)
logger = logging.getLogger(__name__)
OUTPUT_FIELDS = [
    "URL",
    "ITEM_ID",
    "CATEGORY_ID",
    "TITLE",
    "IMAGES",
    "SOLD_PRICE",
    "BROWSE_COUNT",
    "WANT_COUNT",
    "COLLECT_COUNT",
    "QUANTITY",
    "GMT_CREATE",
    "SELLER_ID",
]

FAILED_CACHE_PATH = "failed_urls.txt"

def load_failed_urls() -> set:
    if not os.path.exists(FAILED_CACHE_PATH):
        return set()
    with open(FAILED_CACHE_PATH, "r") as f:
        return {line.strip() for line in f if line.strip()}


def save_failed_url(url: str):
    with open(FAILED_CACHE_PATH, "a") as f:
        f.write(url + "\n")


def already_scraped(item_id: str) -> bool:
    try:
        with shelve.open(DISK_CACHE_PATH) as db:
            return item_id in db
    except Exception:
        return False


def process_url(url: str) -> dict | None:
    result = scrape_pdp(url)
    if result:
        return result[0]
    return None


def run_batch(input_file: str, output_file: str, max_workers: int = 8, limit: int = 0):
    failed_urls = load_failed_urls()
    with open(input_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        url_col = next(
            (col for col in fieldnames if "url" in col.lower()),
            fieldnames[0] if fieldnames else "url",
        )
        all_rows = list(reader)

    logger.info(f"Total URLs en input: {len(all_rows)}")
    pending = []
    for row in all_rows:
        url = row.get(url_col, "").strip()
        if not url or url in failed_urls:
            continue
        item_id = _extract_item_id(url)
        if item_id and already_scraped(item_id):
            logger.debug(f"Ya scrapeado: {item_id}")
            continue
        pending.append(url)
        if limit and len(pending) >= limit:
            break

    logger.info(f"URLs pendientes a scrapear: {len(pending)}")
    file_exists = os.path.exists(output_file)
    outfile = open(output_file, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
    if not file_exists:
        writer.writeheader()

    success_count = 0
    error_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(process_url, url): url for url in pending}

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                product = future.result()
                if product:
                    product_row = {**product, "URL": url}
                    product_row["IMAGES"] = "|".join(product.get("IMAGES", []))
                    writer.writerow(product_row)
                    outfile.flush()
                    success_count += 1

                    elapsed = time.time() - start_time
                    rate = success_count / elapsed * 60
                    logger.info(
                        f"✅ [{success_count}] {product.get('ITEM_ID')} | "
                        f"{rate:.1f} productos/min | errores: {error_count}"
                    )
                else:
                    error_count += 1
                    save_failed_url(url)

            except Exception as e:
                error_count += 1
                save_failed_url(url)
                logger.error(f"❌ Error procesando {url}: {e}")

    outfile.close()

    elapsed = time.time() - start_time
    logger.info(
        f"\n{'='*60}\n"
        f"RESUMEN FINAL\n"
        f"  Exitosos:  {success_count}\n"
        f"  Errores:   {error_count}\n"
        f"  Tiempo:    {elapsed/60:.1f} minutos\n"
        f"  Velocidad: {success_count/(elapsed/60):.1f} productos/min\n"
        f"{'='*60}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch scraper para Goofish URLs")
    parser.add_argument("--input", default="goofish_urls.csv", help="CSV de entrada con URLs")
    parser.add_argument("--output", default="results.csv", help="CSV de salida con datos extraídos")
    parser.add_argument("--workers", type=int, default=8, help="Número de hilos concurrentes")
    parser.add_argument("--limit", type=int, default=0, help="Límite de URLs a procesar (0 = todas)")
    args = parser.parse_args()

    run_batch(args.input, args.output, args.workers, args.limit)