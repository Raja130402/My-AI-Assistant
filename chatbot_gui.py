import tkinter as tk
from tkinter import messagebox
import sqlite3
import threading
import queue
from pathlib import Path
from datetime import datetime
import requests

# ============================================================
# Configuration
# ============================================================
APP_TITLE = "My AI Assistant"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

import sys

if getattr(sys, "frozen", False):
    DB_PATH = Path(r"D:\My_AI_Chatbot\chatbot_memory.db")
else:
    DB_PATH = Path(__file__).with_name("chatbot_memory.db")


BG = "#0F172A"
SIDEBAR = "#111827"
CHAT_BG = "#1E293B"
USER_BG = "#065F46"
AI_BG = "#334155"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
GREEN = "#10B981"
GREEN_HOVER = "#34D399"
INPUT_BG = "#1E293B"
BORDER = "#263449"

# ============================================================
# Database: existing database is reused; it is never deleted.
# ============================================================
db_lock = threading.Lock()


def db_connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def setup_database():
    with db_lock:
        with db_connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'Pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute("""
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


def save_memory(key, value):
    with db_lock:
        with db_connect() as connection:
            connection.execute(
                "INSERT INTO memories(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key.strip().lower(), value.strip()),
            )


def get_memory(key):
    with db_lock:
        with db_connect() as connection:
            row = connection.execute(
                "SELECT value FROM memories WHERE key = ?", (key.strip().lower(),)
            ).fetchone()
    return row[0] if row else None


def get_all_memories():
    with db_lock:
        with db_connect() as connection:
            return connection.execute(
                "SELECT key, value FROM memories ORDER BY key"
            ).fetchall()


def create_chat(title):
    clean_title = " ".join(title.split())[:45] or "New Chat"
    with db_lock:
        with db_connect() as connection:
            cursor = connection.execute(
                "INSERT INTO conversations(title) VALUES(?)", (clean_title,)
            )
            return cursor.lastrowid


def update_chat_timestamp(chat_id):
    with db_lock:
        with db_connect() as connection:
            connection.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (chat_id,),
            )


def save_chat_message(chat_id, role, content):
    with db_lock:
        with db_connect() as connection:
            connection.execute(
                "INSERT INTO messages(conversation_id, role, content) VALUES(?, ?, ?)",
                (chat_id, role, content),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (chat_id,),
            )


def load_chat_messages(chat_id):
    with db_lock:
        with db_connect() as connection:
            return connection.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id",
                (chat_id,),
            ).fetchall()


def get_all_chats():
    with db_lock:
        with db_connect() as connection:
            return connection.execute(
                "SELECT id, title FROM conversations ORDER BY updated_at DESC, id DESC"
            ).fetchall()


def add_task(task):
    with db_lock:
        with db_connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(task, status) VALUES(?, 'Pending')", (task.strip(),)
            )
            return cursor.lastrowid


def get_tasks(pending_only=False):
    query = "SELECT id, task, due_at, status FROM tasks"
    if pending_only:
        query += " WHERE lower(status) = 'pending'"
    query += " ORDER BY id"
    with db_lock:
        with db_connect() as connection:
            return connection.execute(query).fetchall()


def complete_task(task_id):
    with db_lock:
        with db_connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status='Completed' WHERE id=?", (task_id,)
            )
            return cursor.rowcount > 0


def format_tasks(tasks):
    if not tasks:
        return "You don't have any tasks in this list yet."
    lines = []
    for task_id, task, due_at, status in tasks:
        line = f"{task_id}. {task} — {status}"
        if due_at:
            line += f" (Due: {due_at})"
        lines.append(line)
    return "\n".join(lines)


# ============================================================
# Application state
# ============================================================
conversation = []
current_chat_id = None
busy = False
chat_ids = []
ui_queue = queue.Queue()

# ============================================================
# Main window
# ============================================================
root = tk.Tk()
root.title(APP_TITLE)
root.geometry("1100x720")
root.minsize(850, 560)
root.configure(bg=BG)

root.grid_rowconfigure(0, weight=1)
root.grid_columnconfigure(1, weight=1)

# Sidebar
sidebar = tk.Frame(root, bg=SIDEBAR, width=250)
sidebar.grid(row=0, column=0, sticky="nsew")
sidebar.grid_propagate(False)
sidebar.grid_rowconfigure(4, weight=1)

brand = tk.Frame(sidebar, bg=SIDEBAR)
brand.pack(fill="x", padx=20, pady=(24, 20))
tk.Label(brand, text="✦  MY AI", bg=SIDEBAR, fg=GREEN,
         font=("Segoe UI", 17, "bold")).pack(anchor="w")
tk.Label(brand, text="Your personal assistant", bg=SIDEBAR, fg=MUTED,
         font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))


def new_chat():
    global current_chat_id, conversation
    if busy:
        return
    current_chat_id = None
    conversation = []
    clear_chat_messages()
    display_message("AI", "Hello! I'm your personal AI assistant. How can I help you today?")
    status_label.config(text="New chat", fg=GREEN)
    recent_listbox.selection_clear(0, tk.END)
    message_entry.focus_set()


new_chat_button = tk.Button(
    sidebar, text="＋  New chat", command=new_chat,
    bg=GREEN, fg="#06281D", activebackground=GREEN_HOVER,
    activeforeground="#06281D", font=("Segoe UI", 10, "bold"),
    relief="flat", bd=0, cursor="hand2", padx=12, pady=11,
)
new_chat_button.pack(fill="x", padx=16, pady=(0, 22))


# Recent chats heading
tk.Label(
    sidebar,
    text="YOUR CONVERSATIONS",
    bg=SIDEBAR,
    fg=MUTED,
    font=("Segoe UI", 9, "bold"),
).pack(anchor="w", padx=20, pady=(4, 12))

# Recent chats list
recent_frame = tk.Frame(sidebar, bg=SIDEBAR)
recent_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

recent_scroll = tk.Scrollbar(
    recent_frame,
    orient="vertical",
    command=lambda *args: recent_listbox.yview(*args),
)

recent_listbox = tk.Listbox(
    recent_frame,
    bg=SIDEBAR,
    fg=TEXT,
    selectbackground="#263B46",
    selectforeground="#FFFFFF",
    activestyle="none",
    relief="flat",
    bd=0,
    font=("Segoe UI", 10),
    yscrollcommand=recent_scroll.set,
    highlightthickness=0,
    exportselection=False,
    selectborderwidth=0,
)

recent_listbox.pack(side="left", fill="both", expand=True)
recent_scroll.pack(side="right", fill="y")

recent_listbox.bind(
    "<<ListboxSelect>>",
    lambda event: load_selected_chat(),
)

# Sidebar footer
sidebar_footer = tk.Frame(sidebar, bg=SIDEBAR)
sidebar_footer.pack(fill="x", padx=18, pady=(8, 16))

tk.Label(
    sidebar_footer,
    text=f"● LOCAL AI  ·  {OLLAMA_MODEL}",
    bg=SIDEBAR,
    fg=GREEN,
    font=("Segoe UI", 9),
).pack(anchor="w")


sidebar_footer = tk.Frame(sidebar, bg=SIDEBAR)
sidebar_footer.pack(fill="x", padx=18, pady=(0, 16))
tk.Label(sidebar_footer, text=f"Local AI  •  {OLLAMA_MODEL}", bg=SIDEBAR,
         fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

# Main chat area
main = tk.Frame(root, bg=BG)
main.grid(row=0, column=1, sticky="nsew")
main.grid_rowconfigure(1, weight=1)
main.grid_columnconfigure(0, weight=1)

header = tk.Frame(main, bg=BG, highlightbackground=BORDER, highlightthickness=1)
header.grid(row=0, column=0, sticky="ew")
header.grid_columnconfigure(0, weight=1)
tk.Label(header, text="Chat assistant", bg=BG, fg=TEXT,
         font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w", padx=24, pady=(15, 0))
status_label = tk.Label(header, text="Ready", bg=BG, fg=GREEN,
                        font=("Segoe UI", 9))
status_label.grid(row=1, column=0, sticky="w", padx=25, pady=(2, 14))

# Scrollable message area
chat_area = tk.Frame(main, bg=BG)
chat_area.grid(row=1, column=0, sticky="nsew")
chat_area.grid_rowconfigure(0, weight=1)
chat_area.grid_columnconfigure(0, weight=1)
chat_canvas = tk.Canvas(chat_area, bg=BG, highlightthickness=0, bd=0)
chat_scrollbar = tk.Scrollbar(chat_area, orient="vertical", command=chat_canvas.yview)
chat_canvas.configure(yscrollcommand=chat_scrollbar.set)
chat_canvas.grid(row=0, column=0, sticky="nsew")
chat_scrollbar.grid(row=0, column=1, sticky="ns")
messages_frame = tk.Frame(chat_canvas, bg=BG)
messages_window = chat_canvas.create_window((0, 0), window=messages_frame, anchor="nw")


def update_scroll_region(_event=None):
    chat_canvas.configure(scrollregion=chat_canvas.bbox("all"))
    chat_canvas.itemconfigure(messages_window, width=chat_canvas.winfo_width())
    chat_canvas.yview_moveto(1.0)


messages_frame.bind("<Configure>", update_scroll_region)
chat_canvas.bind("<Configure>", update_scroll_region)


def clear_chat_messages():
    for widget in messages_frame.winfo_children():
        widget.destroy()



def display_message(sender, message):
    is_user = sender.lower() in ("you", "user")

    outer = tk.Frame(messages_frame, bg=BG)
    outer.pack(fill="x", padx=24, pady=(12, 4))

    holder = tk.Frame(outer, bg=BG)
    holder.pack(anchor="e" if is_user else "w")

    label_text = "YOU" if is_user else "MY AI"
    label_color = GREEN if is_user else MUTED

    tk.Label(
        holder,
        text=label_text,
        bg=BG,
        fg=label_color,
        font=("Segoe UI", 9, "bold"),
    ).pack(anchor="e" if is_user else "w", padx=5, pady=(0, 5))

    bubble_color = USER_BG if is_user else AI_BG
    wrap_length = max(280, min(600, root.winfo_width() - 420))

    text_label = tk.Label(
        holder,
        text=message,
        bg=bubble_color,
        fg=TEXT,
        font=("Segoe UI", 11),
        justify="left",
        anchor="w",
        wraplength=wrap_length,
        padx=16,
        pady=12,
    )

    text_label.pack(anchor="e" if is_user else "w")

    root.after_idle(update_scroll_region)



# Input area

# Input area - modern dark style
input_outer = tk.Frame(
    main,
    bg=BORDER,
    highlightthickness=0,
)
input_outer.grid(
    row=2,
    column=0,
    sticky="ew",
    padx=24,
    pady=(12, 20),
)
input_outer.grid_columnconfigure(0, weight=1)

input_panel = tk.Frame(
    input_outer,
    bg=INPUT_BG,
)
input_panel.grid(
    row=0,
    column=0,
    sticky="ew",
    padx=1,
    pady=1,
)
input_panel.grid_columnconfigure(0, weight=1)

PLACEHOLDER = "Message your AI assistant..."

message_entry = tk.Entry(
    input_panel,
    bg=INPUT_BG,
    fg=MUTED,
    insertbackground=TEXT,
    font=("Segoe UI", 12),
    relief="flat",
    bd=0,
)
message_entry.grid(
    row=0,
    column=0,
    sticky="ew",
    padx=(18, 10),
    pady=16,
    ipady=5,
)
message_entry.insert(0, PLACEHOLDER)


def on_entry_focus_in(_event=None):
    if message_entry.get() == PLACEHOLDER:
        message_entry.delete(0, tk.END)
        message_entry.config(fg=TEXT)


def on_entry_focus_out(_event=None):
    if not message_entry.get().strip():
        message_entry.delete(0, tk.END)
        message_entry.insert(0, PLACEHOLDER)
        message_entry.config(fg=MUTED)


message_entry.bind("<FocusIn>", on_entry_focus_in)
message_entry.bind("<FocusOut>", on_entry_focus_out)


def on_send_enter(_event=None):
    if not busy:
        send_button.config(bg=GREEN_HOVER)


def on_send_leave(_event=None):
    if not busy:
        send_button.config(bg=GREEN)


# Create the button once.
send_button = tk.Button(
    input_panel,
    text="➤  Send",
    command=lambda: None,
    bg=GREEN,
    fg="#06281D",
    activebackground=GREEN_HOVER,
    activeforeground="#06281D",
    disabledforeground=MUTED,
    font=("Segoe UI", 10, "bold"),
    relief="flat",
    bd=0,
    cursor="hand2",
    padx=22,
    pady=11,
)
send_button.grid(
    row=0,
    column=1,
    padx=(4, 10),
    pady=8,
)
send_button.bind("<Enter>", on_send_enter)
send_button.bind("<Leave>", on_send_leave)


# ============================================================
# AI and command handling
# ============================================================
def handle_special_command(question):
    text = question.strip()
    lower = text.lower()

    # Add task: "add a task to call the supplier" or "add task: call supplier"
    task_prefixes = ("add a task to ", "add task to ", "add task: ", "add a task: ")
    for prefix in task_prefixes:
        if lower.startswith(prefix):
            task_text = text[len(prefix):].strip()
            if not task_text:
                return "Please tell me what task you want to add."
            task_id = add_task(task_text)
            return f"Task added successfully!\n\nTask ID: {task_id}\nTask: {task_text}"

    if lower in ("show my pending tasks", "show pending tasks", "list pending tasks"):
        return "Your pending tasks:\n\n" + format_tasks(get_tasks(pending_only=True))

    if lower in ("show all my tasks", "show all tasks", "list all tasks"):
        return "Here are all your tasks:\n\n" + format_tasks(get_tasks())

    if lower.startswith("complete task "):
        number_text = text[len("complete task "):].strip()
        if number_text.isdigit():
            task_id = int(number_text)
            if complete_task(task_id):
                return f"Task {task_id} marked as completed."
            return f"I couldn't find a task with ID {task_id}. Use 'show all tasks' to see task IDs."
        return "Please use this format: complete task 1"

    if lower.startswith("remember my name is "):
        name = text[len("remember my name is "):].strip()
        if name:
            save_memory("name", name)
            return f"I'll remember your name is {name}."
        return "Please tell me your name after 'remember my name is'."

    if lower in ("what is my name?", "what's my name?", "who am i?", "do you know my name?"):
        name = get_memory("name")
        return f"Your name is {name}." if name else "You haven't told me your name yet. You can say: remember my name is ..."

    if lower in ("show my memories", "what do you remember about me?", "show memory"):
        memories = get_all_memories()
        if not memories:
            return "I haven't saved any memories yet."
        return "Here is what I remember:\n\n" + "\n".join(f"• {key}: {value}" for key, value in memories)

    if lower.startswith("remember that "):
        note = text[len("remember that "):].strip()
        if note:
            # Save notes under unique keys so multiple memories can coexist.
            key = "note_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            save_memory(key, note)
            return f"Saved to memory: {note}"
        return "Tell me what you want me to remember."

    return None


def build_prompt(question):
    history_text = ""
    for item in conversation[-10:]:
        role = "User" if item["role"] == "user" else "Assistant"
        history_text += f"{role}: {item['content']}\n"

    memories = get_all_memories()
    memory_text = "\n".join(f"{key}: {value}" for key, value in memories)
    if not memory_text:
        memory_text = "No saved memories."

    return (
        "You are a helpful personal AI assistant. Answer clearly and naturally. "
        "If you are unsure, say so rather than inventing facts.\n\n"
        f"Saved user memory:\n{memory_text}\n\n"
        f"Recent conversation:\n{history_text}\n"
        f"User: {question}\nAssistant:"
    )


def ask_ai(question):
    try:
        special_answer = handle_special_command(question)
        if special_answer is not None:
            ui_queue.put(("answer", special_answer))
            return

        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": build_prompt(question), "stream": False},
            timeout=180,
        )
        response.raise_for_status()
        data = response.json()
        answer = data.get("response", "").strip()
        if not answer:
            answer = "I received an empty response from the AI model. Please try again."
        ui_queue.put(("answer", answer))
    except requests.exceptions.ConnectionError:
        ui_queue.put((
            "error",
            "I couldn't connect to Ollama. Please make sure Ollama is running and the model is installed.\n\n"
            f"Try this in Command Prompt: ollama run {OLLAMA_MODEL}",
        ))
    except requests.exceptions.Timeout:
        ui_queue.put(("error", "The AI took too long to respond. Please try again."))
    except requests.exceptions.RequestException as error:
        ui_queue.put(("error", f"There was a problem contacting Ollama:\n{error}"))
    except Exception as error:
        ui_queue.put(("error", f"Something went wrong:\n{error}"))


def send_message(event=None):
    global current_chat_id, conversation, busy
    if busy:
        return "break"

    question = message_entry.get().strip()
    if not question or question == PLACEHOLDER:
        return "break"

    message_entry.delete(0, tk.END)
    message_entry.config(fg=TEXT)

    if current_chat_id is None:
        current_chat_id = create_chat(question)

    conversation.append({"role": "user", "content": question})
    save_chat_message(current_chat_id, "user", question)
    display_message("You", question)
    refresh_chat_list(select_current=True)

    busy = True
    send_button.config(state="disabled", bg=BORDER, cursor="arrow")
    message_entry.config(state="disabled")
    status_label.config(text="Thinking…", fg=GREEN)
    threading.Thread(target=ask_ai, args=(question,), daemon=True).start()
    return "break"


# Connect handlers only after the function exists. This avoids NameError.
send_button.config(command=send_message)
message_entry.bind("<Return>", send_message)


def refresh_chat_list(select_current=False):
    global chat_ids
    chats = get_all_chats()
    chat_ids = [row[0] for row in chats]
    recent_listbox.delete(0, tk.END)
    selected_index = None
    for index, (chat_id, title) in enumerate(chats):
        recent_listbox.insert(tk.END, title)
        if chat_id == current_chat_id:
            selected_index = index
    if select_current and selected_index is not None:
        recent_listbox.selection_clear(0, tk.END)
        recent_listbox.selection_set(selected_index)
        recent_listbox.activate(selected_index)


def load_selected_chat():
    global current_chat_id, conversation
    if busy:
        return
    selection = recent_listbox.curselection()
    if not selection:
        return
    index = selection[0]
    if index >= len(chat_ids):
        return
    chat_id = chat_ids[index]
    current_chat_id = chat_id
    conversation = []
    clear_chat_messages()
    for role, content in load_chat_messages(chat_id):
        conversation.append({"role": role, "content": content})
        display_message("You" if role == "user" else "AI", content)
    status_label.config(text="Chat history loaded", fg=GREEN)


def process_ui_queue():
    global busy, conversation
    try:
        while True:
            kind, content = ui_queue.get_nowait()
            if kind == "answer":
                conversation.append({"role": "assistant", "content": content})
                if current_chat_id is not None:
                    save_chat_message(current_chat_id, "assistant", content)
                display_message("AI", content)
                status_label.config(text="Ready", fg=GREEN)
            else:
                display_message("AI", content)
                status_label.config(text="Connection issue", fg="#FBBF24")
            busy = False
            send_button.config(state="normal", bg=GREEN, cursor="hand2")
            message_entry.config(state="normal")
            message_entry.focus_set()
            refresh_chat_list(select_current=True)
    except queue.Empty:
        pass
    root.after(100, process_ui_queue)


def close_window():
    try:
        root.destroy()
    except tk.TclError:
        pass


# ============================================================
# Start the application
# ============================================================
setup_database()
refresh_chat_list()
display_message("AI", "Hello! I'm your personal AI assistant. How can I help you today?")
root.protocol("WM_DELETE_WINDOW", close_window)
root.after(100, process_ui_queue)
message_entry.focus_set()
root.mainloop()
