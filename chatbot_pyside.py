
import sys
import sqlite3
import threading
import requests

from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QLineEdit, QTextBrowser, QListWidget,
    QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox
)

# ============================================================
# Configuration
# ============================================================
APP_TITLE = "My AI Assistant"
DB_PATH = Path(r"D:\My_AI_Chatbot\chatbot_memory.db")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

BG = "#080D18"
SIDEBAR = "#0C1422"
PANEL = "#101B2B"
PANEL_LIGHT = "#152438"
BORDER = "#20344A"
TEXT = "#EDF6FF"
MUTED = "#8BA2BA"
CYAN = "#43D9F5"
CYAN_HOVER = "#83E9FA"
USER_BG = "#103447"
AI_BG = "#121F31"


# ============================================================
# Database: use the existing database on D drive
# ============================================================
db_lock = threading.Lock()


def db_connect():
    connection = sqlite3.connect(str(DB_PATH), timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def setup_database():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Existing database not found:\n{DB_PATH}\n\n"
            "The app will not create a new database automatically."
        )

    with db_lock:
        with db_connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    key TEXT PRIMARY KEY, value TEXT
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL, due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'Pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations(id) ON DELETE CASCADE
                )
            """)


def get_all_chats():
    with db_lock, db_connect() as con:
        return con.execute(
            "SELECT id, title FROM conversations "
            "ORDER BY updated_at DESC, id DESC"
        ).fetchall()


def load_chat_messages(chat_id):
    with db_lock, db_connect() as con:
        return con.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id=? ORDER BY id",
            (chat_id,)
        ).fetchall()


def create_chat(title):
    title = " ".join(title.split())[:45] or "New Chat"
    with db_lock, db_connect() as con:
        cursor = con.execute(
            "INSERT INTO conversations(title) VALUES(?)", (title,)
        )
        return cursor.lastrowid


def save_chat_message(chat_id, role, content):
    with db_lock, db_connect() as con:
        con.execute(
            "INSERT INTO messages(conversation_id, role, content) "
            "VALUES (?, ?, ?)", (chat_id, role, content)
        )
        con.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (chat_id,)
        )


def get_memory(key):
    with db_lock, db_connect() as con:
        row = con.execute(
            "SELECT value FROM memories WHERE key=?",
            (key.strip().lower(),)
        ).fetchone()
    return row[0] if row else None


def save_memory(key, value):
    with db_lock, db_connect() as con:
        con.execute(
            "INSERT INTO memories(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key.strip().lower(), value.strip())
        )


def get_all_memories():
    with db_lock, db_connect() as con:
        return con.execute(
            "SELECT key, value FROM memories ORDER BY key"
        ).fetchall()


def add_task(task):
    with db_lock, db_connect() as con:
        cur = con.execute(
            "INSERT INTO tasks(task,status) VALUES(?,'Pending')",
            (task.strip(),)
        )
        return cur.lastrowid


def get_tasks(pending_only=False):
    sql = "SELECT id, task, due_at, status FROM tasks"
    if pending_only:
        sql += " WHERE lower(status)='pending'"
    sql += " ORDER BY id"
    with db_lock, db_connect() as con:
        return con.execute(sql).fetchall()


def complete_task(task_id):
    with db_lock, db_connect() as con:
        cur = con.execute(
            "UPDATE tasks SET status='Completed' WHERE id=?", (task_id,)
        )
        return cur.rowcount > 0


# ============================================================
# Worker signals: keep AI network calls off the GUI thread
# ============================================================
class WorkerSignals(QObject):
    result = Signal(str)
    error = Signal(str)


def ask_ollama(prompt, signals):
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=180
        )
        response.raise_for_status()
        answer = response.json().get("response", "").strip()
        signals.result.emit(answer or "The model returned an empty response.")
    except requests.exceptions.ConnectionError:
        signals.error.emit(
            "Cannot connect to Ollama. Make sure Ollama is running "
            f"and {OLLAMA_MODEL} is installed."
        )
    except requests.exceptions.Timeout:
        signals.error.emit("The AI took too long to respond. Please try again.")
    except Exception as exc:
        signals.error.emit(f"AI request failed:\n{exc}")


# ============================================================
# Main window
# ============================================================
class AssistantWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1250, 820)
        self.setMinimumSize(900, 620)

        self.current_chat_id = None
        self.conversation = []
        self.busy = False
        self.chat_ids = []
        self.signals = WorkerSignals()
        self.signals.result.connect(self.on_ai_result)
        self.signals.error.connect(self.on_ai_error)

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 10pt;
            }}
            QFrame#Sidebar {{
                background: {SIDEBAR};
                border-right: 1px solid {BORDER};
            }}
            QFrame#Header {{
                background: {BG};
                border-bottom: 1px solid {BORDER};
            }}
            QLabel#Brand {{
                color: {CYAN};
                font-size: 22pt;
                font-weight: 700;
                letter-spacing: 2px;
            }}
            QLabel#Eyebrow {{
                color: {MUTED};
                font-size: 8pt;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QLabel#Title {{
                color: {TEXT};
                font-size: 19pt;
                font-weight: 650;
            }}
            QLabel#Status {{
                color: {CYAN};
                font-size: 9pt;
            }}
            QPushButton {{
                background: {PANEL_LIGHT};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 11px 14px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: #1A3045;
                border-color: {CYAN};
            }}
            QPushButton#Primary {{
                background: {CYAN};
                color: #06131C;
                font-weight: 700;
                border: none;
                text-align: center;
            }}
            QPushButton#Primary:hover {{
                background: {CYAN_HOVER};
            }}
            QListWidget {{
                background: transparent;
                color: {MUTED};
                border: none;
                outline: none;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 11px 10px;
                border-radius: 8px;
                margin: 2px 0;
            }}
            QListWidget::item:selected {{
                background: #163044;
                color: {TEXT};
                border-left: 3px solid {CYAN};
            }}
            QLineEdit {{
                background: {PANEL};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 12px;
                padding: 15px;
                selection-background-color: {CYAN};
            }}
            QLineEdit:focus {{
                border-color: {CYAN};
            }}
            QTextBrowser {{
                background: transparent;
                color: {TEXT};
                border: none;
                padding: 6px;
            }}
            QSplitter::handle {{
                background: {BG};
                width: 3px;
            }}
        """)

        self.build_ui()
        self.refresh_chat_list()
        self.show_welcome()

    def build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(290)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 28, 18, 20)
        side.setSpacing(12)

        brand = QLabel("✦  MY AI")
        brand.setObjectName("Brand")
        side.addWidget(brand)

        tagline = QLabel("PERSONAL INTELLIGENCE")
        tagline.setObjectName("Eyebrow")
        side.addWidget(tagline)
        side.addSpacing(18)

        new_button = QPushButton("＋   New conversation")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self.new_chat)
        side.addWidget(new_button)

        tasks_button = QPushButton("▦   My tasks")
        tasks_button.clicked.connect(self.show_tasks)
        side.addWidget(tasks_button)

        memory_button = QPushButton("◈   My memories")
        memory_button.clicked.connect(self.show_memories)
        side.addWidget(memory_button)
        side.addSpacing(22)

        recent_title = QLabel("RECENT CONVERSATIONS")
        recent_title.setObjectName("Eyebrow")
        side.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.itemClicked.connect(self.load_selected_chat)
        side.addWidget(self.recent_list, 1)

        footer = QLabel(f"●  LOCAL MODEL\n    {OLLAMA_MODEL}")
        footer.setObjectName("Status")
        side.addWidget(footer)

        # Main content
        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(26, 0, 26, 22)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(4, 24, 4, 20)
        header_layout.setSpacing(6)

        title = QLabel("Your AI workspace")
        title.setObjectName("Title")
        header_layout.addWidget(title)

        self.status = QLabel("●  Ready")
        self.status.setObjectName("Status")
        header_layout.addWidget(self.status)
        layout.addWidget(header)

        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(True)
        self.chat_view.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.chat_view, 1)

        composer = QFrame()
        composer.setStyleSheet(
            f"QFrame {{ background: {PANEL}; border: 1px solid {BORDER}; "
            "border-radius: 15px; }"
        )
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(12, 8, 8, 8)
        composer_layout.setSpacing(10)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Message your assistant...")
        self.input.returnPressed.connect(self.send_message)
        composer_layout.addWidget(self.input, 1)

        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("Primary")
        self.send_button.setFixedSize(48, 46)
        self.send_button.clicked.connect(self.send_message)
        composer_layout.addWidget(self.send_button)

        layout.addSpacing(16)
        layout.addWidget(composer)

        splitter.addWidget(sidebar)
        splitter.addWidget(main)
        splitter.setSizes([265, 985])

    def append_message(self, sender, message):
        is_user = sender == "You"
        bg = USER_BG if is_user else AI_BG
        name_color = CYAN if is_user else MUTED
        safe_message = (
            message.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        html = f"""
        <div style="margin:18px 8px;">
          <div style="color:{name_color};font-size:10px;
                      font-weight:bold;margin-bottom:7px;">{sender.upper()}</div>
          <div style="background:{bg};border:1px solid {BORDER};
                      border-radius:13px;padding:14px 17px;
                      line-height:1.55;">{safe_message}</div>
        </div>
        """
        self.chat_view.append(html)

    def show_welcome(self):
        self.chat_view.clear()
        self.append_message(
            "MY AI",
            "Welcome to your personal AI workspace.\n"
            "Your local assistant is ready. What would you like to do?"
        )

    def new_chat(self):
        if self.busy:
            return
        self.current_chat_id = None
        self.conversation = []
        self.show_welcome()
        self.status.setText("●  New conversation")
        self.recent_list.clearSelection()
        self.input.setFocus()

    def refresh_chat_list(self, select_id=None):
        self.recent_list.clear()
        self.chat_ids = []
        for chat_id, title in get_all_chats():
            self.chat_ids.append(chat_id)
            self.recent_list.addItem(title)
            if chat_id == select_id:
                self.recent_list.setCurrentRow(len(self.chat_ids) - 1)

    def load_selected_chat(self, item):
        if self.busy:
            return
        index = self.recent_list.row(item)
        if index < 0 or index >= len(self.chat_ids):
            return
        self.current_chat_id = self.chat_ids[index]
        self.conversation = []
        self.chat_view.clear()
        for role, content in load_chat_messages(self.current_chat_id):
            sender = "You" if role == "user" else "MY AI"
            self.conversation.append({"role": role, "content": content})
            self.append_message(sender, content)
        self.status.setText("●  Conversation loaded")

    def show_tasks(self):
        tasks = get_tasks()
        if not tasks:
            content = "You have no tasks saved yet."
        else:
            content = "\n".join(
                f"{tid}. {task} — {status}" +
                (f" (Due: {due})" if due else "")
                for tid, task, due, status in tasks
            )
        self.chat_view.clear()
        self.append_message("MY AI · TASKS", content)
        self.status.setText("●  Your tasks")

    def show_memories(self):
        memories = get_all_memories()
        content = "\n".join(f"• {key}: {value}" for key, value in memories)
        if not content:
            content = "No saved memories yet."
        self.chat_view.clear()
        self.append_message("MY AI · MEMORY", content)
        self.status.setText("●  Your memories")

    def build_prompt(self, question):
        history = ""
        for item in self.conversation[-10:]:
            role = "User" if item["role"] == "user" else "Assistant"
            history += f"{role}: {item['content']}\n"

        memories = "\n".join(
            f"{key}: {value}" for key, value in get_all_memories()
        ) or "No saved memories."

        return (
            "You are a helpful personal AI assistant. Answer clearly and naturally. "
            "If unsure, say so rather than inventing facts.\n\n"
            f"Saved user memory:\n{memories}\n\n"
            f"Recent conversation:\n{history}\n"
            f"User: {question}\nAssistant:"
        )

    def handle_command(self, question):
        text = question.strip()
        lower = text.lower()

        if lower.startswith("remember my name is "):
            name = text[len("remember my name is "):].strip()
            if name:
                save_memory("name", name)
                return f"I'll remember your name is {name}."

        if lower in (
            "what is my name?", "what's my name?",
            "who am i?", "do you know my name?"
        ):
            name = get_memory("name")
            return f"Your name is {name}." if name else (
                "You haven't told me your name yet. "
                "Say: remember my name is ..."
            )

        if lower in ("show my memories", "show memory",
                     "what do you remember about me?"):
            memories = get_all_memories()
            if not memories:
                return "I haven't saved any memories yet."
            return "\n".join(f"• {k}: {v}" for k, v in memories)

        if lower.startswith("remember that "):
            note = text[len("remember that "):].strip()
            if note:
                key = "note_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                save_memory(key, note)
                return f"Saved to memory: {note}"

        prefixes = ("add a task to ", "add task to ", "add task: ",
                    "add a task: ")
        for prefix in prefixes:
            if lower.startswith(prefix):
                task = text[len(prefix):].strip()
                if task:
                    tid = add_task(task)
                    return f"Task added successfully!\nTask ID: {tid}\nTask: {task}"
                return "Please tell me what task to add."

        if lower in ("show my pending tasks", "show pending tasks",
                     "list pending tasks"):
            tasks = get_tasks(pending_only=True)
            return "\n".join(
                f"{tid}. {task} — {status}" for tid, task, due, status in tasks
            ) or "No pending tasks."

        if lower in ("show all my tasks", "show all tasks", "list all tasks"):
            tasks = get_tasks()
            return "\n".join(
                f"{tid}. {task} — {status}" for tid, task, due, status in tasks
            ) or "No tasks saved."

        if lower.startswith("complete task "):
            number = text[len("complete task "):].strip()
            if number.isdigit():
                tid = int(number)
                return (
                    f"Task {tid} marked as completed."
                    if complete_task(tid)
                    else f"Task {tid} was not found."
                )
            return "Use this format: complete task 1"

        return None

    def send_message(self):
        if self.busy:
            return
        question = self.input.text().strip()
        if not question:
            return

        self.input.clear()
        if self.current_chat_id is None:
            self.current_chat_id = create_chat(question)

        self.conversation.append({"role": "user", "content": question})
        save_chat_message(self.current_chat_id, "user", question)
        self.append_message("You", question)
        self.refresh_chat_list(select_id=self.current_chat_id)

        answer = self.handle_command(question)
        if answer is not None:
            self.conversation.append({"role": "assistant", "content": answer})
            save_chat_message(self.current_chat_id, "assistant", answer)
            self.append_message("MY AI", answer)
            return

        self.busy = True
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.status.setText("●  Thinking...")

        prompt = self.build_prompt(question)
        threading.Thread(
            target=ask_ollama,
            args=(prompt, self.signals),
            daemon=True
        ).start()

    def on_ai_result(self, answer):
        self.finish_response(answer)

    def on_ai_error(self, error):
        self.finish_response("Error: " + error)

    def finish_response(self, answer):
        self.busy = False
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.conversation.append({"role": "assistant", "content": answer})
        if self.current_chat_id is not None:
            save_chat_message(self.current_chat_id, "assistant", answer)
        self.append_message("MY AI", answer)
        self.status.setText("●  Ready")
        self.input.setFocus()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    try:
        setup_database()
        window = AssistantWindow()
        window.show()
    except Exception as exc:
        QMessageBox.critical(None, "My AI Assistant", str(exc))
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
