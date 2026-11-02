"""HSK 3 practice: pops up a question on a timer. Run with Start HSK Practice.bat."""
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import winreg
from pathlib import Path
from tkinter import messagebox, ttk

import audio
import quiz
import tutor

CHINESE_FONT = "Microsoft YaHei UI"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "HSK Practice"
MUTEX_NAME = "HSKPracticeSingleInstance"
ERROR_ALREADY_EXISTS = 183
# Stops a click meant for the window underneath from answering the question.
ANSWER_LOCK_MS = 800

BG = "#1e1f22"
SURFACE = "#2f3136"
HOVER = "#3c3f45"
TEXT = "#e8e8e8"
MUTED = "#8a8d93"
RIGHT_BG = "#1f5c34"
WRONG_BG = "#7a2626"
RIGHT_TEXT = "#6fd08c"
WRONG_TEXT = "#f28b82"
YOU_TEXT = "#7db7ff"
SELECTION = "#3a6ea5"
TEXT_WIDTH_PX = 520
READING_FONT = (CHINESE_FONT, 14)
# Extra pixels between wrapped lines and after each line, for the answer and chat text.
READING_SPACING = dict(spacing2=6, spacing3=10)
TASKBAR_ROOM = 60
WINDOW_TOP_MARGIN = 10
MIN_CHAT_LINES = 4
BUTTON_STYLE = dict(bg=SURFACE, fg=TEXT, activebackground=HOVER, activeforeground=TEXT,
                    disabledforeground=MUTED, relief="flat", bd=0, highlightthickness=0,
                    padx=10, pady=3)
DWMWA_USE_IMMERSIVE_DARK_MODE = 20

user32 = ctypes.windll.user32


def dark_title_bar(win):
    hwnd = user32.GetParent(win.winfo_id())
    enabled = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(enabled), ctypes.sizeof(enabled))


def startup_command():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve()}"'


def startup_enabled(value_name=RUN_VALUE):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, value_name)
        return True
    except OSError:
        return False


def set_startup(enabled, value_name=RUN_VALUE):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, startup_command())
        elif startup_enabled(value_name):
            winreg.DeleteValue(key, value_name)


def show_menu(event, actions):
    """Open a right-click menu. actions is a list of (label, function)."""
    menu = tk.Menu(event.widget, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=HOVER,
                   activeforeground=TEXT)
    for label, action in actions:
        menu.add_command(label=label, command=action)
    menu.tk_popup(event.x_root, event.y_root)


def copy_to_clipboard(widget, text):
    widget.clipboard_clear()
    widget.clipboard_append(text)


def make_copyable(text_widget):
    """Let a read-only Text take focus, since Ctrl+C only reaches the focused widget."""
    text_widget.configure(selectbackground=SELECTION, selectforeground="white",
                          inactiveselectbackground=SELECTION, cursor="xterm")
    text_widget.bind("<Button-1>", lambda _event: text_widget.focus_set(), add="+")
    text_widget.bind("<Button-3>", lambda event: show_menu(
        event, [("Copy", lambda: text_widget.event_generate("<<Copy>>"))]))


def fit_height(text_widget, _event=None):
    lines = text_widget.count("1.0", "end", "displaylines")
    height = lines[0] if lines else 1
    if height != int(text_widget.cget("height")):
        text_widget.configure(height=height)


def readonly_label(parent, text, size, fg):
    """Looks like a label, but its text can be selected and copied."""
    font = (CHINESE_FONT, size)
    widget = tk.Text(parent, font=font, fg=fg, bg=BG, relief="flat", bd=0, padx=0, pady=0,
                     highlightthickness=0, wrap="word", height=1,
                     width=TEXT_WIDTH_PX // tkfont.Font(font=font).measure("0"))
    make_copyable(widget)
    widget.bind("<Configure>", lambda event: fit_height(widget, event))
    set_readonly_text(widget, text)
    return widget


def set_readonly_text(widget, text, fg=None):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled", **({"fg": fg} if fg else {}))
    widget.update_idletasks()
    fit_height(widget)


class QuestionPopup:
    """Always-on-top question window. It can't be closed until answered or snoozed."""

    def __init__(self, app, question):
        self.app = app
        self.question = question
        self.chosen = None
        self.history = []
        self.replies = queue.Queue()

        previous_window = user32.GetForegroundWindow()
        self.win = win = tk.Toplevel(app.root)
        win.withdraw()
        win.title("HSK 3 Practice")
        win.attributes("-topmost", True, "-toolwindow", True)
        win.protocol("WM_DELETE_WINDOW", self.on_close_attempt)
        win.configure(padx=18, pady=14, bg=BG)

        readonly_label(win, question.prompt, 18, TEXT).pack(anchor="w")
        if question.hint:
            readonly_label(win, question.hint, 11, MUTED).pack(anchor="w")
        tk.Frame(win, height=10, bg=BG).pack()

        self.buttons = []
        for option in question.options:
            button = tk.Button(win, text=option, font=(CHINESE_FONT, 13), anchor="w",
                               justify="left", wraplength=500, takefocus=0, state="disabled",
                               command=lambda o=option: self.answer(o), **BUTTON_STYLE)
            button.bind("<Button-3>", lambda event, o=option: show_menu(
                event, [("Copy", lambda: copy_to_clipboard(event.widget, o))]))
            button.pack(fill="x", pady=2)
            self.buttons.append(button)

        self.feedback = readonly_label(win, "", READING_FONT[1], TEXT)
        self.feedback.configure(**READING_SPACING)
        self.feedback.pack(anchor="w", pady=(8, 0))

        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self.ask_button = tk.Button(row, text="Ask Claude", takefocus=0, command=self.open_chat,
                                    **BUTTON_STYLE)
        self.ask_button.pack(side="left")
        self.snooze_button = tk.Button(
            row, text=f"Snooze {app.settings['snooze_minutes']} min", takefocus=0,
            command=self.snooze, **BUTTON_STYLE)
        self.snooze_button.pack(side="left", padx=6)
        self.done_button = tk.Button(row, text="Done", takefocus=0, command=self.finish,
                                     **BUTTON_STYLE)
        self.play_buttons = []
        for label, clip in (("Play word", question.word_audio),
                            ("Play sentence", question.sentence_audio)):
            if clip:
                self.play_buttons.append(tk.Button(
                    row, text=label, takefocus=0, command=lambda c=clip: self.play(c),
                    **BUTTON_STYLE))

        self.chat = tk.Frame(win, bg=BG)
        self.log = tk.Text(self.chat, height=10, width=48, wrap="word", state="disabled",
                           font=READING_FONT, bg=SURFACE, fg=TEXT, relief="flat",
                           highlightthickness=0, selectbackground=HOVER, padx=10, pady=8,
                           **READING_SPACING)
        make_copyable(self.log)
        self.log.tag_configure("you", foreground=YOU_TEXT)
        self.log.pack(fill="both", expand=True, pady=(10, 4))
        entry_row = tk.Frame(self.chat, bg=BG)
        entry_row.pack(fill="x")
        self.entry = tk.Entry(entry_row, font=READING_FONT, bg=SURFACE, fg=TEXT,
                              insertbackground=TEXT, relief="flat", highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda _event: self.send())
        self.entry.bind("<Button-3>", lambda event: show_menu(event, [
            ("Cut", lambda: self.entry.event_generate("<<Cut>>")),
            ("Copy", lambda: self.entry.event_generate("<<Copy>>")),
            ("Paste", lambda: self.entry.event_generate("<<Paste>>"))]))
        self.send_button = tk.Button(entry_row, text="Send", command=self.send, **BUTTON_STYLE)
        self.send_button.pack(side="left", padx=(6, 0))

        win.update_idletasks()
        dark_title_bar(win)
        x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        y = max(40, (win.winfo_screenheight() - win.winfo_reqheight()) // 3)
        win.geometry(f"+{x}+{y}")
        win.deiconify()
        win.update()
        # Hand focus back so keystrokes in progress go to the work app, not this window.
        if previous_window:
            user32.SetForegroundWindow(previous_window)
        win.after(ANSWER_LOCK_MS, self.unlock)

    def unlock(self):
        if not self.win.winfo_exists():  # Snoozed before the lock ran out.
            return
        for button in self.buttons:
            button.configure(state="normal")

    def on_close_attempt(self):
        if self.chosen is not None:
            self.finish()
        else:
            self.win.bell()

    def answer(self, option):
        self.chosen = option
        correct = option == self.question.answer
        self.app.progress.record(self.question.id, correct)
        for button in self.buttons:
            text = button.cget("text")
            if text == self.question.answer:
                button.configure(bg=RIGHT_BG, disabledforeground=TEXT)
            elif text == option:
                button.configure(bg=WRONG_BG, disabledforeground=TEXT)
            button.configure(state="disabled")
        heading = "Correct!" if correct else f"Not quite. The answer is: {self.question.answer}"
        set_readonly_text(self.feedback, f"{heading}\n{self.question.explain}",
                          RIGHT_TEXT if correct else WRONG_TEXT)
        self.snooze_button.pack_forget()
        self.done_button.pack(side="right")
        for button in self.play_buttons:
            button.pack(side="left", padx=(6, 0))
        self.keep_on_screen()
        if self.app.settings["autoplay"]:
            self.play(self.question.main_audio)
        self.app.refresh()

    def play(self, clip):
        if not audio.play(clip):
            set_readonly_text(
                self.feedback, self.feedback.get("1.0", "end-1c")
                + "\n(Could not play the audio. Check that "
                  "the .apkg deck is still in the app folder.)")

    def open_chat(self):
        self.ask_button.configure(state="disabled")
        self.chat.pack(fill="both", expand=True)
        self.keep_on_screen()
        self.entry.focus_force()

    def keep_on_screen(self):
        """Move the window up, then shorten the chat if it still doesn't fit above the taskbar."""
        win = self.win
        win.update_idletasks()
        lowest_bottom = win.winfo_screenheight() - TASKBAR_ROOM
        title_bar = win.winfo_rooty() - win.winfo_y()
        excess = win.winfo_reqheight() + title_bar + WINDOW_TOP_MARGIN - lowest_bottom
        if excess > 0:
            line_height = tkfont.Font(font=READING_FONT).metrics("linespace") + 6
            lines = max(MIN_CHAT_LINES,
                        int(self.log.cget("height")) - (excess // line_height + 1))
            self.log.configure(height=lines)
            win.update_idletasks()
        y = max(WINDOW_TOP_MARGIN,
                min(win.winfo_y(), lowest_bottom - win.winfo_reqheight() - title_bar))
        win.geometry(f"+{win.winfo_x()}+{y}")

    def write_log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n\n", tag or ())
        self.log.configure(state="disabled")
        self.log.see("end")

    def send(self):
        text = self.entry.get().strip()
        if not text or str(self.send_button.cget("state")) == "disabled":
            return
        self.entry.delete(0, "end")
        self.write_log(f"You: {text}", "you")
        self.history.append(("Learner", text))
        self.send_button.configure(state="disabled", text="Thinking…")
        history = list(self.history)
        chosen = self.chosen

        def work():
            try:
                self.replies.put((True, tutor.ask(self.question, chosen, history)))
            except Exception as error:
                self.replies.put((False, str(error)))

        threading.Thread(target=work, daemon=True).start()
        self.poll_reply()

    def poll_reply(self):
        if not self.win.winfo_exists():
            return
        try:
            ok, text = self.replies.get_nowait()
        except queue.Empty:
            self.win.after(150, self.poll_reply)
            return
        if ok:
            self.history.append(("Tutor", text))
            self.write_log(f"Claude: {text}")
        else:
            self.history.pop()
            self.write_log(text)
        self.send_button.configure(state="normal", text="Send")

    def snooze(self):
        self.win.destroy()
        self.app.popup_closed(self.app.settings["snooze_minutes"])

    def finish(self):
        audio.stop()
        self.win.destroy()
        self.app.popup_closed(self.app.settings["interval_minutes"])


class App:
    def __init__(self):
        self.root = root = tk.Tk()
        root.title("HSK Practice")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.iconify)  # Only Quit really exits.

        self.settings = quiz.load_settings()
        self.progress = quiz.Progress()
        self.bank = quiz.load_bank(self.settings["max_level"])
        self.popup = None
        self.paused = False
        self.next_at = time.time() + self.settings["interval_minutes"] * 60

        frame = ttk.Frame(root, padding=14)
        frame.pack()
        self.status = ttk.Label(frame, font=("Segoe UI", 11, "bold"))
        self.status.grid(row=0, column=0, columnspan=3, sticky="w")
        self.stats = ttk.Label(frame, justify="left")
        self.stats.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        self.interval = tk.StringVar(value=str(self.settings["interval_minutes"]))
        self.snooze = tk.StringVar(value=str(self.settings["snooze_minutes"]))
        ttk.Label(frame, text="Ask every (minutes)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(frame, from_=1, to=1440, width=6, textvariable=self.interval).grid(
            row=2, column=1, sticky="w", padx=6)
        ttk.Label(frame, text="Snooze for (minutes)").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(frame, from_=1, to=240, width=6, textvariable=self.snooze).grid(
            row=3, column=1, sticky="w", padx=6)
        self.max_level = tk.StringVar(value=str(self.settings["max_level"]))
        ttk.Label(frame, text="Include up to HSK").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(frame, from_=1, to=5, width=6, textvariable=self.max_level).grid(
            row=4, column=1, sticky="w", padx=6)
        ttk.Button(frame, text="Save", command=self.apply_settings).grid(
            row=2, column=2, rowspan=3, sticky="ns")

        self.autoplay = tk.BooleanVar(value=self.settings["autoplay"])
        ttk.Checkbutton(frame, text="Play pronunciation after answering", variable=self.autoplay,
                        command=self.apply_autoplay).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.startup = tk.BooleanVar(value=startup_enabled())
        ttk.Checkbutton(frame, text="Start with Windows", variable=self.startup,
                        command=lambda: set_startup(self.startup.get())).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 10))

        ttk.Button(frame, text="Question now", command=self.show_question).grid(
            row=7, column=0, sticky="ew")
        self.pause_button = ttk.Button(frame, text="Pause", command=self.toggle_pause)
        self.pause_button.grid(row=7, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Quit", command=root.destroy).grid(row=7, column=2, sticky="ew")

        self.refresh()
        self.tick()

    def apply_settings(self):
        try:
            interval = int(self.interval.get())
            snooze = int(self.snooze.get())
            max_level = int(self.max_level.get())
            if interval < 1 or snooze < 1 or not 1 <= max_level <= 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("HSK Practice", "Please enter whole minutes of 1 or more, "
                                                 "and an HSK level from 1 to 5.")
            return
        self.settings.update(interval_minutes=interval, snooze_minutes=snooze,
                             max_level=max_level)
        quiz.save_settings(self.settings)
        self.bank = quiz.load_bank(max_level)
        self.next_at = time.time() + interval * 60
        self.refresh()

    def apply_autoplay(self):
        self.settings["autoplay"] = self.autoplay.get()
        quiz.save_settings(self.settings)

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_button.configure(text="Resume" if self.paused else "Pause")
        if not self.paused:
            self.next_at = time.time() + self.settings["interval_minutes"] * 60

    def show_question(self):
        if self.popup:
            self.popup.win.lift()
            return
        question_id = self.progress.choose(self.bank)
        self.popup = QuestionPopup(self, self.bank[question_id]())

    def popup_closed(self, minutes_until_next):
        self.popup = None
        self.next_at = time.time() + minutes_until_next * 60
        self.refresh()

    def refresh(self):
        stats = self.progress.stats(self.bank)
        self.stats.configure(text=(
            f"Today: {stats['today']} questions   Accuracy: {stats['accuracy']}%\n"
            f"Remembered: {stats['learned']} of {stats['total']}"))

    def tick(self):
        if not self.paused and not self.popup and time.time() >= self.next_at:
            self.show_question()
        if self.popup:
            self.status.configure(text="Question waiting for you")
        elif self.paused:
            self.status.configure(text="Paused")
        else:
            minutes, seconds = divmod(max(0, int(self.next_at - time.time())), 60)
            self.status.configure(text=f"Next question in {minutes}:{seconds:02d}")
        self.root.after(1000, self.tick)

    def run(self):
        self.root.mainloop()


def main():
    # The handle must stay open for as long as the app runs.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("HSK Practice", "HSK Practice is already running. "
                                            "Look for it on the taskbar.")
        return
    App().run()
    kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    main()
