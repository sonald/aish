from __future__ import annotations

from aish import PTYAIShell, run_shell
from aish.pty import CommandState, PTYManager
from aish.shell import CommandResult, CommandStatus


def test_shell_exports_stable_symbols():
    assert PTYAIShell is not None
    assert callable(run_shell)
    assert PTYManager is not None
    assert CommandState is not None
    assert CommandStatus.SUCCESS.value == "success"

    result = CommandResult(
        status=CommandStatus.SUCCESS, exit_code=0, stdout="", stderr=""
    )
    assert result.to_tuple() == (0, "", "")
