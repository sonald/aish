"""TUI Application main class."""

import os
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.text import Text

from aish.tui.types import (
    ContentLine,
    ContentLineType,
    Notification,
    PlanQueueState,
    PTYMode,
    SelectionState,
    StatusInfo,
    StepStatus,
    TUIEvent,
    TUIState,
)

if TYPE_CHECKING:
    from aish.config import ConfigModel


class TUIApp:
    """Main TUI application class with status bar at bottom."""

    def __init__(
        self,
        config: "ConfigModel",
        shell: Optional[object] = None,
    ):
        """Initialize TUI application.

        Args:
            config: Configuration model with TUI settings
            shell: Reference to the shell core for integration
        """
        self.config = config
        self.shell = shell
        self.tui_settings = config.tui

        # Console for rendering - use stderr to avoid interfering with stdout
        self.console = Console(file=sys.stderr)

        # TUI state
        self.state = TUIState(
            status=StatusInfo(model=config.model or "default"),
            max_content_lines=self.tui_settings.max_content_lines,
        )

        # Event queue for thread-safe communication
        self._event_queue: queue.Queue = queue.Queue()

        # PTY mode (capture or passthrough)
        self._pty_mode = PTYMode.CAPTURE

        # Control flags
        self._is_running = False
        self._stop_event = threading.Event()

        # Layout components (lazy loaded)
        self._widgets_loaded = False

        # Input handling
        self._input_buffer = ""
        self._input_prompt = "$ "

        # PTY output capture
        self._pty_output_buffer = ""

        # Current hint message
        self._current_hint = "Use ';' to ask AI"

        # Plan queue state
        self._plan_queue_state = PlanQueueState()

        # Selection state for inline UI (ask_user, plan queue)
        from aish.tui.types import SelectionState
        self._selection_state = SelectionState()

    def _ensure_widgets(self) -> None:
        """Lazily load widgets to avoid circular imports."""
        if self._widgets_loaded:
            return

        from aish.tui.widgets import StatusBar

        self.status_bar = StatusBar(self.tui_settings)
        self._widgets_loaded = True

    def _render_status_bar(self, hint: str = "") -> object:
        """Render status bar line.

        Args:
            hint: Optional hint message to display in status bar (right side)

        Returns:
            Renderable content
        """
        self._ensure_widgets()
        self.state.status.time = time.strftime("%H:%M:%S") if self.tui_settings.show_time else ""
        return self.status_bar.render(self.state.status, hint=hint)

    def emit_event(self, event_type: TUIEvent, data: Optional[object] = None) -> None:
        """Emit an event to the TUI event queue (thread-safe).

        Args:
            event_type: Type of event
            data: Optional event data
        """
        self._event_queue.put((event_type, data))

    def update_status(self, **kwargs) -> None:
        """Update status bar information.

        Args:
            **kwargs: StatusInfo fields to update
        """
        for key, value in kwargs.items():
            if hasattr(self.state.status, key):
                setattr(self.state.status, key, value)
        self.emit_event(TUIEvent.STATUS_UPDATE)

    def add_notification(
        self,
        message: str,
        level: str = "info",
        timeout: Optional[float] = None,
    ) -> None:
        """Add a notification message.

        Args:
            message: Notification message
            level: Message level (info, warning, error)
            timeout: Custom timeout (uses config default if None)
        """
        notification = Notification(
            message=message,
            level=level,
            timeout=timeout or self.tui_settings.notification_timeout,
            timestamp=time.time(),
        )
        self.state.add_notification(notification)
        self.emit_event(TUIEvent.NOTIFICATION)

    def add_content(
        self,
        text: str,
        line_type: ContentLineType = ContentLineType.OUTPUT,
    ) -> None:
        """Add content to the content area.

        Args:
            text: Text content to add
            line_type: Type of content line
        """
        # Split multiline text into individual lines
        for line in text.splitlines():
            content_line = ContentLine(
                text=line,
                line_type=line_type,
                timestamp=time.time(),
            )
            self.state.add_content_line(content_line)
        self.emit_event(TUIEvent.CONTENT_APPEND)

    def set_processing(self, is_processing: bool) -> None:
        """Set processing state indicator.

        Args:
            is_processing: Whether the app is processing
        """
        self.state.status.is_processing = is_processing
        self.emit_event(TUIEvent.STATUS_UPDATE)

    def set_mode(self, mode: str) -> None:
        """Set current mode (PTY or AI).

        Args:
            mode: Mode string
        """
        self.state.status.mode = mode
        self.emit_event(TUIEvent.STATUS_UPDATE)

    def set_cwd(self, cwd: str) -> None:
        """Set current working directory display.

        Args:
            cwd: Current working directory
        """
        if self.tui_settings.show_cwd:
            self.state.status.cwd = cwd
            self.emit_event(TUIEvent.STATUS_UPDATE)

    # Plan queue methods
    def show_plan_queue(
        self,
        plan_id: str,
        plan_title: str,
        steps: list[dict],
        current_step: int = 1,
    ) -> None:
        """Show plan queue in TUI.

        Args:
            plan_id: The plan ID
            plan_title: The plan title
            steps: List of step dictionaries with keys: number, title, status
            current_step: Current step number
        """
        self._plan_queue_state.plan_id = plan_id
        self._plan_queue_state.plan_title = plan_title
        self._plan_queue_state.steps = []
        self._plan_queue_state.current_step = current_step
        self._plan_queue_state.total_steps = len(steps)
        self._plan_queue_state.is_visible = True

        for step_data in steps:
            self._plan_queue_state.add_step(
                number=step_data.get("number", 0),
                title=step_data.get("title", ""),
                status=StepStatus(step_data.get("status", "pending")),
            )

        self.emit_event(TUIEvent.PLAN_QUEUE_UPDATE)

    def hide_plan_queue(self) -> None:
        """Hide plan queue."""
        self._plan_queue_state.is_visible = False
        self.emit_event(TUIEvent.PLAN_QUEUE_UPDATE)

    def update_plan_step(self, step_number: int, status: StepStatus) -> None:
        """Update single step status.

        Args:
            step_number: The step number (1-indexed)
            status: New step status
        """
        self._plan_queue_state.update_step_status(step_number, status)

        # Update current step if completing
        if status == StepStatus.COMPLETED:
            self._plan_queue_state.current_step = step_number + 1

        self.emit_event(TUIEvent.PLAN_QUEUE_UPDATE)

    def get_plan_queue_state(self) -> PlanQueueState:
        """Get current plan queue state.

        Returns:
            Current plan queue state
        """
        return self._plan_queue_state

    def get_plan_queue_render(self) -> Text:
        """Get rendered plan queue for display.

        Returns:
            Rich Text object with formatted plan queue
        """
        from aish.tui.widgets import PlanQueueWidget

        widget = PlanQueueWidget(max_visible=5)
        return widget.render(self._plan_queue_state)

    def get_plan_queue_render_compact(self) -> Text:
        """Get compact plan queue for status bar.

        Returns:
            Rich Text object with compact progress display
        """
        from aish.tui.widgets import PlanQueueWidget

        widget = PlanQueueWidget(max_visible=5)
        return widget.render_compact(self._plan_queue_state)

    # Inline selection UI methods (for ask_user and similar)
    def show_selection(
        self,
        prompt: str,
        options: list[dict],
        title: str = "",
        default: str = "",
        allow_cancel: bool = True,
        allow_custom_input: bool = False,
    ) -> None:
        """Show inline selection UI above status bar.

        Args:
            prompt: Question/prompt to display
            options: List of option dicts with 'value' and 'label' keys
            title: Optional title for the selection
            default: Default option value
            allow_cancel: Whether Escape cancels
            allow_custom_input: Whether custom input is allowed
        """
        self._selection_state.is_active = True
        self._selection_state.prompt = prompt
        self._selection_state.options = options
        self._selection_state.title = title
        self._selection_state.allow_cancel = allow_cancel
        self._selection_state.allow_custom_input = allow_custom_input
        self._selection_state.custom_input = ""

        # Set default selection
        if default:
            for i, opt in enumerate(options):
                if opt.get("value") == default:
                    self._selection_state.selected_index = i
                    break
        else:
            self._selection_state.selected_index = 0

        self.emit_event(TUIEvent.SELECTION_UPDATE)

    def hide_selection(self) -> None:
        """Hide inline selection UI."""
        self._selection_state.is_active = False
        self.emit_event(TUIEvent.SELECTION_UPDATE)

    def move_selection(self, delta: int) -> bool:
        """Move selection by delta.

        Args:
            delta: Amount to move (positive = down, negative = up)

        Returns:
            True if selection changed, False otherwise
        """
        if not self._selection_state.is_active:
            return False

        changed = self._selection_state.move_selection(delta)
        if changed:
            self.emit_event(TUIEvent.SELECTION_UPDATE)
        return changed

    def get_selected_value(self) -> str | None:
        """Get the value of the currently selected option.

        Returns:
            Selected value or None
        """
        return self._selection_state.get_selected_value()

    def get_selection_state(self) -> SelectionState:
        """Get current selection state.

        Returns:
            Current selection state
        """
        return self._selection_state

    def get_selection_render(self) -> list[str]:
        """Get rendered selection UI for display.

        Returns:
            List of formatted strings, one per line
        """
        from aish.tui.widgets import InlineSelectionWidget

        widget = InlineSelectionWidget(max_visible=5)
        return widget.render(self._selection_state)

    # PTY Adapter interface
    @property
    def pty_mode(self) -> PTYMode:
        """Get current PTY mode."""
        return self._pty_mode

    def set_pty_mode(self, mode: PTYMode) -> None:
        """Set PTY output handling mode.

        Args:
            mode: CAPTURE for normal commands, PASSTHROUGH for interactive
        """
        self._pty_mode = mode

    def append_pty_output(self, output: str) -> None:
        """Append PTY output to content area (for capture mode).

        Args:
            output: PTY output text
        """
        if self._pty_mode == PTYMode.CAPTURE:
            self.add_content(output, ContentLineType.OUTPUT)

    # Input handling - delegated by shell
    def _show_status_bar(self, hint: str = "") -> None:
        """Update status bar hint.

        The actual display is handled by prompt_toolkit's bottom_toolbar.
        This method just updates the internal state.

        Args:
            hint: Optional hint message to display in status bar (right side)
        """
        self._current_hint = hint

    def set_input_prompt(self, prompt: str) -> None:
        """Set the input prompt.

        Args:
            prompt: Prompt string
        """
        self._input_prompt = prompt

    def _start_live(self) -> None:
        """Start displaying the status bar.

        Status bar is now handled by prompt_toolkit's bottom_toolbar.
        This method is kept for compatibility.
        """
        pass

    def _stop_live(self) -> None:
        """Stop the status bar display.

        Status bar is now handled by prompt_toolkit's bottom_toolbar.
        This method is kept for compatibility.
        """
        pass

    def _update_live(self, hint: str = "") -> None:
        """Update the status bar display.

        Args:
            hint: Optional hint message to display
        """
        self._current_hint = hint

    # Lifecycle
    async def run(self) -> None:
        """Run the TUI application with shell integration.

        The shell runs normally with its output going to the terminal.
        The status bar is shown before each input prompt using ANSI codes.
        """
        self._is_running = True
        self._stop_event.clear()

        # Update initial CWD
        try:
            self.set_cwd(os.getcwd())
        except Exception:
            pass

        try:
            # Run the shell's main loop
            if self.shell is not None:
                # Pass hint to shell for display
                self.shell._tui_hint = "Use ';' to ask AI"
                await self.shell.run()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_event.set()
            self._stop_live()

    def _handle_event(self, event_type: TUIEvent, data: Optional[object]) -> None:
        """Handle TUI events.

        Args:
            event_type: Type of event
            data: Event data
        """
        if event_type == TUIEvent.QUIT:
            self._is_running = False
        elif event_type == TUIEvent.INPUT_SUBMIT:
            # Input submitted - handled by input_bar
            pass
        # Other events just trigger redraw

    def _cleanup_notifications(self) -> None:
        """Remove expired notifications."""
        current_time = time.time()
        self.state.notifications = [
            n for n in self.state.notifications
            if current_time - n.timestamp < n.timeout
        ]

    def stop(self) -> None:
        """Stop the TUI application."""
        self._is_running = False
        self._stop_event.set()
        self._stop_live()
        self.emit_event(TUIEvent.QUIT)
