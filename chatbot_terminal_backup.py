
import requests
import sqlite3

# Step 1: Connect to the memory database
conn = sqlite3.connect("chatbot_memory.db")
cursor = conn.cursor()

# Step 2: Create a table to store memories
cursor.execute("""
    CREATE TABLE IF NOT EXISTS memories (
        key TEXT PRIMARY KEY,
        value TEXT
    )
""")

conn.commit()

# Step 3: Function to save a memory
def save_memory(key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO memories (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()


# Step 4: Function to read a memory
def get_memory(key):
    cursor.execute(
        "SELECT value FROM memories WHERE key = ?",
        (key,)
    )
    result = cursor.fetchone()

    if result:
        return result[0]

    return None


# Step 5: Start the chatbot
conversation = []

print("Welcome to My AI Chatbot!")
print("Type 'exit' to stop.")
print("Tip: Type 'remember my name is Rajendran' to save your name.")

while True:
    question = input("\nYou: ").strip()
    lower_question = question.lower()

    if lower_question == "exit":
        print("AI: Goodbye! Your saved memories will remain.")
        break

    # Save the user's name
    if lower_question.startswith("remember my name is "):
        name = question[len("remember my name is "):].strip()

        if name:
            save_memory("name", name)
            print("AI: I'll remember your name:", name)
        else:
            print("AI: Please provide a name to remember.")

        continue

    # Recall the saved name
    if lower_question in ("what is my name?", "what is my name"):
        name = get_memory("name")

        if name:
            print("AI: Your name is", name)
        else:
            print("AI: You haven't asked me to remember your name yet.")

        continue

    # Add saved information to the AI's prompt
    saved_name = get_memory("name")

    prompt = (
        "You are a helpful personal AI assistant.\n"
        f"Saved user name: {saved_name or 'Not saved'}\n"
        "Use the saved name when relevant. Answer clearly.\n\n"
    )

    prompt += "\n".join(conversation)
    prompt += "\nYou: " + question + "\nAI:"

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": False
            },
            timeout=180
        )

        response.raise_for_status()
        answer = response.json()["response"].strip()

        print("\nAI:", answer)

        conversation.append("You: " + question)
        conversation.append("AI: " + answer)

    except requests.RequestException as error:
        print("AI: I couldn't connect to Ollama.")
        print("Check that Ollama is running.", error)

conn.close()
