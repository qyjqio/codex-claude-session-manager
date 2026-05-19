#!/usr/bin/env python3
import datetime as _dt
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk


CODEX_SESSIONS_ROOT = pathlib.Path.home() / ".codex" / "sessions"
CLAUDE_PROJECTS_ROOT = pathlib.Path.home() / ".claude" / "projects"
NOTES_FILE = pathlib.Path.home() / ".chat_session_notes.json"
DELETED_ROOT = pathlib.Path.home() / ".chat_session_deleted"
API_PROMO_URL = "https://api.qyjqio.com/"


def redact(text: str) -> str:
    if not text:
        return ""
    text = re.sub(
        r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        text,
        flags=re.S,
    )
    text = re.sub(r"\b(ghp_|github_pat_|sk-)[A-Za-z0-9_\-]+", "[REDACTED_TOKEN]", text)
    text = re.sub(
        r"(?i)(password|pass|token|secret|api key|apikey)(\s*[:=：]\s*)([^\s,;，；`|]+)",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(
        r"(密码|口令|令牌|密钥|私钥)(\s*[:=：]\s*)([^\s,;，；`|]+)",
        r"\1\2[REDACTED]",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    return f"{max(1, round(size / 1024))}KB"


def session_key(item: dict) -> str:
    return f"{item.get('source', '')}:{item.get('session_id', '')}"


def load_notes() -> dict:
    if not NOTES_FILE.exists():
        return {}
    try:
        with NOTES_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_notes(notes: dict) -> None:
    NOTES_FILE.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def unique_path(path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(1, 10000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"无法生成唯一路径：{path}")


def move_to_deleted(path: pathlib.Path, source: str) -> pathlib.Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = DELETED_ROOT / source / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(target_dir / path.name)
    shutil.move(str(path), str(target))
    return target


def first_text_from_claude_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text") or "")
                elif item.get("type") == "tool_result":
                    continue
        return " ".join(part for part in parts if part.strip())
    return ""


def base_session(path: pathlib.Path, source: str, session_id: str, title: str, cwd: str = "", created: str = "") -> dict:
    stat = path.stat()
    return {
        "source": source,
        "mtime": stat.st_mtime,
        "time": _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "session_id": session_id,
        "size": human_size(stat.st_size),
        "bytes": stat.st_size,
        "title": redact(title)[:180] or "未找到用户正文",
        "path": str(path),
        "cwd": cwd,
        "created": created,
    }


def parse_codex_session(path: pathlib.Path) -> dict:
    session_id = ""
    cwd = ""
    created = ""
    title = ""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                payload = obj.get("payload") or {}
                if obj.get("type") == "session_meta":
                    session_id = payload.get("id") or session_id
                    cwd = payload.get("cwd") or cwd
                    created = payload.get("timestamp") or created
                    continue

                if obj.get("type") != "response_item":
                    continue
                if payload.get("type") != "message" or payload.get("role") != "user":
                    continue

                parts = []
                for item in payload.get("content") or []:
                    if item.get("type") != "input_text":
                        continue
                    text = item.get("text") or ""
                    if text.startswith("<environment_context>"):
                        continue
                    if text.strip():
                        parts.append(text.strip())
                if parts:
                    title = " ".join(parts)
                    break
    except OSError:
        pass

    if not session_id:
        match = re.search(r"rollout-[0-9T\-]+-([0-9a-f\-]{36})\.jsonl$", path.name)
        if match:
            session_id = match.group(1)

    return base_session(path, "Codex", session_id, title, cwd, created)


def parse_claude_session(path: pathlib.Path) -> dict:
    session_id = path.stem
    cwd = ""
    created = ""
    title = ""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                session_id = obj.get("sessionId") or session_id
                cwd = obj.get("cwd") or cwd
                created = obj.get("timestamp") or created

                if obj.get("isSidechain"):
                    continue
                if obj.get("type") != "user":
                    continue

                message = obj.get("message") or {}
                if isinstance(message, dict):
                    text = first_text_from_claude_content(message.get("content"))
                else:
                    text = str(message)
                if text.strip():
                    title = text
                    break
    except OSError:
        pass

    return base_session(path, "Claude", session_id, title, cwd, created)


def load_sessions(include_claude: bool = True) -> list[dict]:
    sessions = []
    if CODEX_SESSIONS_ROOT.exists():
        for path in CODEX_SESSIONS_ROOT.rglob("rollout-*.jsonl"):
            try:
                sessions.append(parse_codex_session(path))
            except OSError:
                continue
    if include_claude and CLAUDE_PROJECTS_ROOT.exists():
        for path in CLAUDE_PROJECTS_ROOT.rglob("*.jsonl"):
            if "/subagents/" in str(path):
                continue
            try:
                sessions.append(parse_claude_session(path))
            except OSError:
                continue
    sessions.sort(key=lambda item: item["mtime"], reverse=True)
    return sessions


def find_terminal() -> str | None:
    for name in (
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "mate-terminal",
        "x-terminal-emulator",
        "xterm",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


def resume_command(item: dict, prompt: str = "") -> str:
    session_id = item.get("session_id", "")
    source = item.get("source", "Codex")
    cwd = item.get("cwd") or ""
    if source == "Claude":
        command = f"claude --resume {shlex.quote(session_id)}"
    else:
        command = f"codex resume {shlex.quote(session_id)}"

    if prompt.strip():
        command += " " + shlex.quote(prompt.strip())
    if cwd and pathlib.Path(cwd).exists():
        command = f"cd {shlex.quote(cwd)} && {command}"
    return command


def launch_resume(item: dict, prompt: str = "") -> None:
    if not item.get("session_id"):
        messagebox.showerror("无法恢复", "这个记录没有解析到 Session ID。")
        return

    command = resume_command(item, prompt)
    shell_cmd = f"{command}; echo; echo '对话已结束，按 Enter 关闭窗口'; read"

    terminal = find_terminal()
    if not terminal:
        messagebox.showerror("找不到终端", f"没有找到可用终端。请手动执行：\n{command}")
        return

    base = os.path.basename(terminal)
    if base in {"gnome-terminal", "mate-terminal", "x-terminal-emulator"}:
        args = [terminal, "--", "bash", "-lc", shell_cmd]
    elif base == "konsole":
        args = [terminal, "-e", "bash", "-lc", shell_cmd]
    elif base == "xfce4-terminal":
        args = [terminal, "--command", f"bash -lc {subprocess.list2cmdline([shell_cmd])}"]
    else:
        args = [terminal, "-e", "bash", "-lc", shell_cmd]

    try:
        subprocess.Popen(args)
    except OSError as exc:
        messagebox.showerror("打开失败", f"无法打开终端：{exc}\n\n可手动执行：\n{command}")


def xdg_open(path: str) -> None:
    opener = shutil.which("xdg-open")
    if not opener:
        messagebox.showerror("打开失败", f"没有找到 xdg-open：\n{path}")
        return
    try:
        subprocess.Popen([opener, path])
    except OSError as exc:
        messagebox.showerror("打开失败", str(exc))


def open_api_service() -> None:
    opener = shutil.which("xdg-open")
    if not opener:
        messagebox.showerror("打开失败", f"没有找到 xdg-open：\n{API_PROMO_URL}")
        return
    try:
        subprocess.Popen([opener, API_PROMO_URL])
    except OSError as exc:
        messagebox.showerror("打开失败", str(exc))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Codex / Claude 聊天记录恢复工具")
        self.geometry("1220x780")
        self.minsize(860, 560)
        self.configure(bg="#f6f7fb")

        self.sessions: list[dict] = []
        self.filtered: list[dict] = []
        self.notes: dict = load_notes()

        self.search_var = tk.StringVar()
        self.prompt_var = tk.StringVar(value="")
        self.source_filter_var = tk.StringVar(value="Codex")
        self.sort_column = "time"
        self.sort_reverse = True
        self.status_var = tk.StringVar()
        self.detail_title_var = tk.StringVar(value="未选择会话")
        self.detail_meta_var = tk.StringVar(value="从左侧列表选择一条记录")
        self.detail_id_var = tk.StringVar(value="-")
        self.detail_cwd_var = tk.StringVar(value="-")
        self.detail_path_var = tk.StringVar(value="-")
        self.detail_command_var = tk.StringVar(value="-")
        self.detail_note_var = tk.StringVar(value="")

        self.build_ui()
        self.refresh()

    def build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Noto Sans CJK SC", 10), background="#f6f7fb", foreground="#1f2937")
        style.configure("App.TFrame", background="#f6f7fb")
        style.configure("App.TLabel", background="#f6f7fb", foreground="#1f2937")
        style.configure("Panel.TFrame", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("Header.TFrame", background="#111827")
        style.configure("HeaderTitle.TLabel", background="#111827", foreground="#ffffff", font=("Noto Sans CJK SC", 16, "bold"))
        style.configure("HeaderSub.TLabel", background="#111827", foreground="#cbd5e1")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure("DetailTitle.TLabel", background="#ffffff", foreground="#111827", font=("Noto Sans CJK SC", 13, "bold"))
        style.configure("Detail.TLabel", background="#ffffff", foreground="#111827")
        style.configure("Treeview", rowheight=38, font=("Noto Sans CJK SC", 10), background="#ffffff", fieldbackground="#ffffff", foreground="#111827")
        style.configure("Treeview.Heading", font=("Noto Sans CJK SC", 10, "bold"), background="#eef2f7", foreground="#374151")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#111827")])
        style.configure("Accent.TButton", padding=(14, 8))
        style.configure("Tool.TButton", padding=(10, 7))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = ttk.Frame(self, style="Header.TFrame", padding=(18, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="聊天记录恢复", style="HeaderTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="分开浏览 Codex 和 Claude 会话，选择记录后可恢复、查看原始文件或打开所在目录",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        promo = ttk.Frame(self, style="App.TFrame", padding=(14, 10, 14, 0))
        promo.grid(row=1, column=0, sticky="ew")
        promo.columnconfigure(0, weight=1)
        ttk.Label(
            promo,
            text="API 中转服务：稳定接入多模型接口，支持开发调试和生产中转",
            style="App.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(promo, text="访问 api.qyjqio.com", style="Tool.TButton", command=open_api_service).grid(
            row=0, column=1, sticky="e"
        )

        toolbar = ttk.Frame(self, style="App.TFrame", padding=(14, 10, 14, 8))
        toolbar.grid(row=2, column=0, sticky="ew")
        toolbar.columnconfigure(3, weight=1)

        ttk.Label(toolbar, text="来源", style="App.TLabel").grid(row=0, column=0, sticky="w")
        source_filter = ttk.Combobox(
            toolbar,
            textvariable=self.source_filter_var,
            values=("Codex", "Claude", "全部"),
            width=9,
            state="readonly",
        )
        source_filter.grid(row=0, column=1, sticky="w", padx=(8, 18))
        source_filter.bind("<<ComboboxSelected>>", lambda _event: self.apply_filter())

        ttk.Label(toolbar, text="搜索", style="App.TLabel").grid(row=0, column=2, sticky="w")
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.grid(row=0, column=3, sticky="ew", padx=(8, 12))
        search.bind("<KeyRelease>", lambda _event: self.apply_filter())

        ttk.Button(toolbar, text="刷新", style="Tool.TButton", command=self.refresh).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(toolbar, text="删除选中", style="Tool.TButton", command=self.delete_selected).grid(row=0, column=5, padx=(0, 8))
        ttk.Button(toolbar, text="恢复选中", style="Accent.TButton", command=self.resume_selected).grid(row=0, column=6)

        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 12))

        list_panel = ttk.Frame(main, style="Panel.TFrame", padding=10)
        detail_panel = ttk.Frame(main, style="Panel.TFrame", padding=14)
        main.add(list_panel, weight=5)
        main.add(detail_panel, weight=1)

        list_panel.columnconfigure(0, weight=1)
        list_panel.rowconfigure(1, weight=1)
        ttk.Label(list_panel, text="会话列表", style="DetailTitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))

        columns = ("source", "time", "size", "note", "title", "session_id")
        self.tree = ttk.Treeview(list_panel, columns=columns, show="headings", selectmode="extended")
        self.heading_labels = {
            "source": "来源",
            "time": "时间",
            "size": "大小",
            "note": "备注",
            "title": "主题/首条用户消息",
            "session_id": "Session ID",
        }
        for column, label in self.heading_labels.items():
            self.tree.heading(column, text=label, command=lambda col=column: self.sort_by_column(col))
        self.tree.column("source", width=72, minwidth=64, anchor=tk.W, stretch=False)
        self.tree.column("time", width=135, minwidth=125, anchor=tk.W, stretch=False)
        self.tree.column("size", width=70, minwidth=64, anchor=tk.E, stretch=False)
        self.tree.column("note", width=220, minwidth=120, anchor=tk.W, stretch=False)
        self.tree.column("title", width=480, minwidth=260, anchor=tk.W, stretch=True)
        self.tree.column("session_id", width=285, minwidth=220, anchor=tk.W, stretch=False)
        self.tree.tag_configure("Codex", background="#ffffff")
        self.tree.tag_configure("Claude", background="#f8fafc")
        self.tree.bind("<Double-1>", lambda _event: self.resume_selected())
        self.tree.bind("<Return>", lambda _event: self.resume_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_detail())
        self.tree.grid(row=1, column=0, sticky="nsew")

        y_scrollbar = ttk.Scrollbar(list_panel, orient=tk.VERTICAL, command=self.tree.yview)
        y_scrollbar.grid(row=1, column=1, sticky="ns")
        x_scrollbar = ttk.Scrollbar(list_panel, orient=tk.HORIZONTAL, command=self.tree.xview)
        x_scrollbar.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

        detail_panel.columnconfigure(0, weight=1)
        detail_panel.rowconfigure(9, weight=1)
        ttk.Label(detail_panel, textvariable=self.detail_title_var, style="DetailTitle.TLabel", wraplength=280).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(detail_panel, textvariable=self.detail_meta_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="ew", pady=(6, 16)
        )

        self.add_detail_row(detail_panel, 2, "Session ID", self.detail_id_var)
        self.add_detail_row(detail_panel, 3, "工作目录", self.detail_cwd_var)
        self.add_detail_row(detail_panel, 4, "记录文件", self.detail_path_var)
        self.add_detail_row(detail_panel, 5, "恢复命令", self.detail_command_var)

        note_box = ttk.Frame(detail_panel, style="Panel.TFrame")
        note_box.grid(row=6, column=0, sticky="ew", pady=(14, 8))
        note_box.columnconfigure(0, weight=1)
        ttk.Label(note_box, text="备注", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        note_entry = ttk.Entry(note_box, textvariable=self.detail_note_var)
        note_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        note_entry.bind("<Return>", lambda _event: self.save_current_note())
        ttk.Button(note_box, text="保存备注", style="Tool.TButton", command=self.save_current_note).grid(
            row=2, column=0, sticky="ew", pady=(8, 0)
        )

        prompt_box = ttk.Frame(detail_panel, style="Panel.TFrame")
        prompt_box.grid(row=7, column=0, sticky="ew", pady=(8, 8))
        prompt_box.columnconfigure(0, weight=1)
        ttk.Label(prompt_box, text="恢复后追加提示", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        prompt_entry = ttk.Entry(prompt_box, textvariable=self.prompt_var)
        prompt_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        prompt_entry.bind("<KeyRelease>", lambda _event: self.update_detail())

        action_frame = ttk.Frame(detail_panel, style="Panel.TFrame")
        action_frame.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        action_frame.columnconfigure((0, 1), weight=1)
        ttk.Button(action_frame, text="恢复对话", style="Accent.TButton", command=self.resume_selected).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(action_frame, text="打开原始记录", style="Tool.TButton", command=self.open_file).grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )
        ttk.Button(action_frame, text="打开所在目录", style="Tool.TButton", command=self.open_folder).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Button(action_frame, text="删除选中聊天", style="Tool.TButton", command=self.delete_selected).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        bottom = ttk.Frame(self, style="App.TFrame", padding=(14, 0, 14, 12))
        bottom.grid(row=4, column=0, sticky="ew")
        ttk.Label(bottom, textvariable=self.status_var, style="App.TLabel").grid(row=0, column=0, sticky="w")

    def add_detail_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(0, weight=1)
        ttk.Label(box, text=label, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        text = tk.Text(
            box,
            height=2,
            wrap="word",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#e5e7eb",
            highlightcolor="#93c5fd",
            background="#ffffff",
            foreground="#111827",
            font=("Noto Sans CJK SC", 10),
            padx=6,
            pady=4,
        )
        text.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        def sync_text(*_args) -> None:
            text.configure(state="normal")
            text.delete("1.0", tk.END)
            text.insert("1.0", variable.get())
            text.configure(state="disabled")

        variable.trace_add("write", sync_text)
        sync_text()

    def sort_key(self, item: dict, column: str):
        if column == "time":
            return item.get("mtime", 0)
        if column == "size":
            return item.get("bytes", 0)
        if column == "note":
            return self.notes.get(session_key(item), "").lower()
        if column == "title":
            return item.get("title", "").lower()
        if column == "session_id":
            return item.get("session_id", "").lower()
        if column == "source":
            return item.get("source", "").lower()
        return ""

    def sort_by_column(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = column in {"time", "size"}
        self.apply_filter(select_first=True)

    def update_headings(self) -> None:
        for column, label in self.heading_labels.items():
            if column == self.sort_column:
                arrow = "↓" if self.sort_reverse else "↑"
                text = f"{label} {arrow}"
            else:
                text = label
            self.tree.heading(column, text=text, command=lambda col=column: self.sort_by_column(col))

    def refresh(self) -> None:
        self.sessions = load_sessions(include_claude=True)
        self.apply_filter()

    def apply_filter(self, select_first: bool = True) -> None:
        needle = self.search_var.get().strip().lower()
        source_filter = self.source_filter_var.get()
        self.filtered = []
        for item in self.sessions:
            if source_filter != "全部" and item["source"] != source_filter:
                continue
            if needle:
                haystack = " ".join(
                    [
                        item["source"],
                        item["time"],
                        self.notes.get(session_key(item), ""),
                        item["title"],
                        item["session_id"],
                        item["path"],
                        item.get("cwd", ""),
                    ]
                ).lower()
                if needle not in haystack:
                    continue
            self.filtered.append(item)

        self.filtered.sort(key=lambda item: self.sort_key(item, self.sort_column), reverse=self.sort_reverse)
        self.update_headings()

        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.filtered):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                tags=(item["source"],),
                values=(
                    item["source"],
                    item["time"],
                    item["size"],
                    self.notes.get(session_key(item), ""),
                    item["title"],
                    item["session_id"],
                ),
            )
        counts = {}
        for item in self.sessions:
            counts[item["source"]] = counts.get(item["source"], 0) + 1
        count_text = "，".join(f"{source} {count}" for source, count in sorted(counts.items()))
        self.status_var.set(
            f"共 {len(self.sessions)} 个会话（{count_text}），当前显示 {len(self.filtered)} 个。双击记录可恢复对话。"
        )
        if self.filtered and select_first:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self.update_detail()
        elif not self.filtered:
            self.clear_detail()

    def clear_detail(self) -> None:
        self.detail_title_var.set("未找到匹配会话")
        self.detail_meta_var.set("调整来源或搜索条件后再试")
        self.detail_id_var.set("-")
        self.detail_cwd_var.set("-")
        self.detail_path_var.set("-")
        self.detail_command_var.set("-")
        self.detail_note_var.set("")

    def update_detail(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.clear_detail()
            return
        item = self.filtered[int(selection[0])]
        self.detail_title_var.set(item["title"])
        self.detail_meta_var.set(f"{item['source']} · {item['time']} · {item['size']}")
        self.detail_id_var.set(item["session_id"] or "-")
        self.detail_cwd_var.set(item.get("cwd") or "-")
        self.detail_path_var.set(item["path"])
        self.detail_command_var.set(resume_command(item, self.prompt_var.get()))
        self.detail_note_var.set(self.notes.get(session_key(item), ""))

    def save_current_note(self) -> None:
        item = self.selected_item()
        if not item:
            return
        key = session_key(item)
        note = self.detail_note_var.get().strip()
        if note:
            self.notes[key] = note
        else:
            self.notes.pop(key, None)
        try:
            save_notes(self.notes)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存备注：{exc}")
            return

        self.apply_filter(select_first=False)
        self.status_var.set(f"备注已保存到 {NOTES_FILE}")

    def selected_items(self) -> list[dict]:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("未选择", "请先选择一条聊天记录。")
            return []
        return [self.filtered[int(item_id)] for item_id in selection]

    def selected_item(self) -> dict | None:
        items = self.selected_items()
        return items[0] if items else None

    def resume_selected(self) -> None:
        item = self.selected_item()
        if item:
            launch_resume(item, self.prompt_var.get())

    def open_file(self) -> None:
        item = self.selected_item()
        if item:
            xdg_open(item["path"])

    def open_folder(self) -> None:
        item = self.selected_item()
        if item:
            xdg_open(str(pathlib.Path(item["path"]).parent))

    def delete_selected(self) -> None:
        items = self.selected_items()
        if not items:
            return

        count = len(items)
        preview_lines = []
        for item in items[:8]:
            preview_lines.append(f"- {item.get('source', 'Unknown')} | {item.get('time', '')} | {item.get('title', '')}")
        if count > 8:
            preview_lines.append(f"... 还有 {count - 8} 条")
        preview = "\n".join(preview_lines)

        ok = messagebox.askyesno(
            "确认批量删除聊天记录",
            "将把选中的聊天移动到本地回收目录，不会直接永久删除。\n\n"
            f"选中数量：{count} 条\n\n"
            f"{preview}\n\n"
            f"回收目录：{DELETED_ROOT}\n\n"
            "确定删除吗？",
        )
        if not ok:
            return

        moved_paths = []
        failed = []
        deleted_keys = []
        for item in items:
            source = item.get("source", "Unknown")
            path = pathlib.Path(item["path"])
            if not path.exists():
                failed.append(f"不存在：{path}")
                continue
            try:
                moved_paths.append(move_to_deleted(path, source))
                if source == "Claude":
                    session_dir = path.with_suffix("")
                    if session_dir.exists() and session_dir.is_dir():
                        moved_paths.append(move_to_deleted(session_dir, source))
                deleted_keys.append(session_key(item))
            except OSError as exc:
                failed.append(f"{path}: {exc}")

        notes_changed = False
        for key in deleted_keys:
            if key in self.notes:
                self.notes.pop(key, None)
                notes_changed = True
        if notes_changed:
            try:
                save_notes(self.notes)
            except OSError:
                pass

        self.refresh()
        if failed:
            messagebox.showwarning(
                "部分删除失败",
                f"成功移动 {len(deleted_keys)} 条，失败 {len(failed)} 条。\n\n" + "\n".join(failed[:10]),
            )
        self.status_var.set(f"已移动 {len(deleted_keys)} 条聊天到回收目录：{DELETED_ROOT}")


if __name__ == "__main__":
    App().mainloop()
