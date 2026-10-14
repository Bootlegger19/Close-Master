from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from constants import DENOMINATIONS
from models import (db, CashCount, CashCountLine, Category, ClosingNote,
                    ClosingTask, Count, Employee, InventorySession, Item,
                    TaskCompletion)

employee_bp = Blueprint("employee", __name__, template_folder="../templates/employee")

NOTE_MAX_LENGTH = 500


def get_current_session():
    """Return this browser's in-progress closing, or None.

    A completed closing never counts as current, which is what stops the
    browser's Back button from reopening a submitted closing.
    """
    session_id = session.get("inventory_session_id")
    if not session_id:
        return None
    current = db.session.get(InventorySession, session_id)
    if current and current.status == "in_progress":
        return current
    session.pop("inventory_session_id", None)
    return None


def closing_required(view_func):
    """Guard for pages that only make sense during an active closing."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        current = get_current_session()
        if not current:
            flash("No closing in progress. Start a new one to continue.")
            return redirect(url_for("employee.home"))
        g.current = current
        return view_func(*args, **kwargs)
    return wrapper


def get_bar_items(bar_id):
    """Every item in a bar's categories: exactly what an inventory check must cover."""
    return Item.query.join(Category).filter(Category.bar_id == bar_id).all()


@employee_bp.route("/")
def home():
    categories = Category.query.all()
    return render_template("items.html", categories=categories,
                           in_progress=get_current_session())


@employee_bp.route("/start-closing", methods=["GET", "POST"])
def start_closing():
    if get_current_session():
        return redirect(url_for("employee.closing_hub"))

    if request.method == "POST":
        employee_id = request.form.get("employee_id", type=int)
        employee = db.session.get(Employee, employee_id) if employee_id else None
        if not employee:
            flash("Please select your name before starting.")
            return redirect(url_for("employee.start_closing"))
        new_session = InventorySession(bar_id=employee.bar_id, employee_id=employee.id)
        db.session.add(new_session)
        db.session.commit()
        session["inventory_session_id"] = new_session.id
        return redirect(url_for("employee.closing_hub"))

    return render_template("start_closing.html", employees=Employee.query.all())


@employee_bp.route("/closing")
@closing_required
def closing_hub():
    current = g.current
    item_ids = {item.id for item in get_bar_items(current.bar_id)}
    counted_ids = {count.item_id for count in current.counts}
    return render_template(
        "closing_hub.html",
        current=current,
        items_counted=len(counted_ids & item_ids),
        items_total=len(item_ids),
        cash=CashCount.query.filter_by(session_id=current.id).first(),
        total_tasks=ClosingTask.query.filter_by(bar_id=current.bar_id).count(),
        done_tasks=TaskCompletion.query.filter_by(session_id=current.id).count(),
        note_count=len(current.notes),
    )


@employee_bp.route("/inventory-check")
@closing_required
def inventory_check():
    current = g.current
    categories = Category.query.filter_by(bar_id=current.bar_id).all()

    previous = (
        InventorySession.query
        .filter_by(bar_id=current.bar_id, status="completed")
        .filter(InventorySession.id != current.id)
        .order_by(InventorySession.completed_at.desc())
        .first()
    )
    last_counts = {c.item_id: c.quantity for c in previous.counts} if previous else {}
    current_counts = {c.item_id: c.quantity for c in current.counts}

    return render_template(
        "inventory_check.html",
        categories=categories,
        last_counts=last_counts,
        # Boxes start at this closing's saved number, else last closing's number.
        prefill={**last_counts, **current_counts},
        saved_ids=set(current_counts),
    )


@employee_bp.route("/save-counts", methods=["POST"])
def save_counts():
    """Save every count on the page in one request (JSON in, JSON out)."""
    current = get_current_session()
    if not current:
        return jsonify(success=False,
                       error="This closing was already submitted or never started."), 400

    entries = (request.get_json(silent=True) or {}).get("counts")
    if not isinstance(entries, list):
        return jsonify(success=False, error="No counts were received."), 400

    item_ids = {item.id for item in get_bar_items(current.bar_id)}

    # Validate everything first, so one bad box never leaves a half-saved page.
    cleaned = {}
    for entry in entries:
        entry = entry if isinstance(entry, dict) else {}
        item_id, quantity = entry.get("item_id"), entry.get("quantity")
        if not isinstance(item_id, int) or item_id not in item_ids:
            return jsonify(success=False,
                           error="An item no longer exists. Refresh the page and try again."), 400
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            return jsonify(success=False,
                           error="Counts must be whole numbers, zero or higher."), 400
        cleaned[item_id] = quantity

    missing = item_ids - set(cleaned)
    if missing:
        return jsonify(success=False,
                       error=f"{len(missing)} item(s) still need a count. "
                             "Refresh the page and fill in every box."), 400

    existing = {c.item_id: c for c in current.counts}
    for item_id, quantity in cleaned.items():
        if item_id in existing:
            existing[item_id].quantity = quantity
        else:
            db.session.add(Count(session_id=current.id, item_id=item_id, quantity=quantity))
    db.session.commit()
    return jsonify(success=True)


@employee_bp.route("/cash-count", methods=["GET", "POST"])
@closing_required
def cash_count():
    current = g.current
    existing = CashCount.query.filter_by(session_id=current.id).first()

    if request.method == "POST":
        quantities = {}
        for _, group in DENOMINATIONS:
            for label, cents in group:
                raw = request.form.get(f"d_{cents}", "").strip() or "0"
                try:
                    quantity = int(raw)
                except ValueError:
                    flash(f"{label}: enter a whole number.")
                    return redirect(url_for("employee.cash_count"))
                if quantity < 0:
                    flash(f"{label}: can't be negative.")
                    return redirect(url_for("employee.cash_count"))
                quantities[cents] = quantity

        total = Decimal(sum(cents * qty for cents, qty in quantities.items())) / 100

        tip_raw = request.form.get("tip_pool", "").strip()
        try:
            tip = Decimal(tip_raw) if tip_raw else None
        except InvalidOperation:
            flash("Tip pool must be a number, like 42.50.")
            return redirect(url_for("employee.cash_count"))

        lines = [CashCountLine(denomination_cents=cents, quantity=qty)
                 for cents, qty in quantities.items() if qty > 0]
        if existing:
            existing.total = total
            existing.tip_pool_total = tip
            existing.lines = lines  # old lines are deleted automatically (delete-orphan)
        else:
            db.session.add(CashCount(session_id=current.id, total=total,
                                     tip_pool_total=tip, lines=lines))
        db.session.commit()
        return redirect(url_for("employee.closing_hub"))

    saved = {line.denomination_cents: line.quantity for line in existing.lines} if existing else {}
    return render_template("cash_count.html", denominations=DENOMINATIONS,
                           existing=existing, saved=saved)


@employee_bp.route("/closing-tasks", methods=["GET", "POST"])
@closing_required
def closing_tasks():
    current = g.current
    tasks = ClosingTask.query.filter_by(bar_id=current.bar_id).all()

    if request.method == "POST":
        checked_ids = {int(task_id) for task_id in request.form.getlist("task_ids")}
        TaskCompletion.query.filter_by(session_id=current.id).delete()
        for task in tasks:
            if task.id in checked_ids:
                db.session.add(TaskCompletion(session_id=current.id, task_id=task.id))
        db.session.commit()
        return redirect(url_for("employee.closing_hub"))

    done_ids = {tc.task_id for tc in TaskCompletion.query.filter_by(session_id=current.id)}
    return render_template("closing_tasks.html", tasks=tasks, done_ids=done_ids)


@employee_bp.route("/notes", methods=["GET", "POST"])
@closing_required
def closing_notes():
    current = g.current
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if not text:
            flash("Write something before adding a note.")
        elif len(text) > NOTE_MAX_LENGTH:
            flash(f"Notes can be up to {NOTE_MAX_LENGTH} characters.")
        else:
            db.session.add(ClosingNote(session_id=current.id, text=text))
            db.session.commit()
        return redirect(url_for("employee.closing_notes"))
    return render_template("closing_notes.html", notes=current.notes,
                           max_len=NOTE_MAX_LENGTH)


@employee_bp.route("/notes/<int:note_id>/delete", methods=["POST"])
@closing_required
def delete_note(note_id):
    note = db.session.get(ClosingNote, note_id)
    # The session check means a note can only be deleted from its own closing.
    if note is None or note.session_id != g.current.id:
        flash("That note couldn't be found.")
    else:
        db.session.delete(note)
        db.session.commit()
    return redirect(url_for("employee.closing_notes"))


@employee_bp.route("/finish-closing", methods=["POST"])
@closing_required
def finish_closing():
    current = g.current
    items = get_bar_items(current.bar_id)
    if not items:
        flash("There are no items to count yet. Ask an admin to add stock items.")
        return redirect(url_for("employee.closing_hub"))

    counted_ids = {count.item_id for count in current.counts}
    missing = [item.name for item in items if item.id not in counted_ids]
    if missing:
        shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        flash(f"Count every item before finishing. Still missing: {shown}")
        return redirect(url_for("employee.inventory_check"))

    current.status = "completed"
    current.completed_at = datetime.now()
    db.session.commit()
    session.pop("inventory_session_id", None)
    flash("Closing submitted. Good night!")
    return redirect(url_for("employee.home"))