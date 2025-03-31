# Copyright 2017-2020 Palantir Technologies, Inc.
# Copyright 2021- Python Language Server Contributors.

import ast
from contextlib import contextmanager

from pyflakes import api as pyflakes_api, messages, checker
from pylsp import hookimpl, lsp

# Pyflakes messages that should be reported as Errors instead of Warns
PYFLAKES_ERROR_MESSAGES = (
    messages.UndefinedName,
    messages.UndefinedExport,
    messages.UndefinedLocal,
    messages.DuplicateArgument,
    messages.FutureFeatureNotDefined,
    messages.ReturnOutsideFunction,
    messages.YieldOutsideFunction,
    messages.ContinueOutsideLoop,
    messages.BreakOutsideLoop,
    messages.TwoStarredExpressions,
)


@hookimpl
def pylsp_lint(document):
    with workspace.report_progress("lint: pyflakes"):
        if end_coordinates_patch.is_needed:
            with end_coordinates_patch.activate_patch():
                reporter = PyflakesDiagnosticReport(document.lines)
        else:
            reporter = PyflakesDiagnosticReport(document.lines)
        pyflakes_api.check(
            document.source.encode("utf-8"), document.path, reporter=reporter
        )
        return reporter.diagnostics


class PyflakesDiagnosticReport:
    def __init__(self, lines):
        self.lines = lines
        self.diagnostics = []

    def unexpectedError(self, _filename, msg):  # pragma: no cover
        err_range = {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        }
        self.diagnostics.append(
            {
                "source": "pyflakes",
                "range": err_range,
                "message": msg,
                "severity": lsp.DiagnosticSeverity.Error,
            }
        )

    def syntaxError(self, _filename, msg, lineno, offset, text):
        # We've seen that lineno and offset can sometimes be None
        lineno = lineno or 1
        offset = offset or 0
        # could be None if the error is due to an invalid encoding
        # see e.g. https://github.com/python-lsp/python-lsp-server/issues/429
        text = text or ""

        err_range = {
            "start": {"line": lineno - 1, "character": offset},
            "end": {"line": lineno - 1, "character": offset + len(text)},
        }
        self.diagnostics.append(
            {
                "source": "pyflakes",
                "range": err_range,
                "message": msg,
                "severity": lsp.DiagnosticSeverity.Error,
            }
        )

    def flake(self, message):
        """Get message like <filename>:<lineno>: <msg>"""
        err_range = {
            "start": {"line": message.lineno - 1, "character": message.col},
            "end": {
                "line": getattr(message, "end_lineno", message.lineno) - 1,
                "character": getattr(
                    message, "end_col_offset", len(self.lines[message.lineno - 1])
                ),
            },
        }

        severity = lsp.DiagnosticSeverity.Warning
        for message_type in PYFLAKES_ERROR_MESSAGES:
            if isinstance(message, message_type):
                severity = lsp.DiagnosticSeverity.Error
                break

        self.diagnostics.append(
            {
                "source": "pyflakes",
                "range": err_range,
                "message": message.message % message.message_args,
                "severity": severity,
            }
        )


class PyflakesEndPositionsPatch:
    def __init__(self, checker_class, message_class):
        self.is_active = False
        self.is_needed = self._check_if_patch_needed(message_class)
        checker_class.handleNode = self._patch_handle_node(checker_class.handleNode)
        message_class.__init__ = self._patch_message_init(message_class.__init__)

    @contextmanager
    def activate_patch(self):
        self.is_active = True
        try:
            yield
        finally:
            self.is_active = False

    def _check_if_patch_needed(self, message_class) -> bool:
        node = ast.parse("1").body[0]
        message = message_class("fielname", node)
        python_version_supports_end_coords = hasattr(node, "end_lineno")
        is_patch_applied_upstream = hasattr(message, "end_lineno")
        return python_version_supports_end_coords and not is_patch_applied_upstream

    def _patch_handle_node(self, original):
        def patched(this, node, parent):
            if (
                self.is_active
                and this.offset
                and getattr(node, "end_lineno", None) is not None
            ):
                node.end_lineno += this.offset[0]
                node.end_col_offset += this.offset[1]
            return original(this, node, parent)

        return patched

    def _patch_message_init(self, original):
        def patched(this, filename, loc):
            original(this, filename, loc)
            if self.is_active:
                this.end_col = getattr(loc, "end_col_offset", None)
                this.end_lineno = getattr(loc, "end_lineno", None)

        return patched


end_coordinates_patch = PyflakesEndPositionsPatch(checker.Checker, messages.Message)
