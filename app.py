import csv
import io
import os
import sqlite3
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from flask import Flask, flash, g, redirect, render_template, request, url_for


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "app.db")
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


def parse_recipe_csv(file_obj):
    text_stream = io.StringIO(file_obj.read().decode("utf-8-sig"))
    reader = csv.DictReader(text_stream)
    grouped = defaultdict(list)

    for row in reader:
        stock_code = (row.get("F/G Code") or "").strip()
        product_name = (row.get("F/G Description") or "").strip()
        raw_material = (row.get("R/M Description") or "").strip()
        quantity_raw = row.get("Quantity")

        if not stock_code or not product_name or not raw_material:
            continue

        try:
            quantity = parse_quantity(quantity_raw)
        except ValueError:
            continue

        grouped[(stock_code, product_name)].append(
            {
                "raw_material": raw_material,
                "quantity": quantity,
            }
        )

    return grouped


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
    grouped = parse_recipe_csv(file_obj)
    if replace:
        clear_all_recipes()
    for (stock_code, product_name), items in grouped.items():
        upsert_recipe(stock_code, product_name, items)
    return len(grouped)


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
                imported = import_recipes_from_csv(upload.stream, replace=(action == "replace"))
            except Exception as exc:
                flash(f"Import failed: {exc}", "danger")
                return redirect(url_for("settings"))
            message = (
                f"Replaced all recipes and imported {imported} product(s)."
                if action == "replace"
                else f"Imported {imported} product(s)."
            )
            flash(message, "success")
            return redirect(url_for("settings"))

    query = (request.args.get("q") or "").strip()
    db = get_db()
    if query:
        search = f"%{query}%"
        recipes = db.execute(
            """
            SELECT DISTINCT r.id, r.stock_code, r.product_name
            FROM recipes r
            LEFT JOIN recipe_items ri ON ri.recipe_id = r.id
            WHERE r.stock_code LIKE ?
               OR r.product_name LIKE ?
               OR ri.raw_material LIKE ?
            ORDER BY r.product_name COLLATE NOCASE
            """,
            (search, search, search),
        ).fetchall()
    else:
        recipes = get_all_recipes()

    return render_template("settings.html", recipes=recipes, query=query)


@app.route("/recipes/new", methods=["GET", "POST"])
def recipe_new():
    if request.method == "POST":
        stock_code = request.form.get("stock_code", "").strip()
        product_name = request.form.get("product_name", "").strip()
        materials = request.form.getlist("raw_material")
        quantities = request.form.getlist("quantity")
        items = []
        for material, quantity in zip(materials, quantities):
            material = material.strip()
            quantity = quantity.strip()
            if not material or not quantity:
                continue
            try:
                items.append({"raw_material": material, "quantity": parse_quantity(quantity)})
            except ValueError:
                continue

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
        materials = request.form.getlist("raw_material")
        quantities = request.form.getlist("quantity")
        new_items = []
        for material, quantity in zip(materials, quantities):
            material = material.strip()
            quantity = quantity.strip()
            if not material or not quantity:
                continue
            try:
                new_items.append({"raw_material": material, "quantity": parse_quantity(quantity)})
            except ValueError:
                continue

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
    app.run(debug=True)
