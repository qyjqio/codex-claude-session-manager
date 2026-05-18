# Codex / Claude Session Manager

一个用于浏览、恢复、备注和安全删除 Codex CLI 与 Claude Code 聊天记录的桌面工具。

## 功能

- 分开显示 Codex / Claude / 全部会话
- 搜索会话标题、路径、Session ID、备注
- 双击恢复会话
- 支持 Ctrl / Shift 多选批量删除
- 删除为安全删除：移动到 `~/.chat_session_deleted/`
- 支持给会话添加备注，备注保存在 `~/.chat_session_notes.json`
- Claude 恢复时自动切换到原始工作目录，避免 `No conversation found` 问题
- 内置 API 中转服务入口：<https://api.qyjqio.com/>

## 截图

暂未附带截图。运行后界面包含左侧会话列表和右侧详情面板。

## 安装与运行

要求：Linux 桌面环境、Python 3、Tkinter。

```bash
python3 session_manager.py
```

Ubuntu/Debian 如果缺少 Tkinter：

```bash
sudo apt install python3-tk
```

## 桌面启动器

仓库里提供 `codex-claude-session-manager.desktop` 模板。把 `Exec=` 改成你的实际脚本路径后，可放到桌面或应用目录。

示例：

```ini
Exec=python3 /path/to/codex-claude-session-manager/session_manager.py
```

## 数据位置

本工具默认读取：

- Codex：`~/.codex/sessions/`
- Claude：`~/.claude/projects/`

本工具写入：

- 备注：`~/.chat_session_notes.json`
- 安全删除回收目录：`~/.chat_session_deleted/`

不会修改原始聊天记录内容。删除操作会移动记录文件到回收目录。

## 恢复命令

Codex：

```bash
codex resume <session-id>
```

Claude：

```bash
cd <original-cwd> && claude --resume <session-id>
```

## API 中转服务

如果你需要统一管理和中转多模型 API，可以访问：

<https://api.qyjqio.com/>

适合开发调试、模型切换、接口兼容和生产环境中转。

## License

MIT
