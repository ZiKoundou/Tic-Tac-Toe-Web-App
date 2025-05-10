from flask import Flask, request, redirect, session, url_for, flash, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
import os
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
import eventlet.wsgi

rooms = {}  # track board states by room
app = Flask(__name__)
socketio = SocketIO(app, async_mode='eventlet')
app.secret_key = 'mysecretkey'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)

@app.before_first_request
def create_tables():
    db.create_all()
    print("[✔] Tables created")
    if not db.session.execute(db.select(User).filter_by(username='admin')).scalar():
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
        user = db.session.get(User, user_id)
        return user and user.is_admin

admin = Admin(app, name='Dashboard', template_mode='bootstrap3')
admin.add_view(SecureModelView(User, db.session))

@app.route('/')
def home():
    if "user_id" in session:
        user = db.session.get(User, session["user_id"])
        return render_template("game.html", user=user)
    return redirect(url_for("login"))

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        existing_user = db.session.execute(db.select(User).filter_by(username=username)).scalar()
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

@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        user = db.session.execute(db.select(User).filter_by(username=username)).scalar()

        if user and check_password_hash(user.password_hash, request.form["password"]):
            session["user_id"] = user.id
            flash("Login successful!", "success")
            if user.is_admin:
                return redirect("/admin")
            return redirect(url_for("lobby"))

        flash("Invalid login credentials. Please try again.", "danger")

    return render_template("login.html")

@app.route('/logout')
def logout():
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, session["user_id"])
        if user:
            username = user.username

            # Loop through all rooms to remove user from any
            for room_id, data in rooms.items():
                if username in data['players']:
                    del data['players'][username]
                    print(f"{username} removed from room {room_id} on logout")

    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route('/win')
def win():
    if "user_id" in session:
        user = db.session.get(User, session["user_id"])
        user.wins += 1
        db.session.commit()
        flash("You won this game!", "success")
    return redirect(url_for("home"))

@app.route('/lose')
def lose():
    if "user_id" in session:
        user = db.session.get(User, session["user_id"])
        user.losses += 1
        db.session.commit()
        flash("You lost this game.", "warning")
    return redirect(url_for("home"))

@app.route('/admin/reset/<int:user_id>')
def reset_user_stats(user_id):
    if "user_id" not in session:
        flash("You must be logged in.", "danger")
        return redirect(url_for("login"))

    current_user = db.session.get(User, session["user_id"])
    if not current_user.is_admin:
        flash("Unauthorized access.", "danger")
        return redirect(url_for("home"))

    user = db.session.get(User, user_id)
    if user:
        user.wins = 0
        user.losses = 0
        db.session.commit()
        flash(f"Stats reset for user {user.username}.", "info")
    else:
        flash("User not found.", "warning")

    return redirect(url_for("home"))

@app.route('/lobby')
def lobby():
    return render_template("lobby.html")

@app.route('/play/<room_id>')
def play(room_id):
    user = db.session.get(User, session["user_id"]) if "user_id" in session else None
    return render_template("game.html", room_id=room_id, user=user, username=user.username)

@socketio.on('join')
def on_join(data):
    room = data['room']
    username = data['username']

    if room not in rooms:
        rooms[room] = {
            'board': [''] * 9,
            'turn': 'X',
            'players': {}
        }

    if len(rooms[room]['players']) >= 2 and username not in rooms[room]['players']:
        emit('invalid_move', {'message': 'Room is full!'}, to=request.sid)
        return

    if username not in rooms[room]['players']:
        player_role = 'X' if 'X' not in rooms[room]['players'].values() else 'O'
        rooms[room]['players'][username] = player_role
    else:
        player_role = rooms[room]['players'][username]  # if they’re rejoining

    join_room(room)

    emit('joined', {
        'username': username,
        'role': player_role,
        'board': rooms[room]['board']
    }, to=request.sid)

    if len(rooms[room]['players']) == 2:
        emit('start_game', {'message': 'Game can start!'}, to=room)
    
@socketio.on('make_move')
def on_move(data):
    room = data['room']
    index = data['index']
    player = data['player']

    board = rooms.get(room)
    if board and board['board'][index] == '':
        board['board'][index] = player  
        emit('update_board', {'index': index, 'player': player}, to=room)

@socketio.on('reset')
def on_reset(data):
    room = data['room']
    if room in rooms:
        rooms[room]['board'] = [''] * 9
        emit('reset_board', to=room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    username = data['username']
    
    # Remove the player from the room's players dictionary
    if room in rooms and username in rooms[room]['players']:
        del rooms[room]['players'][username]
        print(f"{username} left the room {room}")
        
    leave_room(room)
    emit('player_left', {'username': username}, to=room)

@socketio.on('reload')
def on_reload(data):
    room = data['room']
    username = data['username']
    
    if room in rooms and username in rooms[room]['players']:
        # Clean up the player's state
        del rooms[room]['players'][username]
        print(f"{username} reloaded, leaving room {room}")
        
    # Optionally, allow the player to rejoin or assign them a new room
    join_room(room)

if __name__ == '__main__':
    print("Running app from:", os.getcwd())
    socketio.run(app, debug=True)