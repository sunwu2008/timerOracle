from flask import Flask, request, render_template, send_from_directory, redirect, url_for
import cx_Oracle
from configparser import ConfigParser
import pandas as pd
import csv
import os
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3
import datetime

app = Flask(__name__)

# Database Configuration
config = ConfigParser()
config.read('config.ini')

DB_CONFIG = {
    'host': config.get('oracle', 'host'),
    'port': config.get('oracle', 'port'),
    'user': config.get('oracle', 'user'),
    'password': config.get('oracle', 'password'),
    'sid': config.get('oracle', 'sid')
}

# Establishing connection pool for Oracle
pool = cx_Oracle.SessionPool(DB_CONFIG['user'], DB_CONFIG['password'],
                             f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['sid']}",
                             min=2, max=5, increment=1, threaded=True, getmode=cx_Oracle.SPOOL_ATTRVAL_WAIT)

scheduler = BackgroundScheduler()
scheduler.start()

DATABASE_NAME = "tasks.db"


def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            query TEXT,
            time_to_run TEXT,
            status TEXT,
            filename TEXT
        )
        """)


init_db()


@app.route('/')
def index():
    tasks = get_all_tasks()
    return render_template('index.html', tasks=tasks)


@app.route('/get_latest_tasks')
def get_latest_tasks():
    tasks = get_all_tasks()
    tasks_list = [{'id': task[0], 'query': task[1], 'time_to_run': task[2], 'status': task[3], 'filename': task[4]} for
                  task in tasks]
    return {'tasks': tasks_list}


@app.route('/schedule_job', methods=['POST'])
def schedule_job():
    query = get_query_from_request()
    time_to_run = get_time_to_run_from_request()

    if not query or not time_to_run:
        return {"error": "Both SQL query and time to run must be provided."}, 400

    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute("INSERT INTO tasks (query, time_to_run, status, filename) VALUES (?, ?, ?, ?)",
                     (query, time_to_run, 'scheduled', None))

    scheduler.add_job(execute_sql, 'date', [query, time_to_run], run_date=time_to_run)
    return redirect(url_for('index'))


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(os.getcwd(), filename)


@app.route('/delete_task/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    try:
        with sqlite3.connect(DATABASE_NAME) as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return {"status": "Task deleted successfully"}, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_all_tasks():
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DATABASE_NAME) as conn:
        # Update task status if time_to_run has passed and status is still "scheduled"
        conn.execute("UPDATE tasks SET status = 'completed' WHERE time_to_run <= ? AND status = 'scheduled'",
                     (current_time,))
        tasks = conn.execute("SELECT * FROM tasks ORDER BY time_to_run DESC").fetchall()
    return tasks


def get_query_from_request():
    uploaded_file = request.files.get('sqlfile')
    if uploaded_file:
        return uploaded_file.read().decode('utf-8')
    return request.form['query']


def get_time_to_run_from_request():
    time_to_run = request.form.get('time_to_run') or request.form.get('manual_time_to_run')
    if "T" in time_to_run:
        time_to_run = time_to_run.replace("T", " ") + ":00"
    return time_to_run


def execute_sql(query, time_to_run):
    try:
        conn = pool.acquire()
        cursor = conn.cursor()
        cursor.execute(query)

        rows = cursor.fetchall()
        column_names = [i[0] for i in cursor.description]
        df = pd.DataFrame(rows, columns=column_names)

        csv_filename = "result_" + time_to_run.replace(":", "_").replace(" ", "_") + ".csv"
        df.to_csv(csv_filename, index=False, quoting=csv.QUOTE_NONNUMERIC)

        cursor.close()

        # Update task in the SQLite database
        with sqlite3.connect(DATABASE_NAME) as sqlite_conn:
            sqlite_conn.execute("UPDATE tasks SET status = ?, filename = ? WHERE query = ? AND time_to_run = ?",
                                ('completed', csv_filename, query, time_to_run))

    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        if conn:
            pool.release(conn)


if __name__ == '__main__':
    app.run(debug=True)
