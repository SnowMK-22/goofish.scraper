# Goofish Scraping API

Microservicio en **FastAPI** para extraer información detallada de productos de [Goofish (闲鱼)](https://www.goofish.com), el marketplace de segunda mano de Alibaba.

---

## Estructura del proyecto

```
goofish-scraper/
├── main.py           # Configuración de FastAPI y endpoints
├── scraping.py       # Lógica de scraping (scrape_pdp)
├── batch_scraper.py  # Script para procesar el CSV de 50k URLs
├── requirements.txt  # Dependencias
└── README.md
```

---

## Instalación

```bash
pip install -r requirements.txt
```

---

## Levantar el servidor

```bash
python main.py
# o bien:
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

La documentación interactiva estará disponible en: `http://localhost:8080/docs`

---

## Uso de la API

### `GET /scrapePDP`

Extrae los datos de un producto de Goofish dado su URL.

**Parámetros:**

| Nombre | Tipo   | Descripción                          |
|--------|--------|--------------------------------------|
| `url`  | string | URL del producto en Goofish          |

**Ejemplo:**

```bash
curl "http://localhost:8080/scrapePDP?url=https://www.goofish.com/item?id=738399182847"
```

**Respuesta:**

```json
[
  {
    "ITEM_ID": "738399182847",
    "CATEGORY_ID": "50016217",
    "TITLE": "iPhone 13 Pro 256GB Sierra Blue",
    "IMAGES": ["https://img.goofish.com/..."],
    "SOLD_PRICE": 3200.0,
    "BROWSE_COUNT": 1540,
    "WANT_COUNT": 23,
    "COLLECT_COUNT": 10,
    "QUANTITY": 1,
    "GMT_CREATE": "2024-03-15 14:22:00",
    "SELLER_ID": "123456789"
  }
]
```

---

## Campos extraídos

| Campo          | Descripción                                      |
|----------------|--------------------------------------------------|
| `ITEM_ID`      | Identificador único del producto                 |
| `CATEGORY_ID`  | Identificador de la categoría                    |
| `TITLE`        | Título del producto                              |
| `IMAGES`       | Lista de URLs de imágenes                        |
| `SOLD_PRICE`   | Precio de venta                                  |
| `BROWSE_COUNT` | Número de visualizaciones                        |
| `WANT_COUNT`   | Usuarios que marcaron "Lo quiero"                |
| `COLLECT_COUNT`| Usuarios que añadieron a favoritos               |
| `QUANTITY`     | Cantidad disponible                              |
| `GMT_CREATE`   | Fecha y hora de publicación                      |
| `SELLER_ID`    | Identificador único del vendedor                 |

---

## Procesamiento en batch (50k URLs)

```bash
python batch_scraper.py \
  --input goofish_urls.csv \
  --output results.csv \
  --workers 10
```

El script:
- Evita procesar la misma URL dos veces (caché en disco).
- Guarda los URLs fallidos para no desperdiciar créditos del proxy.
- Escribe los resultados incrementalmente (resistente a caídas).

---

## Configuración del proxy

Las credenciales del proxy NetNut se configuran en `scraping.py`:

```python
PROXY_URL = "http://codify-dc-any:58ADAB79s03h8TJ@gw.netnut.net:5959"
```

> ⚠️ Antes de usar el proxy, envía un correo a `info@iceberg-data.com`
> indicando el volumen estimado en GBs, los dominios a visitar y tu IP pública.

---

## Configuración de cookies (autenticación)

Goofish requiere cookies de sesión válidas. Para obtenerlas:

1. Abre [goofish.com](https://www.goofish.com) en Chrome.
2. Inicia sesión con tu cuenta.
3. Abre DevTools → Application → Cookies → `www.goofish.com`.
4. Copia los valores de `_m_h5_tk`, `_m_h5_tk_enc`, `cna`, `unb`.
5. Pégalos en la función `_get_session_cookies()` en `scraping.py`.

---

## Estrategia técnica

1. **Intento 1 – HTML**: Se intenta extraer `window.__INIT_DATA__` del HTML de la página (sin necesidad de cookies, más barato en proxy).
2. **Intento 2 – API interna**: Si el HTML no contiene los datos, se llama al endpoint interno `mtop.taobao.idle.pc.detail` con firma MD5 (`token + timestamp + appKey + payload`).
3. **Caché en memoria + disco**: Evita requests duplicados dentro y entre sesiones.
4. **Errores permanentes**: Los 404 se registran en `failed_urls.txt` y no se reintenta.