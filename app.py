from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from threading import Thread, Event
import os, uuid, json, logging, time
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# ---------------- APP SETUP ----------------
app = Flask(__name__)
app.secret_key = "3a4f82d59c6e4f0a8e912a5d1f7c3b2e6f9a8d4c5b7e1d1a4c"
app.debug = True

# ---------------- DATABASE SETUP ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "tasks.db"
engine = create_engine(f'sqlite:///{os.path.join(BASE_DIR, DB_NAME)}?check_same_thread=False')
Base = declarative_base()
Session = sessionmaker(bind=engine)

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50))
    thread_id = Column(String(50))
    prefix = Column(String(255))
    interval = Column(Integer)
    messages = Column(Text)
    tokens = Column(Text)      # Only admin can see
    status = Column(String(20), default='Running')
    messages_sent = Column(Integer, default=0)
    start_time = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ---------------- RUNNING TASKS ----------------
running_tasks = {}  # task_id -> {thread, stop_event, pause_event}

# ---------------- HELPER ----------------
def send_messages(task_id, stop_event, pause_event):
    db_session = Session()
    task = db_session.query(Task).filter_by(id=task_id).first()
    if not task:
        db_session.close()
        return
    messages = json.loads(task.messages)
    while not stop_event.is_set():
        if pause_event.is_set():
            time.sleep(1)
            continue
        try:
            # Simulate sending message (replace with actual API if needed)
            for msg in messages:
                if stop_event.is_set() or pause_event.is_set():
                    break
                task.messages_sent += 1
                task.status = 'Running'
                db_session.commit()
                logging.info(f"[{task.username}] Sent message: {msg[:30]}")
                time.sleep(task.interval)
        except Exception as e:
            logging.error(f"Task {task.id} error: {e}")
            task.status = 'Failed'
            db_session.commit()
            time.sleep(5)
    db_session.close()

def start_task(task):
    stop_event = Event()
    pause_event = Event()
    thread = Thread(target=send_messages, args=(task.id, stop_event, pause_event))
    thread.daemon = True
    thread.start()
    running_tasks[task.id] = {'thread': thread, 'stop_event': stop_event, 'pause_event': pause_event}

# ---------------- ROUTES ----------------
@app.route('/', methods=['GET', 'POST'])
def user_panel():
    db_session = Session()
    if request.method == 'POST':
        username = request.form.get('username')
        thread_id = request.form.get('threadId')
        prefix = request.form.get('prefix')
        interval = int(request.form.get('interval', 2))
        messages_file = request.files['txtFile']
        messages = json.dumps(messages_file.read().decode().splitlines())
        tokens = json.dumps([])  # Not shown for user

        task = Task(username=username, thread_id=thread_id, prefix=prefix,
                    interval=interval, messages=messages, tokens=tokens)
        db_session.add(task)
        db_session.commit()
        start_task(task)
    # Load tasks for this user
    username = request.args.get('username', None)
    tasks = []
    if username:
        tasks = db_session.query(Task).filter_by(username=username).all()
    db_session.close()
    return render_template('user.html', tasks=tasks)

@app.route('/user/action/<task_id>/<action>')
def user_action(task_id, action):
    db_session = Session()
    task = db_session.query(Task).filter_by(id=task_id).first()
    if not task:
        db_session.close()
        return jsonify({'ok': False, 'msg': 'Task not found'})
    if task_id in running_tasks:
        if action == 'pause':
            running_tasks[task_id]['pause_event'].set()
            task.status = 'Paused'
        elif action == 'resume':
            running_tasks[task_id]['pause_event'].clear()
            task.status = 'Running'
        elif action == 'stop':
            running_tasks[task_id]['stop_event'].set()
            task.status = 'Stopped'
            del running_tasks[task_id]
    db_session.commit()
    db_session.close()
    return jsonify({'ok': True, 'msg': f'Task {action} successfully'})

# ---------------- ADMIN ----------------
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'AXSHU143':
            session['admin'] = True
            return redirect(url_for('admin_panel'))
    if not session.get('admin'):
        return render_template('login.html')
    db_session = Session()
    tasks = db_session.query(Task).all()
    db_session.close()
    return render_template('admin.html', tasks=tasks)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_panel'))

# ---------------- RUN APP ----------------
if __name__ == '__main__':
    # Resume all running tasks from DB
    db_session = Session()
    for task in db_session.query(Task).filter(Task.status=='Running').all():
        start_task(task)
    db_session.close()
    
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
