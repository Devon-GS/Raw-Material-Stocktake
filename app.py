import csv
import io
import os
import sqlite3
from collections import defaultdict
from math import ceil
from decimal import Decimal, InvalidOperation

from flask import Flask, flash, g, redirect, render_template, request, url_for


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "app.db"))
DEFAULT_RECIPE_CSV = os.path.join(BASE_DIR, "Recipe Listing.csv")
DATABASE_PREEXISTED = os.path.exists(DATABASE)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL UNIQUE,
            product_name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            raw_material TEXT NOT NULL,
            quantity REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        """
    )
    db.commit()


def clear_all_recipes():
    db = get_db()
    db.execute("DELETE FROM recipe_items")
    db.execute("DELETE FROM recipes")
    db.commit()


def parse_quantity(value):
    if value is None:
        raise ValueError("Missing quantity")
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid quantity: {value}")
    return quantity


def normalize_label(value):
    return " ".join(str(value or "").strip().split())


def consolidate_recipe_items(items):
    consolidated = []
    lookup = {}
    for item in items:
        raw_material = normalize_label(item.get("raw_material"))
        if not raw_material:
            continue
        quantity = parse_quantity(item.get("quantity"))
        key = raw_material.casefold()
        if key in lookup:
            consolidated[lookup[key]]["quantity"] += quantity
        else:
            lookup[key] = len(consolidated)
            consolidated.append({"raw_material": raw_material, "quantity": quantity})
    return consolidated


def collect_recipe_items_from_form(form):
    materials = form.getlist("raw_material")
    quantities = form.getlist("quantity")
    items = []
    for material, quantity in zip(materials, quantities):
        material = normalize_label(material)
        quantity = normalize_label(quantity)
        if not material or not quantity:
            continue
        try:
            items.append({"raw_material": material, "quantity": parse_quantity(quantity)})
        except ValueError:
            continue
    return consolidate_recipe_items(items)


def parse_recipe_rows(rows):
    grouped = {}
    issues = []

    for row_number, row in enumerate(rows, start=1):
        if not row or all(not cell.strip() for cell in row):
            continue

        if len(row) >= 6 and row[0].strip().casefold() in {"f/g code", "stock code"}:
            continue

        if len(row) < 6:
            issues.append(f"Row {row_number}: skipped because it has fewer than 6 columns.")
            continue

        stock_code = normalize_label(row[0])
        product_name = normalize_label(row[1])
        raw_material = normalize_label(row[3])
        quantity_raw = normalize_label(row[5])

        if not stock_code or not product_name or not raw_material or not quantity_raw:
            issues.append(f"Row {row_number}: skipped because required values were missing.")
            continue

        try:
            quantity = parse_quantity(quantity_raw)
        except ValueError:
            issues.append(f"Row {row_number}: skipped because quantity '{quantity_raw}' is invalid.")
            continue

        recipe = grouped.setdefault(
            stock_code,
            {
                "stock_code": stock_code,
                "product_name": product_name,
                "items": [],
            },
        )

        if recipe["product_name"] != product_name:
            recipe["product_name"] = product_name

        recipe["items"].append({"raw_material": raw_material, "quantity": quantity})

    for recipe in grouped.values():
        recipe["items"] = consolidate_recipe_items(recipe["items"])

    return grouped, issues


def parse_recipe_csv(file_obj):
    text_stream = io.StringIO(file_obj.read().decode("utf-8-sig"))
    reader = csv.reader(text_stream)
    return parse_recipe_rows(reader)


def upsert_recipe(stock_code, product_name, items):
    db = get_db()
    recipe = db.execute(
        "SELECT id FROM recipes WHERE stock_code = ?",
        (stock_code,),
    ).fetchone()

    if recipe:
        recipe_id = recipe["id"]
        db.execute(
            "UPDATE recipes SET product_name = ? WHERE id = ?",
            (product_name, recipe_id),
        )
        db.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
    else:
        cursor = db.execute(
            "INSERT INTO recipes (stock_code, product_name) VALUES (?, ?)",
            (stock_code, product_name),
        )
        recipe_id = cursor.lastrowid

    for index, item in enumerate(items):
        db.execute(
            """
            INSERT INTO recipe_items (recipe_id, raw_material, quantity, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (recipe_id, item["raw_material"], float(item["quantity"]), index),
        )

    db.commit()


def import_recipes_from_csv(file_obj, replace=False):
    grouped, issues = parse_recipe_csv(file_obj)
    if replace:
        if not grouped:
            raise ValueError("No valid recipes were found in the CSV.")
        clear_all_recipes()
    for stock_code, recipe in grouped.items():
        upsert_recipe(stock_code, recipe["product_name"], recipe["items"])
    return len(grouped), issues


def get_all_recipes():
    db = get_db()
    return db.execute(
        """
        SELECT id, stock_code, product_name
        FROM recipes
        ORDER BY product_name COLLATE NOCASE
        """
    ).fetchall()


def get_recipe(recipe_id):
    db = get_db()
    recipe = db.execute(
        "SELECT id, stock_code, product_name FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if recipe is None:
        return None
    items = db.execute(
        """
        SELECT id, raw_material, quantity, sort_order
        FROM recipe_items
        WHERE recipe_id = ?
        ORDER BY sort_order, id
        """,
        (recipe_id,),
    ).fetchall()
    return recipe, items


def get_recipe_page(search="", page=1, per_page=20):
    db = get_db()
    search = normalize_label(search)
    params = []
    where_sql = ""
    if search:
        like = f"%{search.casefold()}%"
        where_sql = "WHERE lower(stock_code) LIKE ? OR lower(product_name) LIKE ?"
        params = [like, like]

    total = db.execute(
        f"SELECT COUNT(*) AS count FROM recipes {where_sql}",
        params,
    ).fetchone()["count"]

    total_pages = max(1, ceil(total / per_page)) if total else 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    recipes = db.execute(
        f"""
        SELECT id, stock_code, product_name
        FROM recipes
        {where_sql}
        ORDER BY stock_code COLLATE NOCASE
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    ).fetchall()

    return {
        "recipes": recipes,
        "search": search,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "page_numbers": list(range(max(1, page - 2), min(total_pages, page + 2) + 1)),
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "start": 0 if total == 0 else offset + 1,
        "end": 0 if total == 0 else min(offset + len(recipes), total),
    }


def format_number(value):
    value = Decimal(str(value))
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    text = format(normalized, "f").rstrip("0").rstrip(".")
    return text if text else "0"


@app.before_request
def ensure_database():
    init_db()
    db = get_db()
    recipe_count = db.execute("SELECT COUNT(*) AS count FROM recipes").fetchone()["count"]
    if not DATABASE_PREEXISTED and recipe_count == 0 and os.path.exists(DEFAULT_RECIPE_CSV):
        with open(DEFAULT_RECIPE_CSV, "rb") as file_obj:
            import_recipes_from_csv(file_obj, replace=False)


@app.context_processor
def inject_helpers():
    return {"format_number": format_number}


@app.route("/")
def index():
    products = get_all_recipes()
    return render_template("index.html", products=products)


@app.route("/process", methods=["POST"])
def process():
    products = get_all_recipes()
    requested = []
    totals = defaultdict(Decimal)
    selected_products = []

    db = get_db()
    for product in products:
        field_name = f"qty_{product['id']}"
        raw_value = request.form.get(field_name, "").strip()
        if not raw_value:
            continue
        try:
            qty = Decimal(raw_value)
        except InvalidOperation:
            qty = Decimal("0")
        if qty <= 0:
            continue
        requested.append({"product_name": product["product_name"], "quantity": qty})
        selected_products.append((product["id"], qty))

    if not selected_products:
        flash("Enter at least one quantity before processing.", "warning")
        return redirect(url_for("index"))

    for recipe_id, product_qty in selected_products:
        db = get_db()
        items = db.execute(
            """
            SELECT raw_material, quantity
            FROM recipe_items
            WHERE recipe_id = ?
            ORDER BY sort_order, id
            """,
            (recipe_id,),
        ).fetchall()
        for item in items:
            totals[item["raw_material"]] += Decimal(str(item["quantity"])) * product_qty

    ordered_totals = sorted(totals.items(), key=lambda entry: entry[0].lower())
    return render_template(
        "results.html",
        requested=requested,
        totals=ordered_totals,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        upload = request.files.get("recipe_file")

        if action in {"append", "replace"}:
            if upload is None or upload.filename == "":
                flash("Choose a CSV file to import.", "warning")
                return redirect(url_for("settings"))
            try:
                imported, issues = import_recipes_from_csv(upload.stream, replace=(action == "replace"))
            except Exception as exc:
                flash(f"Import failed: {exc}", "danger")
                return redirect(url_for("settings"))
            if issues:
                flash(f"Imported {imported} product(s) with {len(issues)} row warning(s).", "warning")
            else:
                flash(
                    (
                        f"Replaced all recipes and imported {imported} product(s)."
                        if action == "replace"
                        else f"Imported {imported} product(s)."
                    ),
                    "success",
                )
            return redirect(url_for("settings"))

    query = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)
    pagination = get_recipe_page(search=query, page=page, per_page=20)
    return render_template("settings.html", **pagination)


@app.route("/recipes/new", methods=["GET", "POST"])
def recipe_new():
    if request.method == "POST":
        stock_code = request.form.get("stock_code", "").strip()
        product_name = request.form.get("product_name", "").strip()
        items = collect_recipe_items_from_form(request.form)

        if not stock_code or not product_name:
            flash("Stock code and product name are required.", "warning")
            return redirect(url_for("recipe_new"))
        if not items:
            flash("Add at least one raw material line.", "warning")
            return redirect(url_for("recipe_new"))

        try:
            upsert_recipe(stock_code, product_name, items)
        except sqlite3.IntegrityError:
            flash("That stock code already exists. Edit the existing recipe instead.", "danger")
            return redirect(url_for("recipe_new"))
        flash("Recipe saved.", "success")
        return redirect(url_for("settings"))

    return render_template(
        "recipe_form.html",
        recipe=None,
        items=[{"raw_material": "", "quantity": ""} for _ in range(8)],
        page_title="Add Recipe",
    )


@app.route("/recipes/<int:recipe_id>/edit", methods=["GET", "POST"])
def recipe_edit(recipe_id):
    data = get_recipe(recipe_id)
    if data is None:
        flash("Recipe not found.", "danger")
        return redirect(url_for("settings"))

    recipe, items = data
    if request.method == "POST":
        stock_code = request.form.get("stock_code", "").strip()
        product_name = request.form.get("product_name", "").strip()
        new_items = collect_recipe_items_from_form(request.form)

        if not stock_code or not product_name:
            flash("Stock code and product name are required.", "warning")
            return redirect(url_for("recipe_edit", recipe_id=recipe_id))
        if not new_items:
            flash("Add at least one raw material line.", "warning")
            return redirect(url_for("recipe_edit", recipe_id=recipe_id))

        db = get_db()
        try:
            db.execute(
                "UPDATE recipes SET stock_code = ?, product_name = ? WHERE id = ?",
                (stock_code, product_name, recipe_id),
            )
            db.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
            for index, item in enumerate(new_items):
                db.execute(
                    """
                    INSERT INTO recipe_items (recipe_id, raw_material, quantity, sort_order)
                    VALUES (?, ?, ?, ?)
                    """,
                    (recipe_id, item["raw_material"], float(item["quantity"]), index),
                )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("That stock code already exists. Choose a different one.", "danger")
            return redirect(url_for("recipe_edit", recipe_id=recipe_id))
        flash("Recipe updated.", "success")
        return redirect(url_for("settings"))

    editable_items = list(items)
    editable_items.extend({"raw_material": "", "quantity": ""} for _ in range(8))
    return render_template(
        "recipe_form.html",
        recipe=recipe,
        items=editable_items,
        page_title="Edit Recipe",
    )


@app.route("/recipes/<int:recipe_id>/delete", methods=["POST"])
def recipe_delete(recipe_id):
    db = get_db()
    db.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    flash("Recipe deleted.", "success")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5005")), debug=os.environ.get("FLASK_DEBUG") == "1")
