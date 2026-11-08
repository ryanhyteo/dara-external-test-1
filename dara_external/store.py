import os
import sqlite3
from pathlib import Path
from google.protobuf.json_format import MessageToJson, Parse
from a2a.types import Task, TaskState, Message, Part, Role
from uuid import uuid4

class Store:
    """Single-instance durable task + message idempotency store. Never logs case text."""
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.db = sqlite3.connect(str(Path(directory) / 'tasks.sqlite3'))
        os.chmod(Path(directory) / 'tasks.sqlite3', 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests(message_id TEXT PRIMARY KEY, digest TEXT NOT NULL, task_id TEXT NOT NULL)')
        self.db.commit()

    def get(self, task_id):
        row = self.db.execute('SELECT body FROM tasks WHERE id=?', (task_id,)).fetchone()
        return Parse(row[0], Task()) if row else None

    def save(self, task):
        self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?)', (task.id, MessageToJson(task)))
        self.db.commit()

    def duplicate(self, message_id):
        return self.db.execute('SELECT digest, task_id FROM requests WHERE message_id=?', (message_id,)).fetchone()

    def create(self, message_id, digest, task):
        with self.db:
            self.db.execute('INSERT INTO tasks VALUES(?,?)', (task.id, MessageToJson(task)))
            self.db.execute('INSERT INTO requests VALUES(?,?,?)', (message_id, digest, task.id))

    def all(self):
        return [Parse(row[0], Task()) for row in self.db.execute('SELECT body FROM tasks ORDER BY rowid DESC')]

    def recover(self):
        # Never replay interrupted provider calls after restart: outcome may be unknown.
        for task in self.all():
            if task.status.state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING):
                task.status.state = TaskState.TASK_STATE_FAILED
                task.status.timestamp.GetCurrentTime()
                task.status.message.CopyFrom(Message(message_id=str(uuid4()), role=Role.ROLE_AGENT,
                    parts=[Part(text='Service restarted during execution; submit a new message ID to retry.')]))
                task.metadata.update({'failure_reason': 'service_restarted'})
                self.save(task)
