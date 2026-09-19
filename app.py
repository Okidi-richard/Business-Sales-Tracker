import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
database_url = os.environ.get("DATABASE_URL", "sqlite:///sales_tracker.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    business_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")
    subscription_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def subscription_active(self):
        return self.subscription_expires and self.subscription_expires > datetime.utcnow()
    
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(100), nullable=True)
    buying_price = db.Column(db.Float, nullable=False, default=0)
    selling_price = db.Column(db.Float, nullable=False, default=0)
    quantity = db.Column(db.Float, nullable=False, default=0)
    low_stock_level = db.Column(db.Float, nullable=False, default=5)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Product {self.name}>"
   

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        business_name = request.form["business_name"].strip()
        phone = request.form["phone"].strip()
        email = request.form.get("email", "").strip() or None
        password = request.form["password"]

        if User.query.filter_by(phone=phone).first():
            flash("That phone number is already registered.", "error")
            return redirect(url_for("register"))
        if email and User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
            return redirect(url_for("register"))

        user = User(
            name=name,
            business_name=business_name,
            phone=phone,
            email=email,
            password_hash=generate_password_hash(password),
            role="user"
        )
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form["identifier"].strip()
        password = request.form["password"]
        user = User.query.filter((User.phone == identifier) | (User.email == identifier)).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid login details.", "error")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session["role"] = user.role
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=user)

@app.route("/subscription")
def subscription():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return render_template("subscription.html", user=user)
@app.route("/admin/activate-subscription/<int:user_id>", methods=["POST"])
def activate_subscription(user_id):
    user = current_user()

    if not user or user.role != "admin":
        flash("Administrator access required.", "error")
        return redirect(url_for("dashboard"))

    target_user = db.session.get(User, user_id)

    if not target_user:
        flash("User not found.", "error")
        return redirect(url_for("admin"))

    target_user.subscription_expires = datetime.utcnow() + timedelta(days=30)
    db.session.commit()

    flash(f"Subscription activated for {target_user.name} for 30 days.", "success")
    return redirect(url_for("admin"))
@app.route("/activate-subscription", methods=["POST"])
def activate_my_subscription():
    user = current_user()

    if not user:
        return redirect(url_for("login"))

    user.subscription_expires = datetime.utcnow() + timedelta(days=30)
    db.session.commit()

    flash("Subscription activated for 30 days.", "success")
    return redirect(url_for("dashboard"))
@app.route("/admin")
def admin():
    user = current_user()
    if not user or user.role != "admin":
        flash("Administrator access required.", "error")
        return redirect(url_for("dashboard"))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin.html", user=user, users=users)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
