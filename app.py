from flask import Flask, render_template
from models import db, Bar, Category, Role, Employee, ClosingTask

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-change-later"
app.config["CODE_PEPPER"] = "dev-pepper-change-later"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///inventory.db"
db.init_app(app)

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response

with app.app_context():
    db.create_all()

    if Bar.query.count() == 0:
        bar = Bar(name="Main Bar")
        db.session.add(bar)
        db.session.commit()
        for name in ["Spirits", "Liqueurs", "Beer", "Cider"]:
            db.session.add(Category(name=name, bar_id=bar.id))
        db.session.commit()
    
    bar = Bar.query.first()

    if Role.query.count() == 0:
        admin_role = Role(name="admin")
        employee_role = Role(name="employee")
        db.session.add_all([admin_role, employee_role])
        db.session.commit()

    if Employee.query.filter_by(role_id=Role.query.filter_by(name="admin").first().id).count() == 0:
        admin = Employee(name="Admin", bar_id=bar.id, role_id=Role.query.filter_by(name="admin").first().id)
        admin.set_pin("1234")
        admin.must_change_pin = True
        db.session.add(admin)
        db.session.commit()
    
    if ClosingTask.query.count() == 0:
        for description in ["Wipe down surfaces", "Garnish prep", "Batch prep"]:
            db.session.add(ClosingTask(bar_id=bar.id, description=description))
        db.session.commit()

from routes.employee import employee_bp
from routes.admin import admin_bp
app.register_blueprint(employee_bp, url_prefix="/employee")
app.register_blueprint(admin_bp, url_prefix="/admin")

@app.route("/")
def landing():
    return render_template("landing.html")

if __name__ == "__main__":
    app.run(debug=True)