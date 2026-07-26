# Raw Material Stocktake

Flask app with SQLite storage and Bootstrap UI for recipe import, editing, and raw material calculations.

## What it does

- Loads recipes from `Recipe Listing.csv` on first run.
- Shows all products on the main page with quantity inputs.
- Processes selected products and multiplies raw material quantities by the entered amounts.
- Lets you import new recipes, replace all recipes, search recipes, edit recipes, and delete recipes from the settings page.

## CSV columns used

- `F/G Code` -> stock code
- `F/G Description` -> product name
- `R/M Description` -> raw material
- `Quantity` -> quantity of raw material used for one product

## Run locally

1. Create or activate a Python environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
python app.py
```

4. Open the local Flask URL shown in the terminal.

## Notes

- Use the Settings page to import a CSV with the same column structure.
- "Add New Recipe(s)" appends or updates recipes by stock code.
- "Upload All Recipes Again" clears the database first, then reloads the CSV.
