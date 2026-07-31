# Noted

Noted turns a casual spoken or typed note (e.g. "remind me I have to text Jerry later today about the meeting") into a structured task card — action, person, topic, time, and priority — that you can copy anywhere. No login, no database; everything lives in the browser tab for the session.

## Running locally

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set your Anthropic API key:
   ```
   set ANTHROPIC_API_KEY=your-key-here      # Windows cmd
   $env:ANTHROPIC_API_KEY="your-key-here"   # PowerShell
   ```
3. Start the backend:
   ```
   python main.py
   ```
   This runs the API at `http://127.0.0.1:8010`.
4. Open `index.html` directly in a browser (double-click it, or serve it with any static file server). Type or speak a note, hit **Extract Task**, and the structured card appears below.

Voice input uses the browser's built-in Web Speech API (Chrome/Edge support it best) — no extra setup needed.

## Note

This reuses the same voice-input and NLP-based command-parsing approach built for the Convo project (IPA Corporate), applied here to personal task capture instead of general assistant commands.
