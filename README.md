# RecipeDB 3.0 — Image Validation & Mapping Tool

## 1. Overview

The **RecipeDB 3.0 Image Validation & Mapping Tool** (`review_server.py`) is an interactive web application used by human reviewers to fix and replace missing, broken, or mismatched recipe images across the RecipeDB 3.0 dataset (`recipedb3_mastersheet_csv_v22`, comprising 584,639 recipes).

The tool assumes that the input CSV contains pre-flagged recipes with missing or invalid images. It loads recipes in batches of 10 per page, displays the flagged database image alongside candidate replacement photos fetched asynchronously from Wikimedia Commons and DuckDuckGo, and allows the reviewer to select the best visual match, run custom searches, paste a direct image URL, skip a recipe for later, or mark it as having no suitable image (`NA`).

---

## 2. Directory & File Structure

```text
RecipeDB_image_review/
│
├── review_server.py               # Main Flask web application
├── README.md                      # User guide and documentation
├── requirements.txt               # Python package dependencies
│
├── recipes_to_review.csv          # Default input CSV (or custom path passed via CLI)
├── reviewed_recipes_output.csv    # Output CSV (auto-created and updated as you review)
│
└── downloaded_recipe_images/      # Folder where approved recipe images are saved
    └── 1001_chicken_tikka_masala.jpg
```

---

## 3. Prerequisites & Setup

### 3.1 System Requirements
* **Python**: Python 3.8 or higher
* **Internet Connection**: Required to query Wikimedia Commons and DuckDuckGo and to download approved images

### 3.2 Install Required Packages
Install Flask and the DuckDuckGo search library:

```bash
pip install flask ddgs
```

*(Or if using `requirements.txt`:)*

```bash
pip install -r requirements.txt
```

### 3.3 Automatic Directory Creation
When you start `review_server.py`, it automatically creates the `downloaded_recipe_images/` directory (and initializes `reviewed_recipes_output.csv` with all rows from your input CSV) if they do not already exist.

---

## 4. How to Run the Tool

### 4.1 Start the Server
Open a terminal in the project directory and run the script with your assigned input CSV file:

```bash
python3 review_server.py path/to/your_input_batch.csv
```

If no input file is specified, the tool looks for `recipes_to_review.csv` first, and falls back to `sample_records.csv` if present.



### 4.3 Open the Web Interface
Once the server starts, you will see:

```text
========================================================
RecipeDB 3.0 Image Validation & Mapping Server
Input CSV  : recipes_to_review.csv
Output CSV : reviewed_recipes_output.csv
Images Dir : downloaded_recipe_images/
Server URL : http://127.0.0.1:5000
========================================================
```

Open your web browser and navigate to:

```text
http://127.0.0.1:5000
```

---

## 5. User-Facing Features & Interface Controls

The interface is organized into a top navigation bar, 10 recipe cards per batch, and a sticky save bar at the bottom of the screen.

### 5.1 Progress Bar, Batch Pagination & Queue Filter (Top Bar)
* **Live Progress Counter**: Displays `Reviewed X of Y (Z%)` alongside a visual progress bar tracking how many recipes in your input CSV have been completed.
* **Batch Pagination (`Previous Batch` / `Next Batch`)**: Step backward and forward through batches of 10 recipes.
* **View Toggle (`Show Unreviewed Only` vs. `Show All Recipes`)**:
  * **Pending Unreviewed Queue (Default)**: Displays only recipes that have not yet been reviewed.
  * **All Recipes (Review & Edit)**: Displays every recipe in the input CSV (including completed rows marked with a green `Reviewed` badge) so you can inspect or overwrite a previous decision without re-downloading unchanged images.

### 5.2 Recipe Information Banner & Context Snippet
At the top of each recipe row, the banner shows:
* **Recipe Name**: The dish title and its item number in the current view.
* **Metadata Badges**:
  * `ID`: The unique `recipe_id`.
  * `Cuisine`: The regional cuisine classification (e.g., `Cajun And Creole`, `European`, `Asian`).
  * `Category`: The dish category (e.g., `Gumbo`, `Stew`, `Dessert`).
  * `Reason`: Why the recipe was flagged (e.g., `Missing image_url`, `na_image = 1`, or custom upstream notes).
  * `Query`: The active search query used to fetch the candidates (updates automatically to `Fallback: ...` if the primary query returned fewer than 3 results).
  * `Recipe URL`: If the input CSV includes the source `url` column, clicking this badge opens the original recipe page (such as Food.com) in a new tab.
* **Ingredients / Description Context**: If the input CSV includes `ingredients` or `description` columns, a 1-line summary is displayed directly underneath the badges to help clarify ambiguous recipe titles.

### 5.3 Flagged DB Image Panel & Quick Actions (Left Panel)
On the left side of each recipe workspace is the reference and action panel:
* **Flagged DB Image Box**: Displays the existing image at `image_url` (if present in the CSV) so you can see why it was flagged (e.g., a generic placeholder graphic or wrong dish), or displays `"No Image in DB"` / `"Broken External Link"`.
* **Quick Actions**:
  * **Skip / Decide Later (Hotkey `S`)**: Selected by default on unreviewed rows. Leaving a recipe on Skip keeps it unreviewed and rotates it to the back of the session queue so new recipes appear first on your next batch.
  * **No Image (NA) (Hotkey `N`)**: Select this card if no accurate photo of the recipe exists in the search results or online. This marks the recipe as reviewed with `NA` across the output image columns.

### 5.4 Candidate Image Grid (Right Panel)
On the right side of each recipe workspace is the candidate grid displaying up to 10 images from Wikimedia Commons and DuckDuckGo:
* **Click-to-Select Cards**: Clicking any candidate card highlights it in blue and selects it for download.
* **Automatic Broken-Preview Filtering**: If a candidate image link is dead or blocked from loading in your browser, that card is automatically removed from the grid so you only see working images.
* **Image Title & Source Domain Badges**: Shows the image title (hover to view full text) along with a green badge for `commons.wikimedia.org` or a blue badge showing the source website domain for DuckDuckGo results.
* **Image Resolution & Low-Res Warning**: Displays the pixel dimensions (`width x height`). If either dimension is smaller than 400px, it highlights the dimensions in amber with `(Low Res)` so you can avoid blurry thumbnails.
* **Full Size & Source Links**:
  * **Full Size**: Opens the full-resolution image in a new browser tab without changing your card selection.
  * **Source**: Opens the web page where the image was hosted to verify dish accuracy.

### 5.5 Custom Search Bar (In-Place Single-Row Refresh)
If the default search query does not return good candidates:
* Type a new search phrase into the search box on that recipe's row and click **Search** (or press `Enter`).
* **In-Place Refresh**: Only that single recipe's candidate grid reloads; all your selections on the other 9 recipes in the batch remain untouched.

### 5.6 Manual Image URL Input
If you locate a good image externally in another browser tab:
* Paste the direct image URL into the **`Paste direct image URL...`** box and click **`Use URL`** (or press `Enter`).
* A custom card displaying your pasted URL is inserted at the start of the candidate grid and selected automatically.

### 5.7 Keyboard Shortcuts (Hotkeys)
You can review an entire batch using the keyboard:
* **`J`** or **`Down Arrow`**: Move focus to the next recipe row and scroll it into the center of the screen.
* **`K`** or **`Up Arrow`**: Move focus to the previous recipe row and scroll it into the center of the screen.
* **`1` – `9`**: Select Candidate Card 1 through 9 in the active row.
* **`0`**: Select Candidate Card 10 in the active row.
* **`N`**: Select No Image (NA) in the active row.
* **`S`**: Select Skip / Decide Later in the active row.

---

## 6. Step-by-Step Reviewer Workflow

1. **Inspect the Recipe Banner & Context**:
   Read the recipe name, cuisine, category, and 1-line ingredient/description context. Check the Flagged DB Image box on the left to see the broken or mismatched database image.
2. **Choose an Action for the Recipe**:
   * **Good match in the grid**: Click the best candidate card (or press `1`–`9` / `0`), favoring high-resolution images without the `(Low Res)` warning.
   * **Poor search results**: Enter simpler keywords in the search bar and click **Search**, or paste a direct image URL and click **Use URL**.
   * **No valid image exists online**: Click the **No Image (NA)** card under Quick Actions (or press `N`).
   * **Defer decision**: Leave **Skip / Decide Later** (or press `S`) selected.
3. **Move Through all 10 Recipes in the Batch**:
   Scroll down or press `J` / `K` to step through the remaining rows on the page.
4. **Save the Batch**:
   Click **Save Selections & Load Next Batch** in the bottom bar.
   The server downloads each selected image to `downloaded_recipe_images/{recipe_id}_{slug}.{ext}` (automatically falling back to the cached thumbnail if a food blog blocks direct full-size downloads), verifies that the file is a valid image larger than 5 KB, and updates `reviewed_recipes_output.csv`.
5. **Handle Any Download Warnings**:
   If both the primary image URL and fallback thumbnail fail validation, a red banner at the top of the next page lists the affected recipe and keeps it in the unreviewed queue so you can pick an alternate image.
6. **Stop and Resume Anytime**:
   All decisions are saved to `reviewed_recipes_output.csv` after every batch submission. If you stop the server and restart it later, it automatically resumes from your remaining unreviewed recipes.

---

## 7. Input & Output CSV Data Contracts

### 7.1 Input CSV Columns
The tool accepts both pre-flagged review CSVs and raw RecipeDB 3.0 exports (`schema.csv` / `sample_records.csv`), automatically normalizing `recipe name` or `recipe_name`:

| Column Name | Required / Optional | Description |
|---|---|---|
| `recipe_id` | **Required** | Unique primary key identifier for the recipe |
| `recipe_name` (or `recipe name`) | **Required** | Display name/title of the recipe |
| `cuisine` | **Required** | Regional cuisine classification |
| `category` | **Required** | Dish category |
| `image_url` | Optional | Flagged/existing image URL in the database |
| `image_id` | Optional | Existing database image identifier |
| `na_image` | Optional | Binary/status flag (`1` = missing/invalid image) |
| `reason` | Optional | Upstream explanation of why the recipe was flagged |
| `url` | Optional | Link to the original source recipe web page |
| `ingredients` / `description` | Optional | Used to display the 1-line context snippet in the UI |

### 7.2 Output CSV Columns (`reviewed_recipes_output.csv`)
`reviewed_recipes_output.csv` maintains a complete 1:1 row mirror of the input CSV formatted with the 9 output columns:

```csv
recipe_id,recipe_name,cuisine,category,source,source_page_url,new_image_id,final_image_url,local_file_path
```

| Column Name | Description |
|---|---|
| `recipe_id` | Original unique recipe identifier |
| `recipe_name` | Name of the recipe |
| `cuisine` | Regional cuisine |
| `category` | Dish category |
| `source` | `Wikimedia Commons`, `DuckDuckGo`, `Manual URL`, or `NA` (blank if unreviewed) |
| `source_page_url` | Origin web page URL for attribution, or `NA` |
| `new_image_id` | `rdb3_img_<recipe_id>` for downloaded images, or `NA` |
| `final_image_url` | Direct URL of the downloaded image, or `NA` (blank if unreviewed) |
| `local_file_path` | Relative path in `downloaded_recipe_images/<id>_<slug>.<ext>`, or `NA` |