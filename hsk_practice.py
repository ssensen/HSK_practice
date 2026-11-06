"""HSK 3 practice: pops up a question on a timer. Run with Start HSK Practice.bat."""
import ctypes
import math
import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import messagebox, ttk

import audio
import quiz
import sync
import tutor

CHINESE_FONT = "Microsoft YaHei UI"
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
# Chinese characters in the answer are shown larger than the English and pinyin around them.
ANSWER_CHINESE_FONT = (CHINESE_FONT, 18)
# Chinese characters and Chinese punctuation, such as ，。？ and full-width brackets.
CHINESE_RUN = re.compile(r"[　-〿一-鿿＀-￯]+")
# Extra pixels between wrapped lines and after each line, for the answer and chat text.
READING_SPACING = dict(spacing2=6, spacing3=10)

# Used to centre the window before it is first shown, when Windows can't measure the title bar.
TITLE_BAR_GUESS = 31
# Gap between the word and the example sentence when both play after answering.
SENTENCE_PAUSE_MS = 400
CLIP_LENGTH_GUESS_MS = 1500
WINDOW_TOP_MARGIN = 10
# Gap between the popup and the right edge of the screen, leaving room for the window border.
SCREEN_EDGE_MARGIN = 30
# How see-through the popup is in gaming mode (1.0 is solid).
GAMING_OPACITY = 0.75
MIN_CHAT_LINES = 4
BUTTON_STYLE = dict(bg=SURFACE, fg=TEXT, activebackground=HOVER, activeforeground=TEXT,
                    disabledforeground=MUTED, relief="flat", bd=0, highlightthickness=0,
                    padx=10, pady=3)
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
# Gaming mode keys. Games like Skyrim keep the mouse, so the number pad answers instead.
# These are Windows virtual-key codes, which need Num Lock on.
VK_NUMPAD0 = 0x60
ANSWER_KEYS = [VK_NUMPAD0 + number for number in range(1, 5)]  # Numpad 1 to 4
SNOOZE_OR_DONE_KEY = VK_NUMPAD0
REPLAY_KEY = VK_NUMPAD0 + 5
GAMING_KEYS_TEXT = "Number pad: 1–4 answer · 0 snooze / done · 5 replay audio"

user32 = ctypes.windll.user32


def dark_title_bar(win):
    hwnd = user32.GetParent(win.winfo_id())
    enabled = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(enabled), ctypes.sizeof(enabled))


def set_no_activate(win, enabled):
    """While on, clicking the window doesn't take focus from the app in front, like a game."""
    hwnd = user32.GetParent(win.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = style | WS_EX_NOACTIVATE if enabled else style & ~WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def set_opacity(win, opacity):
    """Tk's own -alpha attribute drops WS_EX_NOACTIVATE, so this sets it through Windows."""
    hwnd = user32.GetParent(win.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(hwnd, 0, round(opacity * 255), LWA_ALPHA)


class GlobalHotkeys:
    """Keys that reach this app even while a fullscreen game has focus, until stop()."""

    def __init__(self, keys):
        self.pressed = queue.Queue()
        self.failed = []
        self.thread_id = None
        ready = threading.Event()

        def run():
            # Hotkeys belong to the thread that registers them and arrive in its message queue.
            self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # Creates the queue.
            for key in keys:
                if not user32.RegisterHotKey(None, key, MOD_NOREPEAT, key):
                    self.failed.append(key)  # Another app already uses this key.
            ready.set()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    self.pressed.put(message.wParam)
            for key in keys:
                user32.UnregisterHotKey(None, key)

        threading.Thread(target=run, daemon=True).start()
        ready.wait()

    def stop(self):
        user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


MONITORINFOF_PRIMARY = 1
MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


def monitor_work_areas():
    """(left, top, right, bottom) of each monitor without its taskbar, main monitor first."""
    areas = []

    def found(monitor, _dc, _rect, _data):
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        work = info.rcWork
        area = (work.left, work.top, work.right, work.bottom)
        if info.dwFlags & MONITORINFOF_PRIMARY:
            areas.insert(0, area)
        else:
            areas.append(area)
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(found), 0)
    return areas


def popup_area(second_monitor):
    """Falls back to the main monitor when there's no second one plugged in."""
    areas = monitor_work_areas()
    return areas[1] if second_monitor and len(areas) > 1 else areas[0]


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
    if text_widget.tag_ranges("chinese"):
        # Height is in lines of the widget's font, so the larger Chinese text needs more of
        # them. Work out how many from the pixel height of the text.
        pixels = text_widget.count("1.0", "end", "ypixels")
        per_line = text_widget.winfo_reqheight() / int(text_widget.cget("height"))
        if pixels and per_line > 0:
            height = max(height, math.ceil(pixels[0] / per_line))
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
        self.hotkeys = None
        self.queued_audio = None
        gaming = app.settings["gaming_mode"]

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
        for number, option in enumerate(question.options, 1):
            label = option
            if question.option_pinyin:
                label = f"{option}   {question.option_pinyin[option]}"
            button = tk.Button(win, text=f"{number}   {label}" if gaming else label,
                               font=(CHINESE_FONT, 14), anchor="w",
                               justify="left", wraplength=500, takefocus=0, state="disabled",
                               command=lambda o=option: self.answer(o), **BUTTON_STYLE)
            button.bind("<Button-3>", lambda event, o=option: show_menu(
                event, [("Copy", lambda: copy_to_clipboard(event.widget, o))]))
            button.pack(fill="x", pady=2)
            self.buttons.append(button)
        if gaming:
            self.hotkeys = GlobalHotkeys(ANSWER_KEYS + [SNOOZE_OR_DONE_KEY, REPLAY_KEY])
            keys_text = (GAMING_KEYS_TEXT if not self.hotkeys.failed else
                         "Some number pad keys are in use by another app, so use the mouse.")
            readonly_label(win, keys_text, 10, MUTED).pack(anchor="w", pady=(4, 0))

        self.feedback = readonly_label(win, "", READING_FONT[1], TEXT)
        self.feedback.configure(**READING_SPACING)
        self.feedback.tag_configure("chinese", font=ANSWER_CHINESE_FONT)
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
        set_no_activate(win, True)
        if gaming:
            set_opacity(win, GAMING_OPACITY)
        self.area = popup_area(app.settings["second_monitor"])
        self.place()
        win.deiconify()
        win.update()
        # The text boxes only measure their real height once they are on screen.
        self.place()
        # Hand focus back so keystrokes in progress go to the work app, not this window.
        if previous_window:
            user32.SetForegroundWindow(previous_window)
        win.after(ANSWER_LOCK_MS, self.unlock)
        if self.hotkeys:
            self.poll_hotkeys()

    def poll_hotkeys(self):
        if not self.win.winfo_exists():
            return
        while not self.hotkeys.pressed.empty():
            self.on_hotkey(self.hotkeys.pressed.get())
        if self.win.winfo_exists():  # A key may have just closed it.
            self.win.after(50, self.poll_hotkeys)

    def on_hotkey(self, key):
        if key in ANSWER_KEYS:
            button = self.buttons[ANSWER_KEYS.index(key)]
            if str(button.cget("state")) == "normal":  # Unlocked and not yet answered.
                button.invoke()
        elif key == SNOOZE_OR_DONE_KEY:
            self.snooze() if self.chosen is None else self.finish()
        elif key == REPLAY_KEY and self.chosen is not None:  # Before answering it'd give it away.
            self.play_after_answer()

    def close(self):
        self.cancel_queued_audio()
        if self.hotkeys:
            self.hotkeys.stop()
        self.win.destroy()

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
        for button, text in zip(self.buttons, self.question.options):
            if text == self.question.answer:
                button.configure(bg=RIGHT_BG, disabledforeground=TEXT)
            elif text == option:
                button.configure(bg=WRONG_BG, disabledforeground=TEXT)
            button.configure(state="disabled")
        heading = "Correct!" if correct else f"Not quite. The answer is: {self.question.answer}"
        self.set_feedback(f"{heading}\n{self.question.explain}",
                          RIGHT_TEXT if correct else WRONG_TEXT)
        self.snooze_button.pack_forget()
        self.done_button.pack(side="right")
        for button in self.play_buttons:
            button.pack(side="left", padx=(6, 0))
        self.place()
        if self.app.settings["autoplay"]:
            self.play_after_answer()
        self.app.refresh()

    def set_feedback(self, text, fg=None):
        set_readonly_text(self.feedback, text, fg)
        for run in CHINESE_RUN.finditer(text):
            self.feedback.tag_add("chinese", f"1.0 + {run.start()} chars",
                                  f"1.0 + {run.end()} chars")
        fit_height(self.feedback)  # The larger characters can wrap onto more lines.

    def play_after_answer(self):
        """Sentence questions play the sentence. Word questions play the word, then the
        example sentence."""
        question = self.question
        if question.kind in quiz.SENTENCE_KINDS or not question.sentence_audio:
            self.play(question.main_audio)
            return
        self.play(question.word_audio)
        delay = (audio.length_ms() or CLIP_LENGTH_GUESS_MS) + SENTENCE_PAUSE_MS
        self.queued_audio = self.win.after(delay, lambda: self.play(question.sentence_audio))

    def cancel_queued_audio(self):
        if self.queued_audio:
            self.win.after_cancel(self.queued_audio)
            self.queued_audio = None

    def play(self, clip):
        self.cancel_queued_audio()  # Pressing Play stops the sentence that was coming next.
        if not audio.play(clip):
            self.set_feedback(
                self.feedback.get("1.0", "end-1c")
                + "\n(Could not play the audio. Check that "
                  "the .apkg deck is still in the app folder.)")

    def open_chat(self):
        self.ask_button.configure(state="disabled")
        self.chat.pack(fill="both", expand=True)
        self.place()
        # Typing needs keyboard focus, so from here on the window takes focus like a normal one.
        set_no_activate(self.win, False)
        self.entry.focus_force()

    def place(self):
        """Centre the window on the right edge of its monitor, so it covers less of a game or
        video in the middle. If it's too tall to fit above the taskbar, shorten the chat."""
        win = self.win
        win.update_idletasks()
        left, top, right, bottom = self.area
        title_bar = (win.winfo_rooty() - win.winfo_y()) or TITLE_BAR_GUESS
        excess = win.winfo_reqheight() + title_bar + 2 * WINDOW_TOP_MARGIN - (bottom - top)
        if excess > 0:
            line_height = tkfont.Font(font=READING_FONT).metrics("linespace") + 6
            lines = max(MIN_CHAT_LINES,
                        int(self.log.cget("height")) - (excess // line_height + 1))
            self.log.configure(height=lines)
            win.update_idletasks()
        height = win.winfo_reqheight() + title_bar
        y = top + max(WINDOW_TOP_MARGIN, (bottom - top - height) // 2)
        x = right - win.winfo_reqwidth() - SCREEN_EDGE_MARGIN
        win.geometry(f"+{x}+{y}")

    def write_log(self, text, tag=None):
        self.log.configure(state="normal")
        start = self.log.index("end-1c")
        self.log.insert("end", text + "\n\n", tag or ())
        self.log.configure(state="disabled")
        # Show the start of the new message, so a long answer reads from the top.
        self.log.update_idletasks()
        self.log.yview(start)

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
        self.close()
        self.app.popup_closed(self.app.settings["snooze_minutes"])

    def finish(self):
        audio.stop()
        self.close()
        self.app.popup_closed(self.app.settings["interval_minutes"])


class App:
    def __init__(self):
        self.root = root = tk.Tk()
        root.title("HSK Practice")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.iconify)  # Only Quit really exits.

        self.settings = quiz.load_settings()
        self.progress = quiz.Progress()
        self.bank = quiz.load_bank(self.settings["max_level"], self.settings["min_level"])
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
        self.min_level = tk.StringVar(value=str(self.settings["min_level"]))
        ttk.Label(frame, text="Include down to HSK").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Spinbox(frame, from_=1, to=5, width=6, textvariable=self.min_level).grid(
            row=5, column=1, sticky="w", padx=6)
        ttk.Button(frame, text="Save", command=self.apply_settings).grid(
            row=2, column=2, rowspan=4, sticky="ns")

        self.autoplay = tk.BooleanVar(value=self.settings["autoplay"])
        ttk.Checkbutton(frame, text="Play pronunciation after answering (word, then sentence)",
                        variable=self.autoplay, command=self.apply_autoplay).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.gaming_mode = tk.BooleanVar(value=self.settings["gaming_mode"])
        ttk.Checkbutton(frame, text="Gaming mode (see-through popup, answer with the number pad)",
                        variable=self.gaming_mode, command=self.apply_gaming_mode).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.second_monitor = tk.BooleanVar(value=self.settings["second_monitor"])
        ttk.Checkbutton(frame, text="Show the popup on my second monitor",
                        variable=self.second_monitor, command=self.apply_second_monitor).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(2, 10))

        ttk.Button(frame, text="Question now", command=self.show_question).grid(
            row=9, column=0, sticky="ew")
        self.pause_button = ttk.Button(frame, text="Pause", command=self.toggle_pause)
        self.pause_button.grid(row=9, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Quit", command=self.quit).grid(row=9, column=2, sticky="ew")

        self.refresh()
        self.tick()

    def apply_settings(self):
        try:
            interval = int(self.interval.get())
            snooze = int(self.snooze.get())
            max_level = int(self.max_level.get())
            min_level = int(self.min_level.get())
            if interval < 1 or snooze < 1 or not 1 <= min_level <= max_level <= 5:
                raise ValueError
        except ValueError:
            messagebox.showerror("HSK Practice", "Please enter whole minutes of 1 or more, "
                                                 "and HSK levels from 1 to 5, with the "
                                                 "\"down to\" level no higher than \"up to\".")
            return
        self.settings.update(interval_minutes=interval, snooze_minutes=snooze,
                             min_level=min_level, max_level=max_level)
        quiz.save_settings(self.settings)
        self.bank = quiz.load_bank(max_level, min_level)
        self.next_at = time.time() + interval * 60
        self.refresh()

    def apply_autoplay(self):
        self.settings["autoplay"] = self.autoplay.get()
        quiz.save_settings(self.settings)

    def apply_gaming_mode(self):
        self.settings["gaming_mode"] = self.gaming_mode.get()
        quiz.save_settings(self.settings)

    def apply_second_monitor(self):
        self.settings["second_monitor"] = self.second_monitor.get()
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

    def quit(self):
        self.status.configure(text="Saving to GitHub…")
        self.root.update()
        try:
            sync.push_changes()
        except RuntimeError as error:
            messagebox.showwarning("HSK Practice", "Couldn't save to GitHub, so the other PC "
                                                   f"won't get these changes yet.\n\n{error}")
        self.root.destroy()

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
