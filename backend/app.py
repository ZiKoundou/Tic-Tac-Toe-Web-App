from flask import Flask, request, redirect, session, url_for,flash, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
import os

app = Flask(__name__)
app.secret_key = 'mysecretkey'
#makes database at db.sqlite3
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


#---models---#
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)


#initial table creation
@app.before_first_request
def create_tables():
    db.create_all()
    print("[✔] Tables created")
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            password_hash=generate_password_hash('bruh'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin created")

class SecureModelView(ModelView):
    def is_accessible(self):
        user_id = session.get("user_id")
        if not user_id:
            return False
        user = User.query.get(user_id)
        return user and user.is_admin


#---views---#

admin = Admin(app, name='Dashboard', template_mode='bootstrap3')
admin.add_view(SecureModelView(User, db.session))

#get session id
@app.route('/')
def home():
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        return render_template("game.html", user=user)
    return redirect(url_for("login"))

#register route
@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        
        # Check if the username already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash("User already registered. Please log in.", "danger")
            return redirect(url_for("login"))
        
        password_hash = generate_password_hash(password)
        user = User(username=username, password_hash=password_hash)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("register.html")

#login route
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        
        user = User.query.filter_by(username=request.form["username"]).first()
        if user and check_password_hash(user.password_hash, request.form["password"]):
            session["user_id"] = user.id
            flash("Login successful!", "success")
        flash("Invalid login credentials. Please try again.", "danger")
        if user.is_admin:
            return redirect("/admin")
        return redirect(url_for("home"))
    return render_template("login.html")

#log out route
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

#win route
@app.route('/win')
def win():
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        user.wins += 1
        db.session.commit()
        flash("You won this game!", "success")
    return redirect(url_for("home"))

#lost route
@app.route('/lose')
def lose():
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        user.losses += 1
        db.session.commit()
        flash("You lost this game.", "warning")
    return redirect(url_for("home"))

if __name__ == '__main__':
    print("Running app from:", os.getcwd())
    app.run(debug=True)
