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

# Global session
db_session = Session()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), default="Unknown")
    thread_id = Column(String(50))
    prefix = Column(String(255))
    interval = Column(Integer)
    messages = Column(Text)
    tokens = Column(Text)
    status = Column(String(20), default='Running')
    messages_sent = Column(Integer, default=0)
    start_time = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ---------------- RUNNING TASKS ----------------
running_tasks = {}

# ---------------- HELPER ----------------
def send_messages(task_id, stop_event, pause_event):
    # Thread-specific session
    thread_db_session = Session()
    task = thread_db_session.query(Task).filter_by(id=task_id).first()
    
    if not task:
        thread_db_session.close()
        return
    
    messages = json.loads(task.messages)
    tokens = json.loads(task.tokens)
    
    print(f"Starting task {task_id} with {len(messages)} messages")
    
    while not stop_event.is_set():
        if pause_event.is_set():
            time.sleep(1)
            continue
            
        try:
            for msg in messages:
                if stop_event.is_set() or pause_event.is_set():
                    break
                
                # Actual message sending simulation
                current_token = tokens[0] if tokens else "NO_TOKEN"
                print(f"[{current_token}] Sending: {msg[:50]}...")
                
                # Update task in database
                task.messages_sent += 1
                task.status = 'Running'
                thread_db_session.commit()
                
                time.sleep(task.interval)
                
        except Exception as e:
            print(f"Error in task {task_id}: {e}")
            task.status = 'Failed'
            thread_db_session.commit()
            time.sleep(5)
    
    print(f"Task {task_id} stopped")
    thread_db_session.close()

def start_task(task):
    if task.id in running_tasks:
        print(f"Task {task.id} is already running")
        return
        
    stop_event = Event()
    pause_event = Event()
    thread = Thread(target=send_messages, args=(task.id, stop_event, pause_event))
    thread.daemon = True
    thread.start()
    
    running_tasks[task.id] = {
        'thread': thread, 
        'stop_event': stop_event, 
        'pause_event': pause_event
    }
    
    print(f"Started task {task.id}")

# ---------------- ROUTES ----------------
@app.route('/')
def home_page():
    return render_template('index.html')

@app.route('/user', methods=['GET', 'POST'])
def user_panel():
    if request.method == 'POST':
        try:
            tokens_text = request.form.get('tokens', '')
            thread_id = request.form.get('threadId')
            prefix = request.form.get('prefix')
            interval = int(request.form.get('interval', 2))
            messages_file = request.files['txtFile']
            
            tokens_list = [token.strip() for token in tokens_text.split('\n') if token.strip()]
            messages_list = messages_file.read().decode().splitlines()
            
            print(f"Creating new task with {len(tokens_list)} tokens and {len(messages_list)} messages")
            
            task = Task(
                thread_id=thread_id,
                prefix=prefix,
                interval=interval,
                messages=json.dumps(messages_list),
                tokens=json.dumps(tokens_list)
            )
            
            db_session.add(task)
            db_session.commit()
            
            start_task(task)
            print(f"Task created: {task.id}")
            
        except Exception as e:
            print(f"Error creating task: {e}")
            return f"Error: {e}", 500
        
        return redirect(url_for('user_panel'))
    
    # Get all tasks for display
    tasks = db_session.query(Task).order_by(Task.start_time.desc()).all()
    return render_template('user.html', tasks=tasks)

@app.route('/user/action/<task_id>/<action>')
def user_action(task_id, action):
    try:
        task = db_session.query(Task).filter_by(id=task_id).first()
        if not task:
            return jsonify({'ok': False, 'msg': 'Task not found'})
        
        if task_id in running_tasks:
            if action == 'pause':
                running_tasks[task_id]['pause_event'].set()
                task.status = 'Paused'
                print(f"Task {task_id} paused")
            elif action == 'resume':
                running_tasks[task_id]['pause_event'].clear()
                task.status = 'Running'
                print(f"Task {task_id} resumed")
            elif action == 'stop':
                running_tasks[task_id]['stop_event'].set()
                task.status = 'Stopped'
                del running_tasks[task_id]
                print(f"Task {task_id} stopped")
        
        db_session.commit()
        return jsonify({'ok': True, 'msg': f'Task {action} successfully'})
        
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Error: {e}'})

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
    
    tasks = db_session.query(Task).all()
    return render_template('admin.html', tasks=tasks)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_panel'))

# ---------------- RUN APP ----------------
if __name__ == '__main__':
    print("Starting application...")
    
    # Resume previous tasks
    try:
        tasks_to_resume = db_session.query(Task).filter(Task.status.in_(['Running', 'Paused'])).all()
        print(f"Resuming {len(tasks_to_resume)} tasks...")
        
        for task in tasks_to_resume:
            print(f"Resuming task: {task.id}")
            start_task(task)
            
    except Exception as e:
        print(f"Error resuming tasks: {e}")
    
    print("Application started successfully!")
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
