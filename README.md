# RecipeDB 3.0 Image Review & Validation Tool

A high-performance, keyboard-driven web curation server designed to review, validate, and replace recipe images for **RecipeDB 3.0**.

---

## Features

- **Automated Multi-Engine Search**: Automatically queries **Wikimedia Commons** and **DuckDuckGo** using composite queries (`recipe name + cuisine + category`).
- **High-Speed Thumbnail Previews**: Renders lightweight, cached proxy thumbnails for instant loading with automatic fallback to origin full-resolution URLs.
- **Side-by-Side Comparison**: Displays the current database image, flagged issues (`na_image = 1`, broken external links, generic fallback graphics), and candidate cards.
- **Fast Keyboard Navigation**: Full hotkey support for rapid reviews (`J`/`K` navigation, `1`–`9`/`0` selection, `S` to skip, `N` for No Image).
- **Resilient Image Downloader**: Built-in desktop browser headers, Referer matching, and automated fallback to proxy thumbnails to avoid HTTP 403 / dropped connection failures.
- **Deterministic Output**: Preserves exact 1:1 input CSV row ordering while supporting FIFO queue rotation for skipped items.
- **Session Recovery**: Automatically saves decisions and resumes unreviewed queues without lost progress.

---

## Installation

Ensure you have Python 3.8+ installed. Install the required dependencies:

```bash
pip install flask duckduckgo-search
```

---

## Usage

Run the server with your input CSV file:

```bash
python3 review_server.py recipes_to_review.csv
```

### CLI Options

| Argument | Description | Default |
|---|---|---|
| `input_csv` | Path to the input CSV file containing flagged recipes | `recipes_to_review.csv` (or `sample_records.csv`) |
| `--port` | Local server port | `5000` |
| `--output` | Destination path for the reviewed CSV | `reviewed_recipes_output.csv` |
| `--images-dir` | Folder to save downloaded high-res images | `downloaded_recipe_images` |

Once started, open your browser at:
```
http://127.0.0.1:5000
```

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| **`J`** / **`↓`** | Move to next recipe row |
| **`K`** / **`↑`** | Move to previous recipe row |
| **`1` – `9`**, **`0`** | Select Candidate 1 through 10 |
| **`S`** | Skip / Leave Unreviewed (rotates to the back of the queue) |
| **`N`** | Mark as No Suitable Image (NA) |

---

## Output Data Contract

The tool generates `reviewed_recipes_output.csv` with the following 9 columns:

1. `recipe_id` — Primary identifier matching the database.
2. `recipe_name` — Cleaned recipe title.
3. `cuisine` — Regional cuisine taxonomy.
4. `category` — Course or culinary category.
5. `source` — Provenance (`Wikimedia Commons`, `DuckDuckGo`, `Existing DB Image`, or `NA`).
6. `source_page_url` — Attribution/origin webpage URL.
7. `new_image_id` — Standardized image asset ID (`rdb3_img_{recipe_id}`).
8. `final_image_url` — URL to approved image binary (or `NA`).
9. `local_file_path` — Relative path to the downloaded image file on disk (or `NA`).
