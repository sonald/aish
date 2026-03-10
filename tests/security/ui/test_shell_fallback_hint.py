from __future__ import annotations


from rich.console import Console


def _reset_i18n_cache() -> None:
    # i18n caches locale/messages at import-time; reset for deterministic tests.
    import aish.i18n as i18n

    i18n._UI_LOCALE = None  # type: ignore[attr-defined]
    i18n._MESSAGES = None  # type: ignore[attr-defined]
    i18n._MESSAGES_EN = None  # type: ignore[attr-defined]


def test_security_panel_shows_fallback_hint_for_sandbox_execute_failed(monkeypatch) -> None:
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    _reset_i18n_cache()

    from aish.shell import AIShell

    shell = AIShell.__new__(AIShell)
    shell.console = Console(record=True, width=120)

    data = {
        "tool_name": "bash_exec",
        "panel_mode": "confirm",
        "command": "sudo systemctl restart nginx",
        "security_analysis": {
            "risk_level": "MEDIUM",
            "reasons": [],
            "sandbox": {"enabled": False, "reason": "sandbox_execute_failed"},
        },
    }

    shell._display_security_panel(data, panel_mode="confirm")
    text = shell.console.export_text()

    assert "无法检测命令风险" in text
    assert "需要确认" in text


def test_security_panel_hides_fallback_hint_in_command_fallback_mode(monkeypatch) -> None:
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    _reset_i18n_cache()

    from aish.shell import AIShell

    shell = AIShell.__new__(AIShell)
    shell.console = Console(record=True, width=120)

    data = {
        "tool_name": "bash_exec",
        "panel_mode": "confirm",
        "command": "sudo rm -f /etc/aish/123",
        "security_analysis": {
            "mode": "command_fallback",
            "risk_level": "MEDIUM",
            "reasons": ["已启用命令+路径兜底，命中规则 1 条。"],
            "sandbox": {"enabled": False, "reason": "sandbox_execute_failed"},
        },
    }

    shell._display_security_panel(data, panel_mode="confirm")
    text = shell.console.export_text()

    assert "无法检测命令风险" not in text
    assert "提示" not in text
