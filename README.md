Here is a comprehensive `README.md` file tailored specifically to the functionality found in your `app.py` code. 

***

# Recipe & Raw Material Calculator

A lightweight, Flask-based web application designed to manage product recipes and calculate the total raw materials required for production batches. 

This tool is ideal for manufacturing, bakeries, or any production environment where you need to know exactly how much of each raw material to pull from inventory to fulfill an order of finished goods.

## Features

* **Dynamic Material Calculation:** Input quantities for various finished products, and the app will calculate and consolidate the total amount of every raw material needed.
* **Recipe Management (CRUD):** Create, read, update, and delete product recipes directly from the web interface.
* **Bulk CSV Import:** Easily import or update your entire recipe database using a CSV file.
* **Smart Consolidation:** Automatically merges duplicate raw material entries within a recipe and during total calculation.
* **Pagination & Search:** Easily navigate and search through your recipe database.
* **Zero-Setup Database:** Uses SQLite. The database initializes automatically on the first run.

## Prerequisites

* Python 3.7 or higher
* [Flask](https://flask.palletsprojects.com/)

## Installation

1. **Clone or download the repository.**
2. **Navigate to the project directory:**
   ```bash
   cd path/to/your/project
   ```
3. **Create a virtual environment (recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```
4. **Install the required dependencies:**
   ```bash
   pip install Flask
   ```
## Usage

Start the application by running:
```bash
python app.py
```
The application will run by default on `http://0.0.0.0:5005/`.

### App Workflow
1. **Home Page (`/`):** Displays a list of all your finished products. Enter the quantity you want to produce for any number of products and click "Process".
2. **Results Page (`/process`):** Displays a consolidated list of every raw material and the exact quantities needed to fulfill the batch.
3. **Settings & Recipes (`/settings`):** Manage your database. Add new recipes manually, edit existing ones, or use the CSV uploader to append or replace your database.

## CSV Import Format

If you use the CSV import feature (via the Settings page or the auto-load feature), your CSV must have at least 6 columns. The application maps the columns as follows:

| Column A (Index 0) | Column B (Index 1) | Column C (Index 2) | Column D (Index 3) | Column E (Index 4) | Column F (Index 5) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Stock Code** | **Product Name** | *Ignored* | **Raw Material** | *Ignored* | **Quantity** |

* **Header Row:** The app automatically skips the row if Column A contains the text `f/g code` or `stock code` (case-insensitive).
* **Missing Data:** Rows missing required values (Stock code, Product Name, Material, or Quantity) will be safely skipped, and you will receive a warning message letting you know which rows were skipped.

## Environment Variables

You can customize the app's behavior by setting the following environment variables:

* `DATABASE_PATH`: Custom path for the SQLite database. (Default: `app.db` in the app directory)
* `SECRET_KEY`: Flask secret key used for session management and flashing messages. (Default: `dev-secret-key`)
* `PORT`: The port the application runs on. (Default: `5005`)
* `FLASK_DEBUG`: Set to `1` to run the application in debug mode.

## Project Structure

* `app.py`: The main Flask application logic and database routing.
* `app.db`: The SQLite database (auto-generated).
* `templates/`: *(Make sure you have this folder containing your HTML files)*
  * `index.html`: Main calculator form.
  * `results.html`: Consolidated raw materials view.
  * `settings.html`: Recipe list and CSV upload form.
  * `recipe_form.html`: Add/Edit individual recipe form.