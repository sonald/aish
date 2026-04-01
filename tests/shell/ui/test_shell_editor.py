from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from prompt_toolkit.formatted_text import to_formatted_text

from aish.shell.ui.editor import HistoryAutoSuggest, ShellPromptController


def test_history_auto_suggest_returns_suffix_for_matching_prefix():
    history_manager = Mock()
    history_manager.search_prefix_sync.return_value = "git status"
    suggest = HistoryAutoSuggest(history_manager)

    suggestion = suggest.get_suggestion(
        buffer=Mock(),
        document=SimpleNamespace(text_before_cursor="git s"),
    )

    assert suggestion is not None
    assert suggestion.text == "tatus"


def test_history_auto_suggest_returns_none_for_blank_prefix():
    history_manager = Mock()
    suggest = HistoryAutoSuggest(history_manager)

    suggestion = suggest.get_suggestion(
        buffer=Mock(),
        document=SimpleNamespace(text_before_cursor="   "),
    )

    assert suggestion is None
    history_manager.search_prefix_sync.assert_not_called()


def test_shell_prompt_controller_loads_recent_history_and_remembers_commands():
    history_manager = Mock()
    history_manager.get_recent_commands_sync.return_value = ["pwd", "ls"]
    controller = ShellPromptController(history_manager=history_manager)

    controller.remember_command("git status")

    assert controller._history.get_strings() == ["pwd", "ls", "git status"]


def test_shell_prompt_controller_uses_cwd_provider_for_prompt_text():
    controller = ShellPromptController(cwd_provider=lambda: "/tmp/project")

    assert controller._get_prompt_text() == "/tmp/project"


def test_shell_prompt_controller_hides_toolbar_without_hint():
    interruption_manager = Mock()
    interruption_manager.get_prompt_message.return_value = None
    controller = ShellPromptController(interruption_manager=interruption_manager)

    assert controller._get_bottom_toolbar() is None


def test_shell_prompt_controller_does_not_attach_bottom_toolbar_to_session():
    controller = ShellPromptController()

    assert getattr(controller._session, "bottom_toolbar", None) is None


def test_shell_prompt_controller_renders_interruption_hint_without_ai_default():
    interruption_manager = Mock()
    interruption_manager.get_prompt_message.return_value = "<gray>Press Ctrl+C to cancel</gray>"
    controller = ShellPromptController(interruption_manager=interruption_manager)

    toolbar = controller._get_bottom_toolbar()

    assert toolbar is not None
    assert "Press Ctrl+C to cancel" == "".join(
        fragment[1] for fragment in to_formatted_text(toolbar)
    )