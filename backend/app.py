# app.py
import os
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet

#eventlet.monkey_patch()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", "sqlite:///default.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode="eventlet")

rooms = {}  # in-memory: room_id → { players: {user: X/O}, board: ['']*9, turn: 'X'|'O' }
user_sockets = {}  # socket.id → {username, room}

def check_win(board):
    winning = [
        (0,1,2),(3,4,5),(6,7,8),
        (0,3,6),(1,4,7),(2,5,8),
        (0,4,8),(2,4,6),
    ]
    for a,b,c in winning:
        if board[a] and board[a]==board[b]==board[c]:
            return board[a]
    return None

class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    wins          = db.Column(db.Integer, default=0)
    losses        = db.Column(db.Integer, default=0)
    is_admin      = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<User {self.username}>"

# Admin setup
class SecureModelView(ModelView):
    def is_accessible(self):
        uid = session.get("user_id")
        if not uid:
            return False
        user = db.session.get(User, uid)
        return user and user.is_admin

admin = Admin(app, name="Dashboard", template_mode="bootstrap3")
admin.add_view(SecureModelView(User, db.session))

with app.app_context():
    db.create_all()
    if not User.query.filter_by(is_admin=True).first():
        admin_user = User(
            username="admin",
            password_hash=generate_password_hash("adminpass"),
            is_admin=True
        )
        db.session.add(admin_user)
        db.session.commit()


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("lobby"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"]
        if User.query.filter_by(username=u).first():
            flash("Username taken", "danger")
            return redirect(url_for("register"))
        new = User(
            username=u,
            password_hash=generate_password_hash(p)
        )
        db.session.add(new)
        db.session.commit()
        flash("Registered! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"]
        user = User.query.filter_by(username=u).first()
        if user and check_password_hash(user.password_hash, p):
            session["user_id"] = user.id
            flash("Logged in!", "success")
            return redirect(url_for("lobby"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/lobby", methods=["GET"])
def lobby():
    if "user_id" not in session:
        flash("Please log in", "warning")
        return redirect(url_for("login"))
    user = db.session.get(User, session["user_id"])
    return render_template("lobby.html", user=user)


@app.route("/play/<room_id>")
def play(room_id):
    if "user_id" not in session:
        flash("Please log in", "warning")
        return redirect(url_for("login"))
    user = db.session.get(User, session["user_id"])
    return render_template("game.html", user=user, room_id=room_id, username=user.username)


# ─── Socket Handlers ──────────────────────────────────────────────────────────

@socketio.on("join")
def on_join(data):
    room = data["room"]
    username = data["username"]
    
    if room not in rooms:
        rooms[room] = {"players": {}, "board": ['']*9, "turn": None}

    state = rooms[room]

    # Block if full
    if len(state["players"]) >= 2 and username not in state["players"]:
        emit("invalid_move", {"message": "Room is full"}, to=request.sid)
        return

    # Assign X or O
    if username not in state["players"]:
        sym = 'X' if 'X' not in state["players"].values() else 'O'
        state["players"][username] = sym
    else:
        sym = state["players"][username]

    join_room(room)
    emit("joined", {"role": sym, "board": state["board"]}, to=request.sid)
    user_sockets[request.sid] = {"username": username, "room": room}

    if len(state["players"]) == 2:
        emit("start_game", {}, to=room)

@socketio.on("start_game")
def on_start_game(data):
    room = data["room"]
    state = rooms.get(room)
    if not state or len(state["players"]) != 2:
        return
    state["board"] = ['']*9
    state["turn"] = 'X'
    emit("game_started", {"startingPlayer": "X"}, to=room)

@socketio.on("make_move")
def on_move(data):
    room = data["room"]
    idx = data["index"]
    player = data["player"]
    state = rooms.get(room)
    
    if not state:
        emit("invalid_move", {"message": "Room no longer exists"}, to=request.sid)
        return
    
    if state["turn"] != player or state["board"][idx] != '':
        emit("invalid_move", {"message": "Not your turn or occupied"}, to=request.sid)
        return

    state["board"][idx] = player

    winner_sym = check_win(state["board"])
    if winner_sym:
        win_user = next(u for u, s in state["players"].items() if s == winner_sym)
        lose_user = next(u for u, s in state["players"].items() if s != winner_sym)
        w = User.query.filter_by(username=win_user).first()
        l = User.query.filter_by(username=lose_user).first()
        if w:
            w.wins += 1
        if l:
            l.losses += 1
        db.session.commit()

        emit("update_board", {"index": idx, "player": player, "nextTurn": None}, to=room)
        emit("game_over", {"winner": win_user}, to=room)
        
        return

    if '' not in state["board"]:
        emit("update_board", {"index": idx, "player": player, "nextTurn": None}, to=room)
        emit("game_over", {"winner": None}, to=room)
        
        return

    nxt = 'O' if player == 'X' else 'X'
    state["turn"] = nxt
    emit("update_board", {"index": idx, "player": player, "nextTurn": nxt}, to=room)

@socketio.on("disconnect")
def on_disconnect():
    info = user_sockets.pop(request.sid, None)
    if not info:
        return
    username = info["username"]
    room = info["room"]

    state = rooms.get(room)
    if not state:
        return

    state["players"].pop(username, None)
    if not state["players"]:
        rooms.pop(room, None)
    else:
        emit("opponent_left", {"message": f"Your Opponent {username} left the game."}, to=room)

if __name__ == "__main__":
    socketio.run(app, debug=True)