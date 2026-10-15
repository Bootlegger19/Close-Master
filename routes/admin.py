from datetime import date, datetime, time, timedelta
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import func

from constants import (BUSINESS_DAY_STARTS_HOUR, CODE_MAX_LENGTH, CODE_MIN_LENGTH,
                       business_date)
from models import (db, Bar, CashCount, Category, ClosingTask, Employee,
                    InventorySession, Item, Role)
from printout import build_report, pdf_response
from security import (code_in_use, generate_unique_code, hash_code,
                      validate_code_format)

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")

# --- Security settings: change them here, nowhere else ---
PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8
LOCKOUT_THRESHOLD = 5   # failed attempts before locking
LOCKOUT_MINUTES = 5

# --- Limits for typed text (match the column sizes in models.py) ---
CATEGORY_NAME_MAX = 50
ITEM_NAME_MAX = 100
TASK_MAX_LENGTH = 200
EMPLOYEE_NAME_MAX = 100
HISTORY_LIMIT = 50   # most recent closings shown on the History tab

# --- Dashboard tabs: (key, label, endpoint). Adding a tab = adding one line. ---
ADMIN_TABS = [
    ("overview", "Overview", "admin.dashboard"),
    ("stock", "Stock", "admin.stock"),
    ("tasks", "Closing tasks", "admin.tasks"),
    ("employees", "Employees", "admin.employees"),
    ("history", "History", "admin.history"),
    ("printouts", "Printouts", "admin.printouts"),
    ("security", "Security", "admin.change_pin"),
]


@admin_bp.context_processor
def inject_admin_layout():
    """Make the tab list and bar name available to every admin template."""
    return {"admin_tabs": ADMIN_TABS, "admin_bar": Bar.query.first()}


def get_admin():
    return Employee.query.join(Employee.role).filter_by(name="admin").first()


def validate_new_pin(pin):
    """Return an error message if the PIN is unacceptable, else None."""
    if not pin.isdigit():
        return "PIN must contain digits only."
    if not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH:
        return f"PIN must be {PIN_MIN_LENGTH} to {PIN_MAX_LENGTH} digits long."
    if len(set(pin)) == 1:
        return "PIN can't be a single repeated digit."
    if pin in "0123456789" or pin in "9876543210":
        return "PIN can't be a simple sequence like 1234."
    return None


def register_failed_attempt(admin):
    """Count a wrong PIN. Returns True if this attempt triggered a lockout."""
    admin.failed_attempts += 1
    locked = admin.failed_attempts >= LOCKOUT_THRESHOLD
    if locked:
        admin.failed_attempts = 0
        admin.locked_until = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
        session.pop("is_admin", None)
    db.session.commit()
    return locked


def clean_text(raw, max_len, label):
    """Return (text, error) for a name or description typed by the admin."""
    text = (raw or "").strip()
    if not text:
        return None, f"{label} can't be empty."
    if len(text) > max_len:
        return None, f"{label} can be up to {max_len} characters."
    return text, None


def active_categories(bar_id):
    return Category.query.filter_by(bar_id=bar_id).filter(Category.archived_at.is_(None))


def active_tasks(bar_id):
    return ClosingTask.query.filter_by(bar_id=bar_id).filter(ClosingTask.archived_at.is_(None))


def pin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.login"))
        admin = get_admin()
        if admin and admin.must_change_pin and request.endpoint != "admin.change_pin":
            flash("Please set a new PIN before continuing.")
            return redirect(url_for("admin.change_pin"))
        return view_func(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- login / PIN

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        # Already signed in (e.g. reached this page with Back): go to the home page.
        if session.get("is_admin"):
            return redirect(url_for("landing"))
        return render_template("login.html")

    # POST: reply with JSON; static/pin-login.js decides what to show or where to go.
    admin = get_admin()
    now = datetime.now()

    if admin and admin.locked_until and admin.locked_until > now:
        minutes_left = int((admin.locked_until - now).total_seconds() // 60) + 1
        return jsonify(success=False,
                       error=f"Too many failed attempts. Try again in {minutes_left} minute(s)."), 429

    if admin and admin.check_pin(request.form.get("pin", "")):
        admin.failed_attempts = 0
        admin.locked_until = None
        db.session.commit()
        session["is_admin"] = True
        return jsonify(success=True, redirect=url_for("admin.dashboard"))

    if admin:
        if register_failed_attempt(admin):
            error = f"Too many failed attempts. Locked for {LOCKOUT_MINUTES} minutes."
        else:
            left = LOCKOUT_THRESHOLD - admin.failed_attempts
            error = f"Incorrect PIN. {left} attempt(s) left."
    else:
        error = "Incorrect PIN."
    return jsonify(success=False, error=error), 401


@admin_bp.route("/logout")
def logout():
    session.pop("is_admin", None)
    return redirect(url_for("landing"))


@admin_bp.route("/change-pin", methods=["GET", "POST"])
@pin_required
def change_pin():
    admin = get_admin()
    if request.method == "POST":
        current_pin = request.form.get("current_pin", "")
        new_pin = request.form.get("new_pin", "")
        confirm_pin = request.form.get("confirm_pin", "")

        if not admin.check_pin(current_pin):
            if register_failed_attempt(admin):
                flash(f"Too many failed attempts. Locked for {LOCKOUT_MINUTES} minutes.")
                return redirect(url_for("admin.login"))
            flash("Current PIN is incorrect.")
        elif new_pin != confirm_pin:
            flash("New PIN and confirmation don't match.")
        elif new_pin == current_pin:
            flash("New PIN must be different from the current one.")
        else:
            error = validate_new_pin(new_pin)
            if error:
                flash(error)
            else:
                admin.set_pin(new_pin)
                admin.must_change_pin = False
                db.session.commit()
                flash("PIN updated.")
                return redirect(url_for("admin.dashboard"))
        return redirect(url_for("admin.change_pin"))

    return render_template("change_pin.html", active_tab="security",
                           forced=admin.must_change_pin,
                           min_len=PIN_MIN_LENGTH, max_len=PIN_MAX_LENGTH)


# ------------------------------------------------------------------ tab pages

@admin_bp.route("/")
@pin_required
def dashboard():
    bar = Bar.query.first()
    completed = (InventorySession.query
                 .filter_by(bar_id=bar.id, status="completed")
                 .order_by(InventorySession.completed_at.desc()))
    active_item_count = (Item.query.join(Category)
                         .filter(Category.bar_id == bar.id,
                                 Category.archived_at.is_(None),
                                 Item.archived_at.is_(None))
                         .count())
    return render_template(
        "dashboard.html",
        active_tab="overview",
        category_count=active_categories(bar.id).count(),
        item_count=active_item_count,
        task_count=active_tasks(bar.id).count(),
        closing_count=completed.count(),
        last_closing=completed.first(),
    )


@admin_bp.route("/stock")
@pin_required
def stock():
    bar = Bar.query.first()
    archived_categories = (Category.query
                           .filter(Category.bar_id == bar.id, Category.archived_at.is_not(None))
                           .order_by(Category.archived_at.desc()).all())
    archived_items = (Item.query.join(Category)
                      .filter(Category.bar_id == bar.id, Item.archived_at.is_not(None))
                      .order_by(Item.archived_at.desc()).all())
    return render_template("stock.html", active_tab="stock",
                           categories=active_categories(bar.id).order_by(Category.id).all(),
                           archived_categories=archived_categories,
                           archived_items=archived_items,
                           category_max=CATEGORY_NAME_MAX, item_max=ITEM_NAME_MAX)


@admin_bp.route("/tasks")
@pin_required
def tasks():
    bar = Bar.query.first()
    archived = (ClosingTask.query
                .filter(ClosingTask.bar_id == bar.id, ClosingTask.archived_at.is_not(None))
                .order_by(ClosingTask.archived_at.desc()).all())
    return render_template("tasks.html", active_tab="tasks", max_len=TASK_MAX_LENGTH,
                           tasks=active_tasks(bar.id).order_by(ClosingTask.id).all(),
                           archived_tasks=archived)


@admin_bp.route("/history")
@pin_required
def history():
    bar = Bar.query.first()
    closings = (InventorySession.query
                .filter_by(bar_id=bar.id, status="completed")
                .order_by(InventorySession.completed_at.desc())
                .limit(HISTORY_LIMIT).all())
    ids = [closing.id for closing in closings]
    cash_by_session = {cash.session_id: cash
                       for cash in CashCount.query.filter(CashCount.session_id.in_(ids))}
    return render_template("history.html", active_tab="history",
                           closings=closings, cash_by_session=cash_by_session,
                           limit=HISTORY_LIMIT)


@admin_bp.route("/history/<int:session_id>")
@pin_required
def history_detail(session_id):
    closing = InventorySession.query.filter_by(
        id=session_id, status="completed").first_or_404()
    # Same data the PDF uses, so the page and the printout can never disagree.
    return render_template("history_detail.html", active_tab="history",
                           closing=closing, report=build_report(closing))


@admin_bp.route("/printouts")
@pin_required
def printouts():
    bar = Bar.query.first()
    default_day = business_date(datetime.now())
    try:
        day = date.fromisoformat(request.args.get("date", default_day.isoformat()))
    except ValueError:
        flash("That date isn't valid. Showing today instead.")
        day = default_day

    # One business day: from the cutoff hour until the same hour the next morning.
    start = datetime.combine(day, time(BUSINESS_DAY_STARTS_HOUR))
    closings = (InventorySession.query
                .filter_by(bar_id=bar.id, status="completed")
                .filter(InventorySession.completed_at >= start,
                        InventorySession.completed_at < start + timedelta(days=1))
                .order_by(InventorySession.completed_at.desc())
                .all())
    return render_template("printouts.html", active_tab="printouts", day=day,
                           closings=closings,
                           prev_day=day - timedelta(days=1),
                           next_day=day + timedelta(days=1),
                           day_start_hour=BUSINESS_DAY_STARTS_HOUR)


@admin_bp.route("/history/<int:session_id>/pdf")
@pin_required
def closing_pdf(session_id):
    closing = InventorySession.query.filter_by(
        id=session_id, status="completed").first_or_404()
    return pdf_response(closing, as_attachment=request.args.get("download") == "1")


# ------------------------------------------------------- stock: categories / items

@admin_bp.route("/add-category", methods=["POST"])
@pin_required
def add_category():
    bar = Bar.query.first()
    name, error = clean_text(request.form.get("name"), CATEGORY_NAME_MAX, "Category name")
    if error:
        flash(error)
    elif Category.query.filter(Category.bar_id == bar.id,
                               func.lower(Category.name) == name.lower()).first():
        flash(f"A category called '{name}' already exists. "
              "If it was archived, restore it from the Archived list.")
    else:
        db.session.add(Category(name=name, bar_id=bar.id))
        db.session.commit()
    return redirect(url_for("admin.stock"))


@admin_bp.route("/add-item", methods=["POST"])
@pin_required
def add_item():
    category = (Category.query
                .filter(Category.id == request.form.get("category_id", type=int),
                        Category.archived_at.is_(None)).first())
    if category is None:
        flash("That category doesn't exist, or it has been archived.")
        return redirect(url_for("admin.stock"))

    name, error = clean_text(request.form.get("name"), ITEM_NAME_MAX, "Item name")
    if error:
        flash(error)
        return redirect(url_for("admin.stock"))

    same_name = Item.query.filter(Item.category_id == category.id,
                                  func.lower(Item.name) == name.lower())
    if same_name.filter(Item.archived_at.is_(None)).first():
        flash(f"'{name}' is already in {category.name}.")
    elif same_name.filter(Item.archived_at.is_not(None)).first():
        flash(f"'{name}' was archived earlier. Restore it from the Archived list "
              "so its history carries on.")
    else:
        db.session.add(Item(name=name, category_id=category.id))
        db.session.commit()
    return redirect(url_for("admin.stock"))


@admin_bp.route("/archive-item/<int:item_id>", methods=["POST"])
@pin_required
def archive_item(item_id):
    item = Item.query.get_or_404(item_id)
    item.archived_at = datetime.now()
    db.session.commit()
    flash(f"'{item.name}' archived. Past closings still show it.")
    return redirect(url_for("admin.stock"))


@admin_bp.route("/restore-item/<int:item_id>", methods=["POST"])
@pin_required
def restore_item(item_id):
    item = Item.query.get_or_404(item_id)
    if item.category.archived_at is not None:
        flash(f"Restore the category '{item.category.name}' first.")
    else:
        item.archived_at = None
        db.session.commit()
        flash(f"'{item.name}' restored.")
    return redirect(url_for("admin.stock"))


@admin_bp.route("/archive-category/<int:category_id>", methods=["POST"])
@pin_required
def archive_category(category_id):
    category = Category.query.get_or_404(category_id)
    if category.active_items:
        flash(f"Can't archive '{category.name}': it still has active items. "
              "Archive them first.")
    else:
        category.archived_at = datetime.now()
        db.session.commit()
        flash(f"'{category.name}' archived.")
    return redirect(url_for("admin.stock"))


@admin_bp.route("/restore-category/<int:category_id>", methods=["POST"])
@pin_required
def restore_category(category_id):
    category = Category.query.get_or_404(category_id)
    category.archived_at = None
    db.session.commit()
    flash(f"'{category.name}' restored.")
    return redirect(url_for("admin.stock"))


# ---------------------------------------------------------------- closing tasks

@admin_bp.route("/add-task", methods=["POST"])
@pin_required
def add_task():
    text, error = clean_text(request.form.get("description"), TASK_MAX_LENGTH,
                             "Task description")
    if error:
        flash(error)
    else:
        db.session.add(ClosingTask(bar_id=Bar.query.first().id, description=text))
        db.session.commit()
    return redirect(url_for("admin.tasks"))


@admin_bp.route("/edit-task/<int:task_id>", methods=["POST"])
@pin_required
def edit_task(task_id):
    task = ClosingTask.query.get_or_404(task_id)
    text, error = clean_text(request.form.get("description"), TASK_MAX_LENGTH,
                             "Task description")
    if error:
        flash(error)
    else:
        task.description = text
        db.session.commit()
        flash("Task updated.")
    return redirect(url_for("admin.tasks"))


@admin_bp.route("/archive-task/<int:task_id>", methods=["POST"])
@pin_required
def archive_task(task_id):
    task = ClosingTask.query.get_or_404(task_id)
    task.archived_at = datetime.now()
    db.session.commit()
    flash("Task archived. Past closings still show it.")
    return redirect(url_for("admin.tasks"))


@admin_bp.route("/restore-task/<int:task_id>", methods=["POST"])
@pin_required
def restore_task(task_id):
    task = ClosingTask.query.get_or_404(task_id)
    task.archived_at = None
    db.session.commit()
    flash("Task restored.")
    return redirect(url_for("admin.tasks"))


# ------------------------------------------------------------------ employees

def _employee_query():
    """All non-admin employees, active or archived."""
    return Employee.query.join(Employee.role).filter_by(name="employee")


def active_employees():
    return _employee_query().filter(Employee.archived_at.is_(None))


def get_employee_or_404(employee_id):
    # Limited to the 'employee' role, so these routes can never touch the admin account.
    employee = _employee_query().filter(Employee.id == employee_id).first()
    if employee is None:
        abort(404)
    return employee


def clean_employee_name(raw, ignore_id=None):
    """Return (name, error). Exactly one of them is None."""
    name, error = clean_text(raw, EMPLOYEE_NAME_MAX, "Name")
    if error:
        return None, error
    clash = active_employees().filter(func.lower(Employee.name) == name.lower())
    if ignore_id is not None:
        clash = clash.filter(Employee.id != ignore_id)
    if clash.first():
        return None, f"There's already an active employee named {name}."
    return name, None


def apply_code(employee, raw):
    """Give an employee a code: the typed one, or a generated one if blank.
    Returns (code, error). Nothing is saved until the caller commits."""
    raw = (raw or "").strip()
    if raw:
        error = validate_code_format(raw)
        if error:
            return None, error
        if code_in_use(hash_code(raw), ignore_id=employee.id):
            return None, "That code is already used by someone else."
        code = raw
    else:
        code = generate_unique_code()
    employee.code_hash = hash_code(code)
    return code, None


@admin_bp.route("/employees")
@pin_required
def employees():
    archived = (_employee_query().filter(Employee.archived_at.is_not(None))
                .order_by(Employee.archived_at.desc()).all())
    return render_template("employees.html", active_tab="employees",
                           employees=active_employees().order_by(Employee.name).all(),
                           archived=archived, name_max=EMPLOYEE_NAME_MAX,
                           code_max=CODE_MAX_LENGTH, code_min=CODE_MIN_LENGTH)


@admin_bp.route("/employees/add", methods=["POST"])
@pin_required
def add_employee():
    name, error = clean_employee_name(request.form.get("name"))
    if error:
        flash(error)
        return redirect(url_for("admin.employees"))

    employee = Employee(name=name, bar_id=Bar.query.first().id,
                        role_id=Role.query.filter_by(name="employee").first().id)
    code, error = apply_code(employee, request.form.get("code"))
    if error:
        flash(error)
        return redirect(url_for("admin.employees"))

    db.session.add(employee)
    db.session.commit()
    flash(f"Added {name}. Their closing code is {code}. "
          "Write it down now: it can't be shown again.")
    return redirect(url_for("admin.employees"))


@admin_bp.route("/employees/<int:employee_id>/rename", methods=["POST"])
@pin_required
def rename_employee(employee_id):
    employee = get_employee_or_404(employee_id)
    name, error = clean_employee_name(request.form.get("name"), ignore_id=employee.id)
    if error:
        flash(error)
    else:
        employee.name = name
        db.session.commit()
        flash("Name updated.")
    return redirect(url_for("admin.employees"))


@admin_bp.route("/employees/<int:employee_id>/reset-code", methods=["POST"])
@pin_required
def reset_employee_code(employee_id):
    employee = get_employee_or_404(employee_id)
    if employee.archived_at is not None:
        flash("Restore this employee before changing their code.")
        return redirect(url_for("admin.employees"))
    code, error = apply_code(employee, request.form.get("code"))
    if error:
        flash(error)
    else:
        db.session.commit()
        flash(f"New code for {employee.name}: {code}. "
              "Write it down now: it can't be shown again.")
    return redirect(url_for("admin.employees"))


@admin_bp.route("/employees/<int:employee_id>/archive", methods=["POST"])
@pin_required
def archive_employee(employee_id):
    employee = get_employee_or_404(employee_id)
    employee.archived_at = datetime.now()
    employee.code_hash = None   # revokes the code immediately and frees it for reuse
    db.session.commit()
    flash(f"{employee.name} archived. Their closing history is kept.")
    return redirect(url_for("admin.employees"))


@admin_bp.route("/employees/<int:employee_id>/restore", methods=["POST"])
@pin_required
def restore_employee(employee_id):
    employee = get_employee_or_404(employee_id)
    employee.archived_at = None
    code, _ = apply_code(employee, None)   # blank = generate a fresh code
    db.session.commit()
    flash(f"{employee.name} restored. New code: {code}. "
          "Write it down now: it can't be shown again.")
    return redirect(url_for("admin.employees"))