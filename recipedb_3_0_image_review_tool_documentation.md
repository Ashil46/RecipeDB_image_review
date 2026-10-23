# RecipeDB 3.0 — Image Validation & Mapping Tool

## 1. Overview

The **RecipeDB 3.0 Image Validation & Mapping Tool** (`review_server.py`) is an interactive web application used by human reviewers to inspect, fix, and replace broken, missing, or mismatched recipe images across the RecipeDB 3.0 dataset (`recipedb3_mastersheet_csv_v22`, comprising 584,639 recipes).

The tool loads flagged recipes in batches of 10 per page, displays the existing database image alongside candidate photos fetched from Wikipedia, Wikimedia Commons, and DuckDuckGo, and lets the reviewer choose the best replacement image, search with custom keywords, paste a direct image link, or apply a category placeholder.

---

## 2. Directory & File Structure

```
ingredient_image_review_server/
│
├── review_server.py               # Main Flask web application
├── README.md                      # User guide and documentation
├── requirements.txt               # Python package dependencies
├── run_review.sh                  # Optional bash launcher script
│
├── recipes_to_review.csv          # Default input CSV (or custom path passed via CLI)
├── reviewed_recipes_output.csv    # Output CSV (auto-created and updated as you review)
├── search_cache.json              # Local cache of searched candidate images
│
├── downloaded_recipe_images/      # Folder where approved recipe images are automatically saved
│   └── 1001_chicken_tikka_masala.jpg
│
└── placeholders/                  # Folder containing category placeholder images
    ├── soup_placeholder.jpg
    ├── dessert_placeholder.jpg
    ├── main_course_placeholder.jpg
    └── general_food_placeholder.jpg
```

---

## 3. Prerequisites & Setup

### 3.1 System Requirements
* **Python**: Python 3.8 or higher
* **Internet Connection**: Required to search Wikimedia Commons and DuckDuckGo and to download selected images

### 3.2 Install Required Packages
Run the following command once to install Flask and the DuckDuckGo search library:

```bash
pip install flask ddgs
```

*(Alternatively, if installing from `requirements.txt`:)*

```bash
pip install -r requirements.txt
```

### 3.3 Automatic Folder Setup
When you start `review_server.py`, it automatically creates the `downloaded_recipe_images/` and `placeholders/` directories if they do not already exist.

---

## 4. How to Run the Tool

### 4.1 Start the Server
Open a terminal in the project folder and run `review_server.py` with your assigned CSV file:

```bash
python3 review_server.py path/to/your_input_batch.csv
```

If you do not specify a file path, the tool looks for `recipes_to_review.csv`, then `sample_records.csv`, and finally `input.csv`.

### 4.2 Open the Web Interface
Once the server starts, you will see:

```text
==========================================================
RecipeDB 3.0 Image Validation & Mapping Server
Input CSV        : recipes_to_review.csv
Output CSV       : reviewed_recipes_output.csv
Images Directory : downloaded_recipe_images/
Placeholders Dir : placeholders/
URL              : http://127.0.0.1:5000
==========================================================
```

Open your browser and go to:

```text
http://127.0.0.1:5000
```

---

## 5. User-Facing Features & Interface Controls

The web interface is organized into a top navigation header, 10 recipe review rows per batch, and a sticky save bar at the bottom. Below are all the features available to the reviewer:

### 5.1 Progress Bar, Batch Pagination & Queue Filter (Top Bar)
* **Live Progress Counter**: Displays `Reviewed X of Y recipes (Z%)` along with a visual progress bar so you always know how much of your assigned file is complete.
* **Batch Pagination (`Previous Batch` / `Next Batch`)**: Step backward and forward through pages of 10 recipes at a time.
* **Queue View Toggle (`Show Unreviewed Only` vs. `Show All Recipes`)**:
  * **Pending Unreviewed Queue (Default)**: Shows only recipes that still need a review decision.
  * **Show All Recipes**: Displays every recipe in the input CSV (including ones you already reviewed, marked with a green `Reviewed` badge) so you can inspect or change a previous selection at any time.

### 5.2 Recipe Information Banner
At the top of each recipe row, the banner displays:
* **Recipe Name**: The full title of the dish and its batch row number.
* **Metadata Badges**:
  * `ID`: The unique `recipe_id` from RecipeDB 3.0.
  * `Cuisine`: The regional cuisine (e.g., `Indian`, `Italian`).
  * `Category`: The food category (e.g., `Main Course`, `Dessert`, `Bread, Quick Breads`).
  * `Flag Reason`: Why the recipe was flagged for review (e.g., `Flagged: na_image = 1` or `Wrong dish shown`).
  * `Query`: Shows the exact search query used to find the candidate images (and indicates if the automatic fallback query was used).
* **`View Original Recipe` Link**: If the input CSV includes the original source `url`, clicking this badge opens the original recipe article in a new tab so you can read the ingredients or directions for context.

### 5.3 Current Database Image Inspector & "Keep Current Image"
On the left side of each recipe row is the **Current DB Image** panel:
* **Visual Preview**: Displays the existing image currently linked in RecipeDB 3.0. If the URL is empty or broken, it displays `"No Image Recorded"` or `"Broken External Image Link"`.
* **`Open Current Image URL`**: Opens the existing database image in a new browser tab for full-size inspection.
* **`Keep Current Image` Button**: If you inspect the current database image and see that it is actually a clear, accurate photo of the dish (falsely flagged upstream), click **`Keep Current Image`** to approve and retain the existing database URL without downloading a replacement.

### 5.4 Candidate Image Selection Grid
On the right side of each recipe row is the candidate grid showing up to 10 replacement options from **Wikimedia Commons** and **DuckDuckGo**:
* **Click-to-Select Cards**: Clicking any card highlights it in blue and marks it as `Selected`.
* **Source & Domain Badges**: Green badges indicate `Wikimedia Commons` / `en.wikipedia.org`, and blue badges show the exact website domain for DuckDuckGo results (e.g., `allrecipes.com`, `seriouseats.com`, `foodnetwork.com`).
* **Image Resolution Display**: Shows the pixel dimensions (`width x height`) underneath the domain badge so you can pick high-resolution photos and avoid tiny thumbnails.
* **`Original` & `Source Page` Links**:
  * Clicking **`Original`** opens the full-resolution image in a new tab without changing your card selection.
  * Clicking **`Source Page`** opens the web page or article where the photo was published so you can verify that the photo actually belongs to that dish.

### 5.5 Custom Search Bar (In-Place Re-Search)
If the automatic search results do not show the right dish (for example, if a recipe has an unusual title like `"Yes, Virginia There is a Great Meatloaf"`):
* Type your own keywords (such as `"classic glazed meatloaf"`) into the **Custom search query** input box on that recipe's row.
* Click **`Search`** (or press `Enter`).
* **Instant Single-Row Refresh**: Only that recipe's candidate grid refreshes with new images; all your selections on the other 9 recipes on the page remain intact.

### 5.6 Manual Image URL Input
If you find a suitable image yourself on Google Images, Wikimedia, or another tab:
* Copy the direct image address (ending in `.jpg`, `.png`, `.webp`, etc.) and paste it into the **`Paste direct image URL`** field on that recipe row.
* Click **`Use Manual URL`** (or press `Enter`).
* A custom card previewing your pasted image immediately appears in the grid and is selected automatically.

### 5.7 Category Placeholder Fallback (`[ Use Category Placeholder ]`)
Some obscure, regional, or home-named recipes do not have any accurate photo available online. When no candidate image is suitable:
* Click the orange **`[ Use Category Placeholder ]`** button in the top-right corner of the recipe banner, **or** click **Card `0` (`Category Placeholder`)** in the candidate grid.
* The tool automatically maps the recipe's category to its matching placeholder image (e.g., `placeholders/soup_placeholder.jpg`, `placeholders/dessert_placeholder.jpg`, or `placeholders/general_food_placeholder.jpg`).

### 5.8 Skip / Decide Later Card
* Every unreviewed recipe starts with the **`Skip / Decide Later`** card selected by default.
* If you only review 6 out of the 10 recipes on a page and leave the other 4 on **`Skip / Decide Later`**, only the 6 reviewed recipes are saved when you submit. The 4 skipped recipes stay in your unreviewed queue so you never accidentally mark an uninspected recipe as finished.

### 5.9 Reviewer Notes Input
* Each recipe row includes a **`Reviewer notes (optional)`** text field.
* When you use a Category Placeholder, Manual URL, or Custom Search, the tool automatically fills in helpful notes (such as `"Category placeholder applied"`, `"Manual URL entered"`, or `"Manual search used"`), and you can type or edit any custom note before saving.

### 5.10 Keyboard Shortcuts (Hotkeys)
To review batches rapidly without excessive mouse clicking:
1. Click anywhere inside a recipe row to make it the **active row** (highlighted with a blue outline).
2. Press keys **`1` through `9`** on your keyboard to immediately select Candidate Card `1` through `9` in that row.
3. Press key **`0`** on your keyboard to select the **Category Placeholder** card for that row.

---

## 6. Step-by-Step Workflow: How to Review Recipes

Follow this workflow for each batch of 10 recipes:

1. **Check the Recipe Title & Current Image**:
   Read the recipe title, cuisine, and category in the banner, then look at the **Current DB Image** on the left to see if the existing image is missing, broken, or showing the wrong item.
2. **Pick the Best Replacement Option**:
   * **If a good photo appears in the Candidate Grid**: Click the best candidate card (or press `1`–`9`). Check the resolution (`W x H`) and click `Original` or `Source Page` if you want to verify quality before selecting.
   * **If the candidate images do not match the dish**: Use the **Custom search query** bar to search with simpler keywords, or paste a direct link into **Paste direct image URL** and click **`Use Manual URL`**.
   * **If no accurate photo exists anywhere online**: Click **`[ Use Category Placeholder ]`** (or press `0`).
   * **If the Current DB Image on the left is already correct**: Click **`Keep Current Image`**.
   * **If you want to skip the recipe for now**: Leave **`Skip / Decide Later`** selected.
3. **Repeat for the Remaining Recipes in the Batch**:
   Work down through the 10 recipes on the page.
4. **Save the Batch**:
   Click the green **`Save Reviewed Selections & Load Next Batch`** button in the sticky bar at the bottom of the screen.
   * The tool downloads your selected images into `downloaded_recipe_images/`, checks that each downloaded file is a valid image larger than $5\text{ KB}$, and saves your decisions to `reviewed_recipes_output.csv`.
5. **Resolve Any Download Alerts (If Shown)**:
   If a website blocks an image download or returns a corrupt file smaller than $5\text{ KB}$, a red alert box will appear at the top of the next page telling you which recipe failed. That recipe is automatically kept in your unreviewed queue so you can pick a different candidate card.
6. **Stop and Resume Anytime**:
   Your progress is saved to `reviewed_recipes_output.csv` every time you click **`Save Reviewed Selections & Load Next Batch`**. You can close the browser or stop the server (`Ctrl+C`) at any point; running the script again will automatically resume where you left off.

---

## 7. Input & Output CSV Data Contracts

### 7.1 Input CSV Columns
The tool accepts both pre-flagged review files and raw exports from RecipeDB 3.0 (`schema.csv` / `sample_records.csv`), automatically handling either `recipe_name` or `recipe name`:

| Column Name | Required / Optional | Description |
|---|---|---|
| `recipe_id` | **Required** | Unique recipe identifier |
| `recipe_name` (or `recipe name`) | **Required** | Name/title of the recipe |
| `cuisine` | **Required** | Cuisine classification (e.g., `Indian`, `Italian`, `Other`) |
| `category` | **Required** | Dish category (e.g., `Soup`, `Dessert`, `Main Course`) |
| `image_url` | Optional | Current image URL stored in the database |
| `image_id` | Optional | Current image asset ID |
| `na_image` | Optional | Flag (`1` = missing/invalid image; `0` or `-1` = valid) |
| `reason` | Optional | Note from the data team explaining why the image was flagged |
| `url` | Optional | Link to the original source recipe web page |

### 7.2 Output CSV Columns (`reviewed_recipes_output.csv`)
`reviewed_recipes_output.csv` contains all rows from the input file formatted with the 11 columns required for database ingestion:

```csv
recipe_id,recipe_name,cuisine,category,source,source_page_url,new_image_id,final_image_url,local_file_path,review_timestamp,reviewer_notes
```

| Column Name | Description |
|---|---|
| `recipe_id` | Unique recipe identifier |
| `recipe_name` | Recipe display name |
| `cuisine` | Recipe cuisine |
| `category` | Recipe category |
| `source` | `Wikimedia Commons`, `DuckDuckGo`, `Manual URL`, `Category Placeholder`, or `Existing DB Image` |
| `source_page_url` | Web page URL where the image was sourced (`N/A` for placeholders) |
| `new_image_id` | `rdb3_img_<recipe_id>` for downloaded images, `rdb3_placeholder_<category>` for placeholders, or existing `image_id` if kept |
| `final_image_url` | Direct URL of the selected image or `placeholders/<category>_placeholder.jpg` |
| `local_file_path` | Local path in `downloaded_recipe_images/<id>_<slug>.<ext>`, `placeholders/<category>_placeholder.jpg`, or `EXISTING_DB_IMAGE` |
| `review_timestamp` | UTC timestamp when the decision was saved (`YYYY-MM-DDTHH:MM:SSZ`) |
| `reviewer_notes` | Reviewer notes (auto-populated or manually entered) |

---

## 8. Deliverables When Finished

Once the progress bar at the top of the tool reaches **100%**, share the following two outputs with the data engineering team:
1. **`reviewed_recipes_output.csv`**
2. **`downloaded_recipe_images/`** (the folder containing all downloaded recipe photos)